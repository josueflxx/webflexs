from __future__ import annotations

import io
import base64
import html
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
import xml.etree.ElementTree as ET

from django.conf import settings
from django.core.management import call_command
from django.test import SimpleTestCase, override_settings

from core.services.arca_client import (
    ArcaConfigurationError,
    ArcaTemporaryError,
    ArcaWsfeClient,
)
from core.services.arca_homologation import (
    ARCAEmissionDisabledError,
    evaluate_homologation_readiness,
)


SHARED_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": "redis://127.0.0.1:6379/15",
    }
}


def _external_fixture_path(filename: str) -> str:
    return str(Path(tempfile.gettempdir()).resolve() / filename)


SAFE_READ_SETTINGS = {
    "ARCA_ENABLED": True,
    "ARCA_ENVIRONMENT": "homologacion",
    "ARCA_HOMOLOGATION_NETWORK_ENABLED": True,
    "ARCA_HOMOLOGATION_READ_ENABLED": True,
    "ARCA_HOMOLOGATION_EMISSION_ENABLED": False,
    "ARCA_PRODUCTION_ENABLED": False,
    "READY_ARCA_HOMOLOGACION_READONLY": True,
    "ARCA_WSASS_AUTHORIZATION_CONFIRMED": True,
    "ARCA_WSAA_URL": "https://wsaahomo.afip.gov.ar/ws/services/LoginCms",
    "ARCA_WSFE_URL": "https://wswhomo.afip.gov.ar/wsfev1/service.asmx",
    "ARCA_WSFE_WSDL": "https://wswhomo.afip.gov.ar/wsfev1/service.asmx?WSDL",
    "ARCA_TLS_VERIFY": True,
    "ARCA_REDACT_SECRETS": True,
    "ARCA_TOKEN_CACHE_ENABLED": True,
    "ARCA_TOKEN_CACHE_BACKEND": "redis",
    "ARCA_TOKEN_CACHE_URL": "redis://127.0.0.1:6379/15",
    "ARCA_TOKEN_CACHE_PREFIX": "webflexs:arca:homo:test",
    "ARCA_TOKEN_CACHE_PATH": "",
    "ARCA_CREDENTIAL_ID": "local-test-only",
    "ARCA_SERVICE_ID": "wsfe",
    "ARCA_CUIT": "30693450239",
    "ARCA_PTO_VTA": "8",
    "ARCA_DEFAULT_CBTE_TIPO": "6",
    "ARCA_CERT_PATH": _external_fixture_path("arca-test-only.crt"),
    "ARCA_PRIVATE_KEY_PATH": _external_fixture_path("arca-test-only.key"),
    "ARCA_PRIVATE_KEY_PASSPHRASE_FILE": "",
    "ARCA_EXPECTED_CERT_SHA256": "",
    "CACHES": SHARED_CACHE,
}


