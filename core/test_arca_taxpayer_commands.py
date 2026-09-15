from __future__ import annotations

from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from core.management.commands.arca_taxpayer_readonly_probe import (
    Command as TaxpayerProbeCommand,
)
from core.services.fiscal_provider import TaxpayerLookupStatus


TEST_CUIT = "30693450239"


def _network_forbidden(*_args, **_kwargs):
    raise AssertionError("network access is forbidden in offline tests")


class ArcaTaxpayerOfflineCommandTests(SimpleTestCase):
    def _network_guards(self):
        return (
            patch("socket.getaddrinfo", side_effect=_network_forbidden),
            patch("socket.create_connection", side_effect=_network_forbidden),
            patch(
                "urllib.request.OpenerDirector.open",
                side_effect=_network_forbidden,
            ),
            patch(
                "core.services.arca_transport.StrictArcaSoapTransport.post",
                side_effect=_network_forbidden,
            ),
        )

    def test_taxpayer_gate_is_offline_and_fails_closed_by_default(self):
        stdout = StringIO()
        stderr = StringIO()
        guards = self._network_guards()
        with guards[0], guards[1], guards[2], guards[3]:
            call_command(
                "arca_taxpayer_homologation_gate",
                stdout=stdout,
                stderr=stderr,
            )
        self.assertIn(
            "ARCA_TAXPAYER_READINESS_GATE=FAIL",
            stdout.getvalue(),
        )
        self.assertIn("reason=integration_disabled", stderr.getvalue())

    def test_probe_parser_rejects_endpoint_service_and_wsdl_overrides(self):
        parser = TaxpayerProbeCommand().create_parser(
            "manage.py",
            "arca_taxpayer_readonly_probe",
        )
        for argument in (
            "--url=https://example.invalid",
            "--wsdl=https://example.invalid?WSDL",
            "--service-id=wsfe",
            "--represented-cuit=00000000000",
        ):
            with self.subTest(argument=argument):
                with self.assertRaises(CommandError):
                    parser.parse_args(
                        [
                            "--company-id=1",
                            f"--taxpayer-cuit={TEST_CUIT}",
                            argument,
                        ]
                    )

    def test_probe_stops_before_client_when_gate_fails(self):
        company = SimpleNamespace(id=1)
        client = MagicMock()
        command = TaxpayerProbeCommand(
            stdout=StringIO(),
            stderr=StringIO(),
        )
        with (
            patch(
                "core.management.commands.arca_taxpayer_readonly_probe."
                "Company.objects.get",
                return_value=company,
            ),
            patch(
                "core.management.commands.arca_taxpayer_readonly_probe."
                "evaluate_taxpayer_registry_readiness",
                return_value=SimpleNamespace(
                    passed=False,
                    error_codes=("taxpayer_user_signal_missing",),
                ),
            ),
            patch(
                "core.management.commands.arca_taxpayer_readonly_probe."
                "ArcaTaxpayerRegistryClient",
                client,
            ),
        ):
            with self.assertRaises(CommandError):
                command.handle(company_id=1, taxpayer_cuit=TEST_CUIT)
        client.assert_not_called()

    @override_settings(ARCA_TEST_TAXPAYER_CUIT=TEST_CUIT)
    def test_probe_can_take_future_test_cuit_from_private_setting(self):
        company = SimpleNamespace(id=1)
        client = MagicMock()
        command = TaxpayerProbeCommand(
            stdout=StringIO(),
            stderr=StringIO(),
        )
        with (
            patch(
                "core.management.commands.arca_taxpayer_readonly_probe."
                "Company.objects.get",
                return_value=company,
            ),
            patch(
                "core.management.commands.arca_taxpayer_readonly_probe."
                "evaluate_taxpayer_registry_readiness",
                return_value=SimpleNamespace(
                    passed=False,
                    error_codes=("taxpayer_user_signal_missing",),
                ),
            ),
            patch(
                "core.management.commands.arca_taxpayer_readonly_probe."
                "ArcaTaxpayerRegistryClient",
                client,
            ),
        ):
            with self.assertRaises(CommandError) as context:
                command.handle(company_id=1, taxpayer_cuit=None)
        self.assertIn(
            "ARCA_TAXPAYER_READINESS_GATE=FAIL",
            str(context.exception),
        )
        client.assert_not_called()

    def test_mocked_probe_runs_dummy_then_lookup_retains_ticket_and_redacts(self):
        company = SimpleNamespace(id=1)
        stdout = StringIO()
        client_factory = MagicMock()
        client = client_factory.return_value
        client.fetch_service_status.return_value = {
            "ok": True,
            "appserver": "OK",
            "authserver": "OK",
            "dbserver": "OK",
        }
        client.lookup_taxpayer.return_value = SimpleNamespace(
            status=TaxpayerLookupStatus.PARTIAL,
            taxes=({},),
            activities=({}, {}),
            warnings=("fixture warning",),
            is_partial=True,
            display_name="MUST-NOT-BE-PRINTED",
            token="SENTINEL-TOKEN",
            sign="SENTINEL-SIGN",
        )
        command = TaxpayerProbeCommand(
            stdout=stdout,
            stderr=StringIO(),
        )
        with (
            patch(
                "core.management.commands.arca_taxpayer_readonly_probe."
                "Company.objects.get",
                return_value=company,
            ),
            patch(
                "core.management.commands.arca_taxpayer_readonly_probe."
                "evaluate_taxpayer_registry_readiness",
                return_value=SimpleNamespace(passed=True, error_codes=()),
            ),
            patch(
                "core.management.commands.arca_taxpayer_readonly_probe."
                "ArcaTaxpayerRegistryClient",
                client_factory,
            ),
        ):
            command.handle(company_id=1, taxpayer_cuit=TEST_CUIT)

        output = stdout.getvalue()
        client.fetch_service_status.assert_called_once_with()
        client.lookup_taxpayer.assert_called_once_with(TEST_CUIT)
        client.ticket_coordinator.clear_ticket.assert_not_called()
        self.assertIn("ARCA_TAXPAYER_READONLY_PROBE=PASS", output)
        self.assertIn(
            "ticket_cache_policy=retain_until_renewal_window",
            output,
        )
        self.assertIn("lookup_status=PARTIAL", output)
        self.assertIn("tax_count=1", output)
        self.assertIn("activity_count=2", output)
        self.assertIn("taxpayer_cuit=*********39", output)
        self.assertNotIn("MUST-NOT-BE-PRINTED", output)
        self.assertNotIn("SENTINEL-TOKEN", output)
        self.assertNotIn("SENTINEL-SIGN", output)

    def test_probe_sanitizes_failure_and_retains_valid_ticket(self):
        company = SimpleNamespace(id=1)
        client_factory = MagicMock()
        client = client_factory.return_value
        client.fetch_service_status.return_value = {"ok": True}
        client.lookup_taxpayer.side_effect = RuntimeError(
            "token=SENTINEL-TOKEN sign=SENTINEL-SIGN"
        )
        command = TaxpayerProbeCommand(
            stdout=StringIO(),
            stderr=StringIO(),
        )
        with (
            patch(
                "core.management.commands.arca_taxpayer_readonly_probe."
                "Company.objects.get",
                return_value=company,
            ),
            patch(
                "core.management.commands.arca_taxpayer_readonly_probe."
                "evaluate_taxpayer_registry_readiness",
                return_value=SimpleNamespace(passed=True, error_codes=()),
            ),
            patch(
                "core.management.commands.arca_taxpayer_readonly_probe."
                "ArcaTaxpayerRegistryClient",
                client_factory,
            ),
        ):
            with self.assertRaises(CommandError) as context:
                command.handle(company_id=1, taxpayer_cuit=TEST_CUIT)
        self.assertNotIn("SENTINEL-TOKEN", str(context.exception))
        self.assertNotIn("SENTINEL-SIGN", str(context.exception))
        client.ticket_coordinator.clear_ticket.assert_not_called()
