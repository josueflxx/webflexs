from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from django.conf import settings
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from core.services.arca_client import (
    ArcaConfigurationError,
    ArcaTemporaryError,
    ArcaWsfeClient,
)
from core.services.arca_config import (
    ArcaEndpointKind,
    ArcaEnvironment,
    ArcaSecurityConfigurationError,
    parse_arca_environment,
    require_homologation_environment,
    resolve_arca_endpoint,
    validate_endpoint_url,
)
from core.services.arca_credentials import (
    ArcaCredentialError,
    ArcaCredentialSpec,
    resolve_credential_spec,
    validate_credential_offline,
)
from core.services.arca_homologation import ARCAEmissionDisabledError
from core.services.arca_ticket_cache import (
    ArcaAccessTicket,
    ArcaTicketCacheError,
    ArcaTicketCoordinator,
)
from core.services.arca_transport import (
    ArcaTransportError,
    StrictArcaSoapTransport,
)
from core.services.sensitive_data import (
    REDACTED,
    RedactingFormatter,
    sanitize_sensitive_payload,
    sanitize_sensitive_text,
    sanitize_sentry_event,
)


class ArcaClosedConfigurationTests(SimpleTestCase):
    def test_environment_absent_or_empty_is_disabled(self):
        self.assertIs(parse_arca_environment(None), ArcaEnvironment.DISABLED)
        self.assertIs(parse_arca_environment(""), ArcaEnvironment.DISABLED)

    def test_invalid_environment_is_rejected(self):
        with self.assertRaises(ArcaSecurityConfigurationError):
            parse_arca_environment("homologation-ish")

    @override_settings(ARCA_ENVIRONMENT="production")
    def test_production_is_unconditionally_blocked(self):
        with self.assertRaises(ArcaSecurityConfigurationError) as context:
            require_homologation_environment(point_environment="production")
        self.assertEqual(context.exception.error_code, "production_blocked")

    @override_settings(ARCA_ENVIRONMENT="disabled")
    def test_disabled_mode_rejects_client_io(self):
        with self.assertRaises(ArcaSecurityConfigurationError) as context:
            require_homologation_environment(point_environment="homologation")
        self.assertEqual(context.exception.error_code, "integration_disabled")

    @override_settings(ARCA_ENVIRONMENT="homologacion")
    def test_point_environment_must_match_global_mode(self):
        with self.assertRaises(ArcaSecurityConfigurationError) as context:
            require_homologation_environment(point_environment="production")
        self.assertEqual(context.exception.error_code, "point_environment_mismatch")

    def test_closed_homologation_endpoint_is_valid(self):
        endpoint = resolve_arca_endpoint(
            ArcaEnvironment.HOMOLOGATION,
            ArcaEndpointKind.WSAA,
        )
        self.assertEqual(validate_endpoint_url(endpoint.url, endpoint), endpoint.url)

    def test_malicious_endpoint_variants_are_rejected(self):
        endpoint = resolve_arca_endpoint(
            ArcaEnvironment.HOMOLOGATION,
            ArcaEndpointKind.WSAA,
        )
        invalid_urls = (
            "http://wsaahomo.afip.gov.ar/ws/services/LoginCms",
            "https://wsaahomo.afip.gov.ar.evil.invalid/ws/services/LoginCms",
            "https://wsaahomo.afip.gov.ar@evil.invalid/ws/services/LoginCms",
            "https://user:password@wsaahomo.afip.gov.ar/ws/services/LoginCms",
            "https://wsaahomo.afip.gov.ar:444/ws/services/LoginCms",
            "https://wsaahomo.afip.gov.ar/ws/services/LoginCms/extra",
            "https://wsaahomo.afip.gov.ar/ws/services/LoginCms?next=evil",
            "https://wsaahomo.afip.gov.ar/ws/services/LoginCms#fragment",
            "https://wsaa.afip.gov.ar/ws/services/LoginCms",
        )
        for invalid_url in invalid_urls:
            with self.subTest(url=invalid_url):
                with self.assertRaises(ArcaSecurityConfigurationError):
                    validate_endpoint_url(invalid_url, endpoint)


class _FakeHttpResponse:
    def __init__(self, *, url, body=b"<ok/>", status=200):
        self.url = url
        self.status = status
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self._body


class _FakeOpener:
    def __init__(self, callback):
        self.callback = callback
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request.full_url, timeout))
        return self.callback(request)