@override_settings(**SAFE_READ_SETTINGS)
class ArcaHomologationGateTests(SimpleTestCase):
    def test_all_explicit_read_only_guards_pass_without_crypto_or_network(self):
        result = evaluate_homologation_readiness(check_credentials=False)
        self.assertTrue(result.passed, result.error_codes)
        self.assertEqual(result.error_codes, ())
        self.assertEqual(result.wsaa_host, "wsaahomo.afip.gov.ar")
        self.assertEqual(result.wsfe_host, "wswhomo.afip.gov.ar")

    def test_every_safety_switch_fails_closed(self):
        unsafe_cases = {
            "disabled_integration": (
                {"ARCA_ENABLED": False},
                "integration_disabled",
            ),
            "missing_user_signal": (
                {"READY_ARCA_HOMOLOGACION_READONLY": False},
                "user_readiness_signal_missing",
            ),
            "missing_wsass_confirmation": (
                {"ARCA_WSASS_AUTHORIZATION_CONFIRMED": False},
                "wsass_authorization_not_confirmed",
            ),
            "network_disabled": (
                {"ARCA_HOMOLOGATION_NETWORK_ENABLED": False},
                "homologation_network_disabled",
            ),
            "read_disabled": (
                {"ARCA_HOMOLOGATION_READ_ENABLED": False},
                "homologation_read_disabled",
            ),
            "emission_enabled": (
                {"ARCA_HOMOLOGATION_EMISSION_ENABLED": True},
                "homologation_emission_must_remain_disabled",
            ),
            "production_enabled": (
                {"ARCA_PRODUCTION_ENABLED": True},
                "production_enabled",
            ),
            "tls_disabled": (
                {"ARCA_TLS_VERIFY": False},
                "tls_verification_disabled",
            ),
            "redaction_disabled": (
                {"ARCA_REDACT_SECRETS": False},
                "secret_redaction_disabled",
            ),
            "cache_disabled": (
                {"ARCA_TOKEN_CACHE_ENABLED": False},
                "ticket_cache_disabled",
            ),
        }
        for label, (settings_override, expected_error) in unsafe_cases.items():
            with self.subTest(label=label), override_settings(**settings_override):
                result = evaluate_homologation_readiness(
                    check_credentials=False
                )
                self.assertFalse(result.passed)
                self.assertIn(expected_error, result.error_codes)

    def test_external_environment_rejects_every_non_exact_value(self):
        for value in (
            "homologation",
            "production",
            "produccion",
            "prod",
            "",
            "other",
            "HOMOLOGACION",
            " homologacion",
            "homologacion ",
            "homologacion-ish",
        ):
            with self.subTest(value=value), override_settings(
                ARCA_ENVIRONMENT=value
            ):
                result = evaluate_homologation_readiness(
                    check_credentials=False
                )
                self.assertFalse(result.passed)

    def test_endpoint_allowlist_has_no_override_or_redirect_fallback(self):
        unsafe_values = (
            "http://wswhomo.afip.gov.ar/wsfev1/service.asmx",
            "https://servicios1.afip.gov.ar/wsfev1/service.asmx",
            "https://wswhomo.afip.gov.ar.evil.invalid/wsfev1/service.asmx",
            "https://user:password@wswhomo.afip.gov.ar/wsfev1/service.asmx",
            "https://wswhomo.afip.gov.ar:444/wsfev1/service.asmx",
            "https://wswhomo.afip.gov.ar/wrong/service.asmx",
            "https://wswhomo.afip.gov.ar/wsfev1/service.asmx?redirect=1",
            "https://wswhomo.afip.gov.ar/wsfev1/service.asmx#fragment",
            "https://WSWHOMO.AFIP.GOV.AR/wsfev1/service.asmx",
            "https://wswhomo%2eafip%2egov%2ear/wsfev1/service.asmx",
            "https://127.0.0.1/wsfev1/service.asmx",
        )
        for value in unsafe_values:
            with self.subTest(value=value), override_settings(
                ARCA_WSFE_URL=value
            ):
                result = evaluate_homologation_readiness(
                    check_credentials=False
                )
                self.assertFalse(result.passed)
                self.assertIn(
                    "wsfe_endpoint_not_allowlisted",
                    result.error_codes,
                )

    def test_ambiguous_boolean_values_are_rejected(self):
        flag_names = (
            "ARCA_ENABLED",
            "ARCA_HOMOLOGATION_NETWORK_ENABLED",
            "ARCA_HOMOLOGATION_READ_ENABLED",
            "ARCA_HOMOLOGATION_EMISSION_ENABLED",
            "ARCA_PRODUCTION_ENABLED",
            "READY_ARCA_HOMOLOGACION_READONLY",
            "ARCA_WSASS_AUTHORIZATION_CONFIRMED",
            "ARCA_TLS_VERIFY",
            "ARCA_REDACT_SECRETS",
            "ARCA_TOKEN_CACHE_ENABLED",
        )
        for flag_name in flag_names:
            for ambiguous in ("1", "yes", "on", " true ", "false "):
                with self.subTest(
                    flag=flag_name,
                    value=ambiguous,
                ), override_settings(**{flag_name: ambiguous}):
                    result = evaluate_homologation_readiness(
                        check_credentials=False
                    )
                    self.assertFalse(result.passed)
                    self.assertIn(
                        f"{flag_name.lower()}_invalid",
                        result.error_codes,
                    )

    def test_file_ticket_cache_and_passphrase_file_are_rejected(self):
        with override_settings(
            ARCA_TOKEN_CACHE_PATH=_external_fixture_path("ticket.json"),
            ARCA_PRIVATE_KEY_PASSPHRASE_FILE=_external_fixture_path(
                "passphrase.txt"
            ),
        ):
            result = evaluate_homologation_readiness(
                check_credentials=False
            )
        self.assertIn("file_ticket_cache_forbidden", result.error_codes)
        self.assertIn("passphrase_file_not_supported", result.error_codes)

    def test_database_ticket_cache_is_rejected(self):
        with override_settings(
            CACHES={
                "default": {
                    "BACKEND": "django.core.cache.backends.db.DatabaseCache",
                    "LOCATION": "arca_tickets",
                }
            }
        ):
            result = evaluate_homologation_readiness(
                check_credentials=False
            )
        self.assertIn("cache_backend_forbidden", result.error_codes)

    def test_all_unsafe_cache_backend_types_are_rejected(self):
        unsafe_backends = (
            "django.core.cache.backends.locmem.LocMemCache",
            "django.core.cache.backends.db.DatabaseCache",
            "django.core.cache.backends.filebased.FileBasedCache",
            "django.core.cache.backends.dummy.DummyCache",
            "custom.UnknownCache",
        )
        for backend in unsafe_backends:
            with self.subTest(backend=backend), override_settings(
                ARCA_TOKEN_CACHE_BACKEND="",
                CACHES={
                    "default": {
                        "BACKEND": backend,
                        "LOCATION": "test-location",
                    }
                },
            ):
                result = evaluate_homologation_readiness(
                    check_credentials=False
                )
                self.assertIn(
                    "cache_backend_forbidden",
                    result.error_codes,
                )

    def test_invalid_cache_backend_setting_and_prefix_are_rejected(self):
        with override_settings(
            ARCA_TOKEN_CACHE_BACKEND="Redis",
            ARCA_TOKEN_CACHE_PREFIX="../unsafe prefix",
        ):
            result = evaluate_homologation_readiness(
                check_credentials=False
            )
        self.assertIn(
            "cache_backend_setting_invalid",
            result.error_codes,
        )
        self.assertIn("cache_prefix_invalid", result.error_codes)

    def test_missing_or_invalid_shared_cache_location_is_rejected(self):
        unsafe_caches = (
            {
                "default": {
                    "BACKEND": "django.core.cache.backends.redis.RedisCache",
                    "LOCATION": "",
                }
            },
            {
                "default": {
                    "BACKEND": "django.core.cache.backends.redis.RedisCache",
                    "LOCATION": "file:///tmp/arca-cache",
                }
            },
            {
                "default": {
                    "BACKEND": "django.core.cache.backends.memcached.PyMemcacheCache",
                    "LOCATION": "C:\\tmp\\arca-cache",
                }
            },
            {
                "default": {
                    "BACKEND": "custom.UnknownCache",
                    "LOCATION": "127.0.0.1:1",
                }
            },
        )
        for cache_settings in unsafe_caches:
            with self.subTest(cache=cache_settings), override_settings(
                CACHES=cache_settings
            ):
                result = evaluate_homologation_readiness(
                    check_credentials=False
                )
                self.assertFalse(result.passed)
                self.assertTrue(
                    {
                        "cache_location_invalid",
                        "cache_backend_forbidden",
                    }
                    & set(result.error_codes)
                )

    def test_emission_is_blocked_before_login_payload_or_dispatch(self):
        client = object.__new__(ArcaWsfeClient)
        client._login = mock.Mock(side_effect=AssertionError("login called"))
        client._build_wsfe_payload = mock.Mock(
            side_effect=AssertionError("payload built")
        )
        dispatched = mock.Mock()

        with self.assertRaises(ARCAEmissionDisabledError) as context:
            client.emit_fiscal_document(
                fiscal_document=object(),
                cbte_number=1,
                mark_dispatched=dispatched,
            )

        self.assertEqual(
            context.exception.error_code,
            "arca_emission_disabled",
        )
        client._login.assert_not_called()
        client._build_wsfe_payload.assert_not_called()
        dispatched.assert_not_called()

    def test_management_gate_reports_only_sanitized_failure_codes(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with override_settings(ARCA_CERT_PATH="", ARCA_PRIVATE_KEY_PATH=""):
            call_command(
                "arca_homologation_gate",
                stdout=stdout,
                stderr=stderr,
            )
        self.assertEqual(
            stdout.getvalue().strip(),
            "ARCA_HOMOLOGATION_READINESS_GATE=FAIL",
        )
        self.assertIn("reason=certificate_path_missing", stderr.getvalue())
        self.assertNotIn(str(settings.BASE_DIR), stderr.getvalue())


class ArcaReadonlyWsfeMockTests(SimpleTestCase):
    def setUp(self):
        self.client = object.__new__(ArcaWsfeClient)
        self.client.environment = "homologation"
        self.client.wsfe_url = "safe-homologation-wsfe"
        self.client.issuer_cuit = "30693450239"
        self.client.point_of_sale = mock.Mock(number="8")
        self.client._login = mock.Mock(
            return_value=("SECRET-TOKEN", "SECRET-SIGN")
        )

    def test_parameter_catalog_uses_allowlisted_method_and_returns_no_auth(self):
        response = (
            "<Envelope><FEParamGetTiposCbteResult><ResultGet>"
            "<CbteTipo><Id>6</Id><Desc>Factura B</Desc>"
            "<FchDesde>20110101</FchDesde></CbteTipo>"
            "</ResultGet></FEParamGetTiposCbteResult></Envelope>"
        )
        self.client._soap_post = mock.Mock(return_value=response)

        result = self.client.fetch_parameter_catalog(
            method="FEParamGetTiposCbte"
        )

        self.assertEqual(result["values"][0]["Id"], "6")
        self.assertEqual(result["values"][0]["Desc"], "Factura B")
        self.assertNotIn("SECRET-TOKEN", repr(result))
        self.assertNotIn("SECRET-SIGN", repr(result))
        call = self.client._soap_post.call_args.kwargs
        self.assertEqual(
            call["soap_action"],
            "http://ar.gov.afip.dif.FEV1/FEParamGetTiposCbte",
        )
        self.assertIn("<Token>SECRET-TOKEN</Token>", call["body_xml"])


class ArcaWsaaOfflineMockTests(SimpleTestCase):
    def test_tra_uses_confirmed_service_and_bounded_utc_window(self):
        client = object.__new__(ArcaWsfeClient)
        client.service_name = "service-confirmed-in-wsass"

        before = datetime.now(timezone.utc)
        root = ET.fromstring(client._build_tra())
        after = datetime.now(timezone.utc)

        self.assertEqual(
            root.findtext("service"),
            "service-confirmed-in-wsass",
        )
        unique_id = int(root.findtext("./header/uniqueId"))
        self.assertGreater(unique_id, 0)
        generation = datetime.fromisoformat(
            root.findtext("./header/generationTime")
        )
        expiration = datetime.fromisoformat(
            root.findtext("./header/expirationTime")
        )
        self.assertLessEqual(generation, after)
        self.assertGreaterEqual(generation, before.replace(microsecond=0) - timedelta(minutes=6))
        self.assertGreater(expiration, after)
        self.assertLessEqual(expiration, after + timedelta(minutes=11))

    def test_cms_signing_uses_der_nodetach_timeout_and_deletes_temporaries(self):
        client = object.__new__(ArcaWsfeClient)
        client.openssl_bin = "openssl"
        client.cert_path = _external_fixture_path("signing-test.crt")
        client.key_path = _external_fixture_path("signing-test.key")
        observed_paths = []

        def fake_run(command, **kwargs):
            output_path = Path(command[command.index("-out") + 1])
            input_path = Path(command[command.index("-in") + 1])
            observed_paths.extend((input_path, output_path))
            self.assertTrue(input_path.exists())
            output_path.write_bytes(b"TEST-CMS")
            self.assertEqual(kwargs["timeout"], 30)
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        with mock.patch(
            "core.services.arca_client.subprocess.run",
            side_effect=fake_run,
        ):
            encoded = client._sign_tra("<loginTicketRequest />")

        self.assertEqual(base64.b64decode(encoded), b"TEST-CMS")
        self.assertTrue(all(not path.exists() for path in observed_paths))

    def test_wsaa_ticket_is_parsed_without_persistable_raw_credentials(self):
        client = object.__new__(ArcaWsfeClient)
        client.wsaa_url = "safe-wsaa"
        client.WSAA_SOAP_ACTION = "loginCms"
        client._build_tra = mock.Mock(return_value="<tra />")
        client._sign_tra = mock.Mock(return_value="TEST-CMS")
        ticket_xml = (
            "<loginTicketResponse><header>"
            "<generationTime>2026-07-30T12:00:00+00:00</generationTime>"
            "<expirationTime>2026-07-31T00:00:00+00:00</expirationTime>"
            "</header><credentials><token>SECRET-TOKEN</token>"
            "<sign>SECRET-SIGN</sign></credentials></loginTicketResponse>"
        )
        response = (
            "<Envelope><loginCmsReturn>"
            + html.escape(ticket_xml)
            + "</loginCmsReturn></Envelope>"
        )
        client._soap_post = mock.Mock(return_value=response)

        ticket = client._request_new_access_ticket()

        self.assertEqual(ticket.token, "SECRET-TOKEN")
        self.assertEqual(ticket.sign, "SECRET-SIGN")
        call = client._soap_post.call_args.kwargs
        self.assertNotIn("SECRET-TOKEN", call["body_xml"])
        self.assertNotIn("SECRET-SIGN", call["body_xml"])

    def test_wsaa_fault_does_not_expose_response_or_secret(self):
        client = object.__new__(ArcaWsfeClient)
        client.wsaa_url = "safe-wsaa"
        client.WSAA_SOAP_ACTION = "loginCms"
        client._build_tra = mock.Mock(return_value="<tra />")
        client._sign_tra = mock.Mock(return_value="TEST-CMS")
        client._soap_post = mock.Mock(
            return_value=(
                "<Envelope><faultstring>"
                "Token=SHOULD-NOT-LEAK service unauthorized"
                "</faultstring></Envelope>"
            )
        )

        with self.assertRaises(ArcaTemporaryError) as context:
            client._request_new_access_ticket()

        self.assertEqual(
            context.exception.error_code,
            "wsaa_empty_response",
        )
        self.assertNotIn("SHOULD-NOT-LEAK", str(context.exception))
        self.assertNotIn("response_xml", context.exception.response_payload)


class ArcaReadonlyWsfeAdditionalMockTests(SimpleTestCase):
    def setUp(self):
        self.client = object.__new__(ArcaWsfeClient)
        self.client.environment = "homologation"
        self.client.wsfe_url = "safe-homologation-wsfe"
        self.client.issuer_cuit = "30693450239"
        self.client.point_of_sale = mock.Mock(number="8")
        self.client._login = mock.Mock(
            return_value=("SECRET-TOKEN", "SECRET-SIGN")
        )

    def test_points_of_sale_are_parsed_as_read_only_parameters(self):
        response = (
            "<Envelope><FEParamGetPtosVentaResult><ResultGet>"
            "<PtoVenta><Nro>8</Nro><EmisionTipo>CAE</EmisionTipo>"
            "<Bloqueado>N</Bloqueado></PtoVenta>"
            "</ResultGet></FEParamGetPtosVentaResult></Envelope>"
        )
        self.client._soap_post = mock.Mock(return_value=response)

        result = self.client.fetch_points_of_sale()

        self.assertEqual(
            result["values"],
            [{"Nro": "8", "EmisionTipo": "CAE", "Bloqueado": "N"}],
        )
        self.assertEqual(result["method"], "FEParamGetPtosVenta")

    def test_non_allowlisted_or_write_method_is_rejected_before_login(self):
        for method in (
            "FECAESolicitar",
            "FECAEASolicitar",
            "FECAEARegInformativo",
            "Unknown",
        ):
            with self.subTest(method=method):
                self.client._login.reset_mock()
                with self.assertRaises(ArcaConfigurationError):
                    self.client.fetch_parameter_catalog(method=method)
                self.client._login.assert_not_called()

    def test_parameter_fault_is_sanitized_and_has_no_raw_response(self):
        self.client._soap_post = mock.Mock(
            return_value=(
                "<Envelope><FEParamGetTiposIvaResult><Errors><Err>"
                "<Code>500</Code><Msg>Token=SHOULD-NOT-LEAK</Msg>"
                "</Err></Errors></FEParamGetTiposIvaResult></Envelope>"
            )
        )
        with self.assertRaises(ArcaTemporaryError) as context:
            self.client.fetch_parameter_catalog(method="FEParamGetTiposIva")
        self.assertNotIn("SHOULD-NOT-LEAK", str(context.exception))
        self.assertNotIn("response_xml", context.exception.response_payload)

    def test_last_authorized_uses_only_confirmed_numeric_voucher_type(self):
        self.client._soap_post = mock.Mock(
            return_value=(
                "<Envelope><FECompUltimoAutorizadoResult>"
                "<CbteNro>42</CbteNro>"
                "</FECompUltimoAutorizadoResult></Envelope>"
            )
        )
        result = self.client.fetch_last_authorized_by_type(cbte_type=6)
        self.assertEqual(result, 42)
        call = self.client._soap_post.call_args.kwargs
        self.assertIn("<CbteTipo>6</CbteTipo>", call["body_xml"])
        self.assertNotIn("SECRET-TOKEN", repr(result))
