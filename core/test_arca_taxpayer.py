from __future__ import annotations

from pathlib import Path
from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from core.services.arca_config import (
    ArcaEndpointKind,
    ArcaEnvironment,
    resolve_arca_endpoint,
)
from core.services.arca_errors import ArcaTemporaryError
from core.services.arca_homologation import (
    evaluate_taxpayer_registry_readiness,
)
from core.services.arca_taxpayer import (
    ArcaTaxpayerRegistryClient,
    TAXPAYER_SERVICE_ID,
    TAXPAYER_SOAP_NAMESPACE,
    parse_taxpayer_response,
)
from core.services.arca_ticket_cache import ArcaTicketCoordinator
from core.services.fiscal_provider import TaxpayerLookupStatus
from core.test_arca_homologation_gate import SAFE_READ_SETTINGS


FIXTURE_DIR = Path(__file__).parent / "test_fixtures" / "arca"
TEST_CUIT = "30693450239"
TAXPAYER_URL = (
    "https://awshomo.arca.gob.ar/"
    "sr-padron/webservices/personaServiceA5"
)
TAXPAYER_SAFE_SETTINGS = {
    **SAFE_READ_SETTINGS,
    "ARCA_TAXPAYER_READ_ENABLED": True,
    "READY_ARCA_TAXPAYER_READONLY": True,
    "ARCA_TAXPAYER_SERVICE_ID": TAXPAYER_SERVICE_ID,
    "ARCA_TAXPAYER_URL": TAXPAYER_URL,
    "ARCA_TAXPAYER_WSDL": f"{TAXPAYER_URL}?WSDL",
}