class ArcaStrictTransportTests(SimpleTestCase):
    def setUp(self):
        self.endpoint = resolve_arca_endpoint(
            ArcaEnvironment.HOMOLOGATION,
            ArcaEndpointKind.WSFE,
        )

    def test_transport_uses_only_the_validated_endpoint(self):
        opener = _FakeOpener(
            lambda request: _FakeHttpResponse(
                url=request.full_url,
                body=b"<soap/>",
            )
        )
        transport = StrictArcaSoapTransport(opener=opener)
        response = transport.post(
            endpoint=self.endpoint,
            soap_action="action",
            envelope=b"<envelope/>",
            possibly_sent_on_error=False,
        )
        self.assertEqual(response.text, "<soap/>")
        self.assertEqual(opener.calls, [(self.endpoint.url, 30)])

    def test_redirect_is_rejected_without_following_location(self):
        def redirect(_request):
            raise HTTPError(
                self.endpoint.url,
                302,
                "Found",
                {"Location": "https://evil.invalid/"},
                None,
            )

        opener = _FakeOpener(redirect)
        transport = StrictArcaSoapTransport(opener=opener)
        with self.assertRaises(ArcaTransportError) as context:
            transport.post(
                endpoint=self.endpoint,
                soap_action="action",
                envelope=b"<envelope/>",
                possibly_sent_on_error=True,
            )
        self.assertEqual(context.exception.error_code, "redirect_rejected")
        self.assertTrue(context.exception.possibly_sent)
        self.assertEqual(len(opener.calls), 1)

    def test_changed_final_url_is_rejected(self):
        opener = _FakeOpener(
            lambda _request: _FakeHttpResponse(url="https://evil.invalid/")
        )
        transport = StrictArcaSoapTransport(opener=opener)
        with self.assertRaises(ArcaTransportError) as context:
            transport.post(
                endpoint=self.endpoint,
                soap_action="action",
                envelope=b"<envelope/>",
                possibly_sent_on_error=False,
            )
        self.assertEqual(context.exception.error_code, "redirect_rejected")


class ArcaCredentialValidationTests(SimpleTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.cert_path = Path(self.temp_dir.name) / "fixture.crt"
        self.key_path = Path(self.temp_dir.name) / "fixture.key"
        self.cert_path.write_bytes(b"FAKE TEST CERTIFICATE")
        self.key_path.write_bytes(b"FAKE TEST PRIVATE KEY")
        if os.name == "posix":
            self.key_path.chmod(0o600)
        self.public_key = b"-----BEGIN PUBLIC KEY-----\nTEST\n-----END PUBLIC KEY-----\n"
        self.der_certificate = b"FAKE DER TEST CERTIFICATE"

    def tearDown(self):
        self.temp_dir.cleanup()

    def _spec(self, **overrides):
        values = {
            "credential_id": "fixture-homologation",
            "environment": ArcaEnvironment.HOMOLOGATION,
            "issuer_cuit": "30712345678",
            "cert_path": self.cert_path,
            "key_path": self.key_path,
            "expected_fingerprint_sha256": hashlib.sha256(
                self.der_certificate
            ).hexdigest(),
        }
        values.update(overrides)
        return ArcaCredentialSpec(**values)

    def _runner(self, command, **_kwargs):
        if "icacls" in command:
            stdout = (
                b"fixture.key TEST-USER:(F)\n"
                b"Successfully processed 1 files\n"
            )
        elif "x509" in command and "-dates" in command:
            stdout = (
                b"notBefore=Jan 01 00:00:00 2025 GMT\n"
                b"notAfter=Jan 01 00:00:00 2030 GMT\n"
            )
        elif "x509" in command and "-subject" in command:
            stdout = (
                b"subject=serialNumber=CUIT 30712345678,"
                b"CN=fixture-homologation\n"
            )
        elif "x509" in command and "-pubkey" in command:
            stdout = self.public_key
        elif "pkey" in command and "-pubout" in command:
            stdout = self.public_key
        elif "x509" in command and "-outform" in command:
            stdout = self.der_certificate
        else:
            stdout = b""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr=b"")

    def test_valid_fixture_metadata_is_accepted_offline(self):
        metadata = validate_credential_offline(
            self._spec(),
            now=datetime(2026, 1, 1, tzinfo=timezone.utc),
            runner=self._runner,
        )
        self.assertEqual(metadata.environment, ArcaEnvironment.HOMOLOGATION)
        self.assertEqual(
            metadata.fingerprint_sha256,
            hashlib.sha256(self.der_certificate).hexdigest(),
        )
        self.assertTrue(metadata.subject_cuit_matches)

    def test_certificate_subject_must_match_configured_cuit(self):
        def wrong_subject_runner(command, **kwargs):
            result = self._runner(command, **kwargs)
            if "x509" in command and "-subject" in command:
                result.stdout = (
                    b"subject=serialNumber=CUIT 30693450239,CN=wrong\n"
                )
            return result

        with self.assertRaises(ArcaCredentialError) as context:
            validate_credential_offline(
                self._spec(),
                now=datetime(2026, 1, 1, tzinfo=timezone.utc),
                runner=wrong_subject_runner,
            )
        self.assertEqual(
            context.exception.error_code,
            "credential_subject_cuit_mismatch",
        )

    def test_missing_certificate_or_key_is_rejected(self):
        missing = Path(self.temp_dir.name) / "missing.pem"
        for field_name in ("cert_path", "key_path"):
            with self.subTest(field=field_name):
                with self.assertRaises(ArcaCredentialError) as context:
                    validate_credential_offline(
                        self._spec(**{field_name: missing}),
                        now=datetime(2026, 1, 1, tzinfo=timezone.utc),
                        runner=self._runner,
                    )
                self.assertEqual(
                    context.exception.error_code,
                    "credential_file_unavailable",
                )

    def test_empty_credential_file_is_rejected(self):
        empty = Path(self.temp_dir.name) / "empty.pem"
        empty.touch()
        with self.assertRaises(ArcaCredentialError) as context:
            validate_credential_offline(
                self._spec(cert_path=empty),
                now=datetime(2026, 1, 1, tzinfo=timezone.utc),
                runner=self._runner,
            )
        self.assertEqual(
            context.exception.error_code,
            "credential_file_empty",
        )

    def test_non_regular_credential_file_is_rejected(self):
        directory = Path(self.temp_dir.name) / "credential-directory"
        directory.mkdir()
        with self.assertRaises(ArcaCredentialError) as context:
            validate_credential_offline(
                self._spec(cert_path=directory),
                now=datetime(2026, 1, 1, tzinfo=timezone.utc),
                runner=self._runner,
            )
        self.assertEqual(
            context.exception.error_code,
            "credential_file_type_invalid",
        )

    def test_symlink_credential_is_rejected(self):
        linked = Path(self.temp_dir.name) / "linked.crt"

        def assert_rejected():
            with self.assertRaises(ArcaCredentialError) as context:
                validate_credential_offline(
                    self._spec(cert_path=linked),
                    now=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    runner=self._runner,
                )
            self.assertEqual(
                context.exception.error_code,
                "credential_file_type_invalid",
            )

        try:
            linked.symlink_to(self.cert_path)
        except (OSError, NotImplementedError):
            with patch(
                "core.services.arca_credentials."
                "_has_link_or_junction_component",
                return_value=True,
            ):
                assert_rejected()
        else:
            assert_rejected()

    def test_certificate_not_yet_valid_is_rejected(self):
        with self.assertRaises(ArcaCredentialError) as context:
            validate_credential_offline(
                self._spec(),
                now=datetime(2024, 1, 1, tzinfo=timezone.utc),
                runner=self._runner,
            )
        self.assertEqual(
            context.exception.error_code,
            "credential_certificate_not_yet_valid",
        )

    def test_certificate_subject_without_cuit_is_rejected(self):
        def missing_cuit_runner(command, **kwargs):
            result = self._runner(command, **kwargs)
            if "x509" in command and "-subject" in command:
                result.stdout = b"subject=CN=fixture-homologation\n"
            return result

        with self.assertRaises(ArcaCredentialError) as context:
            validate_credential_offline(
                self._spec(),
                now=datetime(2026, 1, 1, tzinfo=timezone.utc),
                runner=missing_cuit_runner,
            )
        self.assertEqual(
            context.exception.error_code,
            "credential_subject_cuit_mismatch",
        )

    def test_certificate_fingerprint_mismatch_is_rejected(self):
        with self.assertRaises(ArcaCredentialError) as context:
            validate_credential_offline(
                self._spec(expected_fingerprint_sha256="0" * 64),
                now=datetime(2026, 1, 1, tzinfo=timezone.utc),
                runner=self._runner,
            )
        self.assertEqual(
            context.exception.error_code,
            "credential_fingerprint_mismatch",
        )

    def test_windows_broad_private_key_acl_is_rejected(self):
        if os.name != "nt":
            self.skipTest("Windows ACL validation")

        def broad_acl_runner(command, **kwargs):
            result = self._runner(command, **kwargs)
            if "icacls" in command:
                result.stdout = b"fixture.key Everyone:(R)\n"
            return result

        with self.assertRaises(ArcaCredentialError) as context:
            validate_credential_offline(
                self._spec(),
                now=datetime(2026, 1, 1, tzinfo=timezone.utc),
                runner=broad_acl_runner,
            )
        self.assertEqual(
            context.exception.error_code,
            "credential_key_permissions",
        )

    def test_certificate_and_key_mismatch_is_rejected(self):
        def mismatch_runner(command, **kwargs):
            result = self._runner(command, **kwargs)
            if "pkey" in command:
                result.stdout = b"DIFFERENT PUBLIC KEY"
            return result

        with self.assertRaises(ArcaCredentialError) as context:
            validate_credential_offline(
                self._spec(),
                now=datetime(2026, 1, 1, tzinfo=timezone.utc),
                runner=mismatch_runner,
            )
        self.assertEqual(context.exception.error_code, "credential_key_mismatch")

    def test_expired_certificate_is_rejected_without_content_in_error(self):
        with self.assertRaises(ArcaCredentialError) as context:
            validate_credential_offline(
                self._spec(),
                now=datetime(2031, 1, 1, tzinfo=timezone.utc),
                runner=self._runner,
            )
        message = str(context.exception)
        self.assertEqual(
            context.exception.error_code,
            "credential_certificate_expired",
        )
        self.assertNotIn("FAKE TEST", message)
        self.assertNotIn(str(self.key_path), message)

    def test_production_labeled_credential_is_rejected_before_file_use(self):
        with self.assertRaises(ArcaCredentialError) as context:
            validate_credential_offline(
                self._spec(environment=ArcaEnvironment.PRODUCTION),
                runner=self._runner,
            )
        self.assertEqual(
            context.exception.error_code,
            "credential_environment_blocked",
        )

    def test_repository_paths_are_rejected(self):
        with tempfile.TemporaryDirectory(dir=settings.BASE_DIR) as directory:
            cert = Path(directory) / "fixture.crt"
            key = Path(directory) / "fixture.key"
            cert.write_bytes(b"fixture")
            key.write_bytes(b"fixture")
            if os.name == "posix":
                key.chmod(0o600)
            with self.assertRaises(ArcaCredentialError) as context:
                validate_credential_offline(
                    self._spec(cert_path=cert, key_path=key),
                    runner=self._runner,
                )
        self.assertEqual(context.exception.error_code, "credential_path_forbidden")

    def test_resolution_requires_explicit_matching_environment_label(self):
        company = SimpleNamespace(slug="fixture", id=7, cuit="30712345678")
        config = {
            "fixture": {
                "homologation": {
                    "credential_id": "fixture",
                    "environment": "production",
                    "cuit": "30712345678",
                    "cert_path": str(self.cert_path),
                    "key_path": str(self.key_path),
                }
            }
        }
        with self.assertRaises(ArcaCredentialError) as context:
            resolve_credential_spec(
                company=company,
                environment=ArcaEnvironment.HOMOLOGATION,
                config=config,
            )
        self.assertEqual(
            context.exception.error_code,
            "credential_environment_mismatch",
        )


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "arca-security-tests",
        }
    }
)
class ArcaTicketSingleflightTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def _coordinator(self, **overrides):
        values = {
            "issuer_cuit": "30712345678",
            "environment": "homologation",
            "service": "wsfe",
            "credential_fingerprint": "a" * 64,
            "cache_backend": cache,
            "require_shared": False,
            "wait_seconds": 2,
        }
        values.update(overrides)
        return ArcaTicketCoordinator(**values)

    def test_concurrent_callers_perform_one_ticket_renewal(self):
        coordinator = self._coordinator()
        count = 0
        count_lock = threading.Lock()

        def loader():
            nonlocal count
            with count_lock:
                count += 1
            time.sleep(0.05)
            now = datetime.now(timezone.utc)
            return ArcaAccessTicket(
                token="test-token",
                sign="test-sign",
                generation_time=now - timedelta(seconds=1),
                expiration_time=now + timedelta(minutes=10),
            )

        with ThreadPoolExecutor(max_workers=12) as executor:
            results = list(executor.map(lambda _index: coordinator.get_or_create(loader), range(24)))
        self.assertEqual(count, 1)
        self.assertEqual({result.token for result in results}, {"test-token"})

    def test_two_coordinator_instances_share_one_renewal(self):
        first = self._coordinator()
        second = self._coordinator()
        count = 0
        count_lock = threading.Lock()

        def loader():
            nonlocal count
            with count_lock:
                count += 1
            time.sleep(0.05)
            now = datetime.now(timezone.utc)
            return ArcaAccessTicket(
                token="temporary-token",
                sign="temporary-sign",
                generation_time=now - timedelta(seconds=1),
                expiration_time=now + timedelta(minutes=10),
            )

        coordinators = [first, second] * 8
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(
                executor.map(
                    lambda coordinator: coordinator.get_or_create(loader),
                    coordinators,
                )
            )
        self.assertEqual(count, 1)
        self.assertEqual(
            {result.token for result in results},
            {"temporary-token"},
        )

    def test_fresh_ticket_is_written_and_reused(self):
        coordinator = self._coordinator()
        calls = 0

        def loader():
            nonlocal calls
            calls += 1
            now = datetime.now(timezone.utc)
            return ArcaAccessTicket(
                token="temporary-token",
                sign="temporary-sign",
                generation_time=now - timedelta(seconds=1),
                expiration_time=now + timedelta(minutes=10),
            )

        first = coordinator.get_or_create(loader)
        second = coordinator.get_or_create(loader)
        self.assertEqual(calls, 1)
        self.assertEqual(first, second)

    def test_expiring_ticket_is_replaced(self):
        coordinator = self._coordinator()
        now = datetime.now(timezone.utc)
        cache.set(
            coordinator.ticket_key,
            {
                "token": "expiring-token",
                "sign": "expiring-sign",
                "generation_time": (now - timedelta(minutes=5)).isoformat(),
                "expiration_time": (now + timedelta(seconds=30)).isoformat(),
            },
        )
        calls = 0

        def loader():
            nonlocal calls
            calls += 1
            return ArcaAccessTicket(
                token="renewed-token",
                sign="renewed-sign",
                generation_time=now - timedelta(seconds=1),
                expiration_time=now + timedelta(minutes=10),
            )

        result = coordinator.get_or_create(loader)
        self.assertEqual(result.token, "renewed-token")
        self.assertEqual(calls, 1)

    def test_cache_read_failure_is_fail_closed(self):
        class UnavailableCache:
            def get(self, _key):
                raise ConnectionError("test cache unavailable")

        coordinator = self._coordinator(cache_backend=UnavailableCache())
        with self.assertRaises(ArcaTicketCacheError) as context:
            coordinator.get_valid_ticket()
        self.assertEqual(
            context.exception.error_code,
            "wsaa_cache_unavailable",
        )
        self.assertNotIn("temporary-token", str(context.exception))

    def test_cache_write_failure_is_fail_closed(self):
        class WriteFailureCache:
            def get(self, _key):
                return None

            def add(self, _key, _value, timeout):
                del timeout
                return True

            def set(self, _key, _value, timeout):
                del timeout
                raise ConnectionError("test cache write failure")

        coordinator = self._coordinator(cache_backend=WriteFailureCache())
        now = datetime.now(timezone.utc)
        with self.assertRaises(ArcaTicketCacheError) as context:
            coordinator.get_or_create(
                lambda: ArcaAccessTicket(
                    token="temporary-token",
                    sign="temporary-sign",
                    generation_time=now - timedelta(seconds=1),
                    expiration_time=now + timedelta(minutes=10),
                )
            )
        self.assertEqual(
            context.exception.error_code,
            "wsaa_cache_write_failed",
        )
        self.assertNotIn("temporary-token", str(context.exception))

    def test_cache_lock_failure_is_fail_closed(self):
        class LockFailureCache:
            def get(self, _key):
                return None

            def add(self, _key, _value, timeout):
                del timeout
                raise ConnectionError("test cache lock failure")

        coordinator = self._coordinator(cache_backend=LockFailureCache())
        with self.assertRaises(ArcaTicketCacheError) as context:
            coordinator.get_or_create(lambda: None)
        self.assertEqual(
            context.exception.error_code,
            "wsaa_lock_unavailable",
        )

    def test_exact_ticket_entry_is_deleted(self):
        coordinator = self._coordinator()
        cache.set(
            coordinator.ticket_key,
            {
                "token": "temporary-token",
                "sign": "temporary-sign",
            },
        )
        coordinator.clear_ticket()
        self.assertIsNone(cache.get(coordinator.ticket_key))

    def test_cache_delete_failure_is_fail_closed(self):
        class DeleteFailureCache:
            def delete(self, _key):
                raise ConnectionError("test cache delete failure")

        coordinator = self._coordinator(
            cache_backend=DeleteFailureCache(),
        )
        with self.assertRaises(ArcaTicketCacheError) as context:
            coordinator.clear_ticket()
        self.assertEqual(
            context.exception.error_code,
            "wsaa_cache_delete_failed",
        )

    def test_cache_identity_changes_with_credential_fingerprint(self):
        first = self._coordinator(credential_fingerprint="a" * 64)
        second = self._coordinator(credential_fingerprint="b" * 64)
        self.assertNotEqual(first.ticket_key, second.ticket_key)

    def test_invalid_cached_dates_are_not_reused(self):
        coordinator = self._coordinator()
        cache.set(
            coordinator.ticket_key,
            {
                "token": "old-token",
                "sign": "old-sign",
                "generation_time": "invalid",
                "expiration_time": "invalid",
            },
        )
        calls = 0

        def loader():
            nonlocal calls
            calls += 1
            now = datetime.now(timezone.utc)
            return ArcaAccessTicket(
                token="new-token",
                sign="new-sign",
                generation_time=now - timedelta(seconds=1),
                expiration_time=now + timedelta(minutes=10),
            )

        result = coordinator.get_or_create(loader)
        self.assertEqual(result.token, "new-token")
        self.assertEqual(calls, 1)

    def test_locmem_is_rejected_when_shared_cache_is_required(self):
        with self.assertRaises(ArcaTicketCacheError) as context:
            self._coordinator(require_shared=True)
        self.assertEqual(
            context.exception.error_code,
            "wsaa_shared_cache_required",
        )