def _fixture(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


@override_settings(**TAXPAYER_SAFE_SETTINGS)
class ArcaTaxpayerGateTests(SimpleTestCase):
    def test_exact_taxpayer_gate_passes_without_crypto_or_network(self):
        result = evaluate_taxpayer_registry_readiness(
            check_credentials=False,
        )
        self.assertTrue(result.passed, result.error_codes)
        self.assertEqual(result.error_codes, ())
        self.assertTrue(result.service_configured)
        self.assertTrue(result.endpoint_configured)

    def test_taxpayer_gate_is_decoupled_from_wsfe_pos_and_voucher(self):
        with override_settings(
            ARCA_WSFE_URL="",
            ARCA_WSFE_WSDL="",
            ARCA_WSFE_SERVICE_ID="",
            ARCA_SERVICE_ID="",
            ARCA_PTO_VTA="",
            ARCA_DEFAULT_CBTE_TIPO="",
        ):
            result = evaluate_taxpayer_registry_readiness(
                check_credentials=False,
            )
        self.assertTrue(result.passed, result.error_codes)

    def test_taxpayer_gate_requires_exact_service_endpoint_and_wsdl(self):
        unsafe_cases = (
            (
                {"ARCA_TAXPAYER_SERVICE_ID": "ws_sr_padron_a5"},
                "taxpayer_service_id_not_allowlisted",
            ),
            (
                {"ARCA_TAXPAYER_URL": f"{TAXPAYER_URL}?WSDL"},
                "taxpayer_endpoint_not_allowlisted",
            ),
            (
                {"ARCA_TAXPAYER_WSDL": TAXPAYER_URL},
                "taxpayer_wsdl_not_allowlisted",
            ),
            (
                {"ARCA_TAXPAYER_READ_ENABLED": False},
                "taxpayer_read_disabled",
            ),
            (
                {"READY_ARCA_TAXPAYER_READONLY": False},
                "taxpayer_user_signal_missing",
            ),
        )
        for settings_override, expected_error in unsafe_cases:
            with self.subTest(error=expected_error), override_settings(
                **settings_override
            ):
                result = evaluate_taxpayer_registry_readiness(
                    check_credentials=False,
                )
                self.assertFalse(result.passed)
                self.assertIn(expected_error, result.error_codes)

    def test_ambiguous_taxpayer_flags_fail_closed(self):
        for flag in (
            "ARCA_TAXPAYER_READ_ENABLED",
            "READY_ARCA_TAXPAYER_READONLY",
        ):
            with self.subTest(flag=flag), override_settings(**{flag: "yes"}):
                result = evaluate_taxpayer_registry_readiness(
                    check_credentials=False,
                )
            self.assertFalse(result.passed)
            self.assertIn(f"{flag.lower()}_invalid", result.error_codes)

    def test_homologation_allowlist_contains_only_exact_taxpayer_runtime_url(self):
        endpoint = resolve_arca_endpoint(
            ArcaEnvironment.HOMOLOGATION,
            ArcaEndpointKind.TAXPAYER_REGISTRY,
        )
        self.assertEqual(endpoint.url, TAXPAYER_URL)
        self.assertEqual(endpoint.host, "awshomo.arca.gob.ar")
        self.assertNotIn("?", endpoint.url)


class ArcaTaxpayerNormalizerTests(SimpleTestCase):
    def test_found_fixture_preserves_structured_official_sections(self):
        result = parse_taxpayer_response(
            _fixture("taxpayer_found.xml"),
            requested_cuit=TEST_CUIT,
        )
        self.assertEqual(result.status, TaxpayerLookupStatus.FOUND)
        self.assertEqual(result.cuit, TEST_CUIT)
        self.assertEqual(result.display_name, "EMPRESA DE PRUEBA SA")
        self.assertEqual(result.business_name, "EMPRESA DE PRUEBA SA")
        self.assertEqual(result.fiscal_address["codPostal"], "1000")
        self.assertEqual(result.taxes[0]["idImpuesto"], "30")
        self.assertEqual(result.activities[0]["idActividad"], "999999")
        self.assertEqual(
            result.characterizations[0]["fechaSolicitud"],
            "2026-02-11",
        )
        self.assertEqual(result.raw_metadata["schema"], "getPersona_v2")
        self.assertIn("response_sha256", result.raw_metadata)
        self.assertNotIn("Envelope", repr(result))

    def test_not_found_is_a_business_result(self):
        result = parse_taxpayer_response(
            _fixture("taxpayer_not_found.xml"),
            requested_cuit=TEST_CUIT,
        )
        self.assertEqual(result.status, TaxpayerLookupStatus.NOT_FOUND)
        self.assertEqual(result.cuit, TEST_CUIT)
        self.assertFalse(result.is_partial)
        self.assertIn("No existe persona", result.warnings[0])

    def test_inactive_and_partial_sections_coexist(self):
        result = parse_taxpayer_response(
            _fixture("taxpayer_inactive_partial.xml"),
            requested_cuit=TEST_CUIT,
        )
        self.assertEqual(result.status, TaxpayerLookupStatus.INACTIVE)
        self.assertEqual(result.key_status, "INACTIVO")
        self.assertTrue(result.is_partial)
        self.assertEqual(result.display_name, "NOMBRE PRUEBA APELLIDO PRUEBA")
        self.assertIsNotNone(result.monotributo)
        self.assertEqual(len(result.warnings), 1)

    def test_optional_sections_may_be_absent(self):
        response = (
            "<Envelope><personaReturn><datosGenerales>"
            f"<idPersona>{TEST_CUIT}</idPersona>"
            "<tipoPersona>FISICA</tipoPersona><estadoClave>ACTIVO</estadoClave>"
            "<nombre>NOMBRE</nombre><apellido>APELLIDO</apellido>"
            "</datosGenerales></personaReturn></Envelope>"
        )
        result = parse_taxpayer_response(response, requested_cuit=TEST_CUIT)
        self.assertEqual(result.status, TaxpayerLookupStatus.FOUND)
        self.assertEqual(result.taxes, ())
        self.assertEqual(result.activities, ())
        self.assertIsNone(result.monotributo)
        self.assertIsNone(result.general_regime)

    def test_non_not_found_constancia_error_is_preserved_as_partial_warning(self):
        response = (
            "<Envelope><personaReturn>"
            "<errorConstancia><mensaje>Bloqueo de prueba</mensaje></errorConstancia>"
            "<datosGenerales>"
            f"<idPersona>{TEST_CUIT}</idPersona>"
            "<estadoClave>ACTIVO</estadoClave>"
            "</datosGenerales></personaReturn></Envelope>"
        )
        result = parse_taxpayer_response(response, requested_cuit=TEST_CUIT)
        self.assertEqual(result.status, TaxpayerLookupStatus.PARTIAL)
        self.assertTrue(result.is_partial)
        self.assertEqual(result.warnings, ("Bloqueo de prueba",))

    def test_technical_fault_invalid_xml_and_identity_mismatch_raise(self):
        cases = (
            (
                "<Envelope><Fault><faultstring>secret</faultstring></Fault></Envelope>",
                "taxpayer_soap_fault",
            ),
            ("not-xml", "taxpayer_parse_error"),
            (
                "<Envelope><personaReturn><datosGenerales>"
                "<idPersona>30712345678</idPersona>"
                "</datosGenerales></personaReturn></Envelope>",
                "taxpayer_identity_mismatch",
            ),
        )
        for response, expected_code in cases:
            with self.subTest(code=expected_code):
                with self.assertRaises(ArcaTemporaryError) as context:
                    parse_taxpayer_response(
                        response,
                        requested_cuit=TEST_CUIT,
                    )
                self.assertEqual(context.exception.error_code, expected_code)
                self.assertNotIn("secret", str(context.exception))


class ArcaTaxpayerClientOfflineTests(SimpleTestCase):
    def test_get_persona_body_uses_exact_method_and_four_parameters(self):
        body = ArcaTaxpayerRegistryClient._build_get_persona_body(
            token="TOKEN&A",
            sign="SIGN<B",
            represented_cuit=TEST_CUIT,
            taxpayer_cuit=TEST_CUIT,
        )
        self.assertIn(f'xmlns:tns="{TAXPAYER_SOAP_NAMESPACE}"', body)
        self.assertIn("<tns:getPersona_v2", body)
        self.assertIn("<token>TOKEN&amp;A</token>", body)
        self.assertIn("<sign>SIGN&lt;B</sign>", body)
        self.assertIn(f"<cuitRepresentada>{TEST_CUIT}</cuitRepresentada>", body)
        self.assertIn(f"<idPersona>{TEST_CUIT}</idPersona>", body)

    def test_dummy_body_has_no_authentication_fields(self):
        body = ArcaTaxpayerRegistryClient._build_dummy_body()
        self.assertIn("<tns:dummy", body)
        self.assertNotIn("token", body.lower())
        self.assertNotIn("sign", body.lower())

    def test_mocked_lookup_returns_no_token_or_sign(self):
        client = object.__new__(ArcaTaxpayerRegistryClient)
        client.issuer_cuit = TEST_CUIT
        client.taxpayer_url = "safe-taxpayer-url"
        client._login = mock.Mock(return_value=("SECRET-TOKEN", "SECRET-SIGN"))
        client._soap_post = mock.Mock(
            return_value=_fixture("taxpayer_found.xml")
        )

        result = client.lookup_taxpayer(TEST_CUIT)

        self.assertNotIn("SECRET-TOKEN", repr(result))
        self.assertNotIn("SECRET-SIGN", repr(result))
        call = client._soap_post.call_args.kwargs
        self.assertIn("SECRET-TOKEN", call["body_xml"])
        self.assertIn("SECRET-SIGN", call["body_xml"])
        self.assertFalse(call["possibly_sent_on_error"])

    @override_settings(
        ARCA_TOKEN_CACHE_PREFIX="webflexs:arca:homo:multi-service-test",
        CACHES={
            "default": {
                "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                "LOCATION": "arca-taxpayer-multi-service",
            }
        },
    )
    def test_ticket_cache_identity_is_separate_per_business_service(self):
        common = {
            "issuer_cuit": TEST_CUIT,
            "environment": "homologation",
            "credential_fingerprint": "a" * 64,
            "cache_backend": cache,
            "require_shared": False,
        }
        wsfe = ArcaTicketCoordinator(service="wsfe", **common)
        taxpayer = ArcaTicketCoordinator(
            service=TAXPAYER_SERVICE_ID,
            **common,
        )
        self.assertNotEqual(wsfe.ticket_key, taxpayer.ticket_key)
        self.assertNotEqual(wsfe.lock_key, taxpayer.lock_key)