@override_settings(
    ARCA_PTO_VTA="7",
    ARCA_DEFAULT_CBTE_TIPO="6",
)
class ArcaReadonlyPreflightSequenceTests(SimpleTestCase):
    def _client(self):
        client = ArcaWsfeClient.__new__(ArcaWsfeClient)
        client.company = SimpleNamespace(id=1)
        client.point_of_sale = SimpleNamespace(number=7)
        client.environment = "homologation"
        client._login = MagicMock(
            return_value=("temporary-token", "temporary-sign")
        )
        client.fetch_service_status = MagicMock(
            return_value={"ok": True}
        )
        client.fetch_readonly_catalogs = MagicMock(
            return_value={
                "points_of_sale": {"values": [{"Nro": "7"}]},
                "voucher_types": {"values": [{"Id": "6"}]},
            }
        )
        client.fetch_last_authorized_by_type = MagicMock(
            return_value=0
        )
        return client

    def test_ticket_precedes_dummy_and_last_query(self):
        client = self._client()
        calls = []
        client._login.side_effect = lambda: (
            calls.append("wsaa") or ("temporary-token", "temporary-sign")
        )
        client.fetch_service_status.side_effect = lambda: (
            calls.append("dummy") or {"ok": True}
        )
        client.fetch_readonly_catalogs.side_effect = lambda: (
            calls.append("catalogs")
            or {
                "points_of_sale": {"values": [{"Nro": "7"}]},
                "voucher_types": {"values": [{"Id": "6"}]},
            }
        )
        client.fetch_last_authorized_by_type.side_effect = (
            lambda **_kwargs: calls.append("last_authorized") or 0
        )
        result = client.run_preflight()
        self.assertTrue(result["ok"])
        self.assertEqual(
            calls,
            ["wsaa", "dummy", "catalogs", "last_authorized"],
        )

    def test_failed_dummy_stops_before_catalogs(self):
        client = self._client()
        client.fetch_service_status.return_value = {"ok": False}
        with self.assertRaises(ArcaTemporaryError):
            client.run_preflight()
        client.fetch_readonly_catalogs.assert_not_called()
        client.fetch_last_authorized_by_type.assert_not_called()

    def test_unconfirmed_point_stops_before_last_authorized(self):
        client = self._client()
        client.fetch_readonly_catalogs.return_value = {
            "points_of_sale": {"values": [{"Nro": "8"}]},
            "voucher_types": {"values": [{"Id": "6"}]},
        }
        with self.assertRaises(ArcaConfigurationError):
            client.run_preflight()
        client.fetch_last_authorized_by_type.assert_not_called()

    def test_unconfirmed_voucher_type_stops_before_last_authorized(self):
        client = self._client()
        client.fetch_readonly_catalogs.return_value = {
            "points_of_sale": {"values": [{"Nro": "7"}]},
            "voucher_types": {"values": [{"Id": "11"}]},
        }
        with self.assertRaises(ArcaConfigurationError):
            client.run_preflight()
        client.fetch_last_authorized_by_type.assert_not_called()


class _Items:
    def __init__(self, values):
        self.values = values

    def all(self):
        return list(self.values)


class ArcaFiscalPayloadTests(SimpleTestCase):
    def setUp(self):
        self.client = object.__new__(ArcaWsfeClient)
        self.client.issuer_cuit = "30712345678"
        self.client.point_of_sale = SimpleNamespace(number=1)

    def _document(self, *, items, net, tax, total):
        return SimpleNamespace(
            items=_Items(items),
            request_payload={
                "snapshot": {
                    "client": {
                        "document_type": "cuit",
                        "document_number": "30712345678",
                        "iva_condition": {"arca_id": 1},
                    }
                }
            },
            receiver_iva_condition_id_snapshot=1,
            doc_type="FA",
            total=Decimal(total),
            tax_total=Decimal(tax),
            subtotal_net=Decimal(net),
            exchange_rate=Decimal("1"),
            currency="ARS",
            issued_at=datetime(2026, 2, 3, 15, 0, tzinfo=timezone.utc),
            related_document_id=None,
        )

    def test_zero_rate_is_emitted_with_official_id_three(self):
        document = self._document(
            items=[
                SimpleNamespace(
                    iva_rate=Decimal("0"),
                    net_amount=Decimal("100"),
                    iva_amount=Decimal("0"),
                )
            ],
            net="100",
            tax="0",
            total="100",
        )
        payload = self.client._build_wsfe_payload(
            fiscal_document=document,
            cbte_number=1,
            token="test-token",
            sign="test-sign",
        )
        self.assertEqual(payload["detalle"]["iva"], [{"id": 3, "base": Decimal("100.00"), "tax": Decimal("0.00")}])
        self.assertEqual(payload["detalle"]["condicion_iva_receptor_id"], 1)
        self.assertEqual(payload["detalle"]["cbte_fch"], "20260203")

    def test_missing_frozen_fiscal_date_is_rejected(self):
        document = self._document(
            items=[
                SimpleNamespace(
                    iva_rate=Decimal("0"),
                    net_amount=Decimal("100"),
                    iva_amount=Decimal("0"),
                )
            ],
            net="100",
            tax="0",
            total="100",
        )
        document.issued_at = None
        with self.assertRaises(ArcaConfigurationError):
            self.client._build_wsfe_payload(
                fiscal_document=document,
                cbte_number=1,
                token="test-token",
                sign="test-sign",
            )

    def test_mixed_supported_rates_reconcile_deterministically(self):
        document = self._document(
            items=[
                SimpleNamespace(iva_rate=Decimal("10.5"), net_amount=Decimal("100"), iva_amount=Decimal("10.50")),
                SimpleNamespace(iva_rate=Decimal("21"), net_amount=Decimal("100"), iva_amount=Decimal("21.00")),
            ],
            net="200",
            tax="31.50",
            total="231.50",
        )
        breakdown = self.client._build_tax_breakdown(document)
        self.assertEqual([row["id"] for row in breakdown], [4, 5])

    def test_unknown_rate_is_rejected_instead_of_falling_back_to_21(self):
        document = self._document(
            items=[
                SimpleNamespace(
                    iva_rate=Decimal("7.5"),
                    net_amount=Decimal("100"),
                    iva_amount=Decimal("7.50"),
                )
            ],
            net="100",
            tax="7.50",
            total="107.50",
        )
        with self.assertRaises(ArcaConfigurationError):
            self.client._build_wsfe_payload(
                fiscal_document=document,
                cbte_number=1,
                token="test-token",
                sign="test-sign",
            )

    def test_tax_total_mismatch_is_rejected(self):
        document = self._document(
            items=[
                SimpleNamespace(
                    iva_rate=Decimal("21"),
                    net_amount=Decimal("100"),
                    iva_amount=Decimal("21"),
                )
            ],
            net="100",
            tax="20",
            total="120",
        )
        with self.assertRaises(ArcaConfigurationError):
            self.client._build_wsfe_payload(
                fiscal_document=document,
                cbte_number=1,
                token="test-token",
                sign="test-sign",
            )

    def test_receiver_vat_condition_is_required_from_snapshot(self):
        document = self._document(
            items=[
                SimpleNamespace(
                    iva_rate=Decimal("0"),
                    net_amount=Decimal("100"),
                    iva_amount=Decimal("0"),
                )
            ],
            net="100",
            tax="0",
            total="100",
        )
        document.receiver_iva_condition_id_snapshot = None
        document.request_payload["snapshot"]["client"]["iva_condition"] = {}
        with self.assertRaises(ArcaConfigurationError):
            self.client._build_wsfe_payload(
                fiscal_document=document,
                cbte_number=1,
                token="test-token",
                sign="test-sign",
            )


class ArcaResponseParserTests(SimpleTestCase):
    def setUp(self):
        self.client = object.__new__(ArcaWsfeClient)

    @staticmethod
    def _emission_xml(*, result="A", cae="12345678901234", observations="", events="", errors=""):
        return (
            "<Envelope><Body><FECAESolicitarResponse><FECAESolicitarResult>"
            "<FeDetResp><FECAEDetResponse>"
            f"<Resultado>{result}</Resultado><CAE>{cae}</CAE><CAEFchVto>20300101</CAEFchVto>"
            f"<Observaciones>{observations}</Observaciones>"
            "</FECAEDetResponse></FeDetResp>"
            f"<Errors>{errors}</Errors><Events>{events}</Events>"
            "</FECAESolicitarResult></FECAESolicitarResponse></Body></Envelope>"
        )

    def test_authorized_with_observations_is_distinct(self):
        xml = self._emission_xml(
            observations="<Obs><Code>100</Code><Msg>Test observation</Msg></Obs>",
            events="<Evt><Code>200</Code><Msg>Test event</Msg></Evt>",
        )
        result = self.client._parse_fe_cae_response(
            response_xml=xml,
            request_payload={"safe": True},
        )
        self.assertEqual(result.state, "authorized_with_observations")
        self.assertEqual(result.observations[0]["code"], "100")
        self.assertEqual(result.events[0]["code"], "200")
        self.assertEqual(result.response_payload["errors"], [])

    def test_rejection_keeps_errors_separate_from_observations(self):
        xml = self._emission_xml(
            result="R",
            cae="",
            observations="<Obs><Code>10</Code><Msg>Observation</Msg></Obs>",
            errors="<Err><Code>20</Code><Msg>Error</Msg></Err>",
        )
        result = self.client._parse_fe_cae_response(
            response_xml=xml,
            request_payload={},
        )
        self.assertEqual(result.state, "rejected")
        self.assertEqual(result.error_code, "20")
        self.assertEqual(len(result.response_payload["observations"]), 1)

    def test_corrupt_or_fault_response_is_uncertain(self):
        corrupt = self.client._parse_fe_cae_response(
            response_xml="<broken",
            request_payload={},
        )
        fault = self.client._parse_fe_cae_response(
            response_xml="<Envelope><Body><Fault><faultstring>fault</faultstring></Fault></Body></Envelope>",
            request_payload={},
        )
        self.assertEqual(corrupt.state, "uncertain")
        self.assertEqual(fault.state, "uncertain")
        self.assertNotIn("raw", corrupt.response_payload)

    def test_consult_authorized_and_not_found_are_normalized(self):
        authorized_xml = (
            "<Envelope><FECompConsultarResult><ResultGet>"
            "<Resultado>A</Resultado><CodAutorizacion>12345678901234</CodAutorizacion>"
            "<FchVto>20300101</FchVto>"
            "</ResultGet><Observaciones><Obs><Code>1</Code><Msg>Obs</Msg></Obs></Observaciones>"
            "</FECompConsultarResult></Envelope>"
        )
        authorized = self.client._parse_consult_response(
            response_xml=authorized_xml,
            request_payload={"cbte_number": 1},
        )
        not_found = self.client._parse_consult_response(
            response_xml="<Envelope><FECompConsultarResult /></Envelope>",
            request_payload={"cbte_number": 2},
        )
        self.assertEqual(authorized.state, "authorized")
        self.assertEqual(authorized.cae, "12345678901234")
        self.assertEqual(not_found.state, "not_found")

    def test_emission_is_blocked_before_authorization_start(self):
        client = object.__new__(ArcaWsfeClient)
        with self.assertRaises(ARCAEmissionDisabledError) as context:
            client.emit_fiscal_document(
                fiscal_document=SimpleNamespace(doc_type="FA"),
                cbte_number=1,
            )
        self.assertEqual(
            context.exception.error_code,
            "arca_emission_disabled",
        )


class ArcaTraTests(SimpleTestCase):
    def test_unique_id_does_not_collide_for_same_second_requests(self):
        client = object.__new__(ArcaWsfeClient)
        client.service_name = "wsfe"
        values = set()
        for _index in range(500):
            xml = client._build_tra()
            unique_id = xml.split("<uniqueId>", 1)[1].split("</uniqueId>", 1)[0]
            values.add(unique_id)
        self.assertEqual(len(values), 500)
        self.assertTrue(all(0 < int(value) < (1 << 63) for value in values))


class ArcaQueryMethodTests(SimpleTestCase):
    def setUp(self):
        self.client = object.__new__(ArcaWsfeClient)
        self.client.environment = "homologation"
        self.client.wsfe_url = "safe-wsfe"
        self.client.issuer_cuit = "30712345678"
        self.client.point_of_sale = SimpleNamespace(number=1)
        self.client._login = lambda: ("test-token", "test-sign")

    def test_fedummy_parses_three_servers(self):
        self.client._soap_post = lambda **_kwargs: (
            "<Envelope><FEDummyResult><AppServer>OK</AppServer>"
            "<DbServer>OK</DbServer><AuthServer>OK</AuthServer>"
            "</FEDummyResult></Envelope>"
        )
        result = self.client.fetch_service_status()
        self.assertTrue(result["ok"])

    def test_receiver_vat_condition_catalog_is_parsed(self):
        self.client._soap_post = lambda **_kwargs: (
            "<Envelope><FEParamGetCondicionIvaReceptorResult><ResultGet>"
            "<CondicionIvaReceptor><Id>1</Id><Desc>Responsable Inscripto</Desc>"
            "<Cmp_Clase>A</Cmp_Clase></CondicionIvaReceptor>"
            "<CondicionIvaReceptor><Id>5</Id><Desc>Consumidor Final</Desc>"
            "<Cmp_Clase>B</Cmp_Clase></CondicionIvaReceptor>"
            "</ResultGet></FEParamGetCondicionIvaReceptorResult></Envelope>"
        )
        result = self.client.fetch_receiver_vat_conditions()
        self.assertEqual([row["id"] for row in result["values"]], [1, 5])

    def test_consult_method_never_puts_auth_in_persistable_request(self):
        self.client._soap_post = lambda **_kwargs: (
            "<Envelope><FECompConsultarResult /></Envelope>"
        )
        result = self.client.consult_fiscal_document(
            doc_type="FA",
            cbte_number=1,
        )
        self.assertEqual(result.state, "not_found")
        self.assertNotIn("token", str(result.request_payload).lower())
        self.assertNotIn("sign", str(result.request_payload).lower())


class ArcaSensitiveLoggingTests(SimpleTestCase):
    def test_extended_secret_forms_are_redacted(self):
        secret = "DO-NOT-LEAK"
        value = (
            f'<ns:Token kind="test">{secret}</ns:Token> '
            f'{{"password": "{secret}", "cert_path": "{secret}"}} '
            f"-----BEGIN CERTIFICATE-----{secret}-----END CERTIFICATE-----"
        )
        sanitized = sanitize_sensitive_text(value)
        self.assertNotIn(secret, sanitized)
        self.assertIn(REDACTED, sanitized)

    def test_binary_payloads_are_never_returned(self):
        sanitized = sanitize_sensitive_payload({"private_key_bytes": b"secret"})
        self.assertEqual(sanitized["private_key_bytes"], REDACTED)

    def test_formatter_redacts_exception_traceback(self):
        formatter = RedactingFormatter("%(levelname)s %(message)s")
        try:
            raise RuntimeError("<Token>DO-NOT-LEAK</Token>")
        except RuntimeError:
            record = logging.LogRecord(
                name="test",
                level=logging.ERROR,
                pathname=__file__,
                lineno=1,
                msg="failed",
                args=(),
                exc_info=__import__("sys").exc_info(),
            )
        output = formatter.format(record)
        self.assertNotIn("DO-NOT-LEAK", output)

    def test_sentry_hook_returns_sanitized_copy(self):
        event = {
            "extra": {"token": "DO-NOT-LEAK"},
            "request": {"data": {"raw": "<Sign>DO-NOT-LEAK</Sign>"}},
        }
        sanitized = sanitize_sentry_event(event)
        self.assertNotIn("DO-NOT-LEAK", str(sanitized))
        self.assertEqual(event["extra"]["token"], "DO-NOT-LEAK")
