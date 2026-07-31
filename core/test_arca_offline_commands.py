from __future__ import annotations

from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from core.management.commands.arca_homologation_readonly_probe import (
    Command as ReadonlyProbeCommand,
)
from core.test_arca_homologation_gate import SAFE_READ_SETTINGS


def _network_forbidden(*_args, **_kwargs):
    raise AssertionError("network access is forbidden in offline tests")


class ArcaOfflineManagementCommandTests(SimpleTestCase):
    def _network_guards(self):
        return (
            patch("socket.getaddrinfo", side_effect=_network_forbidden),
            patch(
                "socket.create_connection",
                side_effect=_network_forbidden,
            ),
            patch(
                "urllib.request.OpenerDirector.open",
                side_effect=_network_forbidden,
            ),
            patch(
                "core.services.arca_transport."
                "StrictArcaSoapTransport.post",
                side_effect=_network_forbidden,
            ),
            patch(
                "core.services.arca_client.ArcaWsfeClient.run_preflight",
                side_effect=_network_forbidden,
            ),
        )

    def test_doctor_reports_current_state_as_waiting_without_network(self):
        stdout = StringIO()
        with (
            self._network_guards()[0],
            self._network_guards()[1],
            self._network_guards()[2],
            self._network_guards()[3],
            self._network_guards()[4],
        ):
            call_command(
                "arca_homologation_doctor",
                stdout=stdout,
                stderr=StringIO(),
            )
        output = stdout.getvalue()
        self.assertIn(
            "ARCA_HOMOLOGATION_DOCTOR=WAITING_FOR_USER",
            output,
        )
        self.assertIn("production_disabled=yes", output)
        self.assertIn("emission_disabled=yes", output)
        self.assertIn("certificate_present=no", output)
        self.assertIn("private_key_present=no", output)
        self.assertIn("gate_possible=no", output)
        self.assertIn("probe_possible=no", output)
        self.assertNotIn("\\Users\\", output)

    def test_gate_is_offline_and_fail_closed(self):
        stdout = StringIO()
        stderr = StringIO()
        guards = self._network_guards()
        with guards[0], guards[1], guards[2], guards[3], guards[4]:
            call_command(
                "arca_homologation_gate",
                stdout=stdout,
                stderr=stderr,
            )
        self.assertIn(
            "ARCA_HOMOLOGATION_READINESS_GATE=FAIL",
            stdout.getvalue(),
        )
        self.assertIn("reason=integration_disabled", stderr.getvalue())

    @override_settings(
        ARCA_HOMOLOGATION_EMISSION_ENABLED=True,
    )
    def test_doctor_classifies_unsafe_configuration_as_fail(self):
        stdout = StringIO()
        call_command(
            "arca_homologation_doctor",
            stdout=stdout,
            stderr=StringIO(),
        )
        self.assertIn(
            "ARCA_HOMOLOGATION_DOCTOR=FAIL",
            stdout.getvalue(),
        )
        self.assertIn(
            "reason=homologation_emission_must_remain_disabled",
            stdout.getvalue(),
        )

    @override_settings(**SAFE_READ_SETTINGS)
    def test_doctor_waits_when_configured_credential_files_are_absent(self):
        stdout = StringIO()
        call_command(
            "arca_homologation_doctor",
            stdout=stdout,
            stderr=StringIO(),
        )
        output = stdout.getvalue()
        self.assertIn(
            "ARCA_HOMOLOGATION_DOCTOR=WAITING_FOR_USER",
            output,
        )
        self.assertIn("certificate_path_configured=yes", output)
        self.assertIn("private_key_path_configured=yes", output)
        self.assertIn("certificate_present=no", output)
        self.assertIn("private_key_present=no", output)
        self.assertNotIn(
            SAFE_READ_SETTINGS["ARCA_CERT_PATH"],
            output,
        )

    def test_probe_parser_rejects_arbitrary_url_and_cuit(self):
        parser = ReadonlyProbeCommand().create_parser(
            "manage.py",
            "arca_homologation_readonly_probe",
        )
        for argument in (
            "--url=https://example.invalid",
            "--cuit=00000000000",
            "--point-of-sale=999",
        ):
            with self.subTest(argument=argument):
                with self.assertRaises(CommandError):
                    parser.parse_args(
                        [
                            "--company-id=1",
                            "--point-of-sale-id=2",
                            argument,
                        ]
                    )

    def test_probe_stops_before_client_when_gate_fails(self):
        company = SimpleNamespace(id=1)
        point = SimpleNamespace(id=2)
        client = MagicMock()
        command = ReadonlyProbeCommand(
            stdout=StringIO(),
            stderr=StringIO(),
        )
        with (
            patch(
                "core.management.commands."
                "arca_homologation_readonly_probe.Company.objects.get",
                return_value=company,
            ),
            patch(
                "core.management.commands."
                "arca_homologation_readonly_probe."
                "FiscalPointOfSale.objects.get",
                return_value=point,
            ) as point_get,
            patch(
                "core.management.commands."
                "arca_homologation_readonly_probe."
                "evaluate_homologation_readiness",
                return_value=SimpleNamespace(
                    passed=False,
                    error_codes=("user_readiness_signal_missing",),
                ),
            ),
            patch(
                "core.management.commands."
                "arca_homologation_readonly_probe.ArcaWsfeClient",
                client,
            ),
        ):
            with self.assertRaises(CommandError):
                command.handle(company_id=1, point_of_sale_id=2)
        point_get.assert_called_once_with(pk=2, company=company)
        client.assert_not_called()

    def test_mocked_probe_uses_only_fixed_preflight_and_redacts_output(self):
        company = SimpleNamespace(id=1)
        point = SimpleNamespace(id=2)
        stdout = StringIO()
        client = MagicMock()
        client.return_value.run_preflight.return_value = {
            "ok": True,
            "environment": "homologation",
            "point_of_sale": "7",
            "voucher_type": "6",
            "checks": {
                "token_obtained": True,
                "sign_obtained": True,
                "configured_point_found": True,
                "configured_voucher_type_found": True,
            },
            "last_authorized_number": 0,
            "token": "SENTINEL_TOKEN",
            "sign": "SENTINEL_SIGN",
        }
        command = ReadonlyProbeCommand(
            stdout=stdout,
            stderr=StringIO(),
        )
        with (
            patch(
                "core.management.commands."
                "arca_homologation_readonly_probe.Company.objects.get",
                return_value=company,
            ),
            patch(
                "core.management.commands."
                "arca_homologation_readonly_probe."
                "FiscalPointOfSale.objects.get",
                return_value=point,
            ),
            patch(
                "core.management.commands."
                "arca_homologation_readonly_probe."
                "evaluate_homologation_readiness",
                return_value=SimpleNamespace(
                    passed=True,
                    error_codes=(),
                ),
            ),
            patch(
                "core.management.commands."
                "arca_homologation_readonly_probe.ArcaWsfeClient",
                client,
            ),
        ):
            command.handle(company_id=1, point_of_sale_id=2)
        output = stdout.getvalue()
        client.return_value.run_preflight.assert_called_once_with()
        client.return_value.ticket_coordinator.clear_ticket.assert_called_once_with()
        self.assertIn(
            "ARCA_HOMOLOGATION_READONLY_PROBE=PASS",
            output,
        )
        self.assertIn("ticket_cache_cleared=True", output)
        self.assertIn("point_of_sale=*7", output)
        self.assertNotIn("SENTINEL_TOKEN", output)
        self.assertNotIn("SENTINEL_SIGN", output)

    def test_mocked_probe_stops_and_sanitizes_partial_failure(self):
        company = SimpleNamespace(id=1)
        point = SimpleNamespace(id=2)
        client = MagicMock()
        client.return_value.run_preflight.side_effect = RuntimeError(
            "token=SENTINEL_TOKEN sign=SENTINEL_SIGN"
        )
        command = ReadonlyProbeCommand(
            stdout=StringIO(),
            stderr=StringIO(),
        )
        with (
            patch(
                "core.management.commands."
                "arca_homologation_readonly_probe.Company.objects.get",
                return_value=company,
            ),
            patch(
                "core.management.commands."
                "arca_homologation_readonly_probe."
                "FiscalPointOfSale.objects.get",
                return_value=point,
            ),
            patch(
                "core.management.commands."
                "arca_homologation_readonly_probe."
                "evaluate_homologation_readiness",
                return_value=SimpleNamespace(
                    passed=True,
                    error_codes=(),
                ),
            ),
            patch(
                "core.management.commands."
                "arca_homologation_readonly_probe.ArcaWsfeClient",
                client,
            ),
        ):
            with self.assertRaises(CommandError) as context:
                command.handle(company_id=1, point_of_sale_id=2)
        message = str(context.exception)
        self.assertNotIn("SENTINEL_TOKEN", message)
        self.assertNotIn("SENTINEL_SIGN", message)
        client.return_value.ticket_coordinator.clear_ticket.assert_called_once_with()
        client.assert_called_once_with(
            company=company,
            point_of_sale=point,
        )

    def test_mocked_probe_fails_closed_when_cache_cleanup_fails(self):
        company = SimpleNamespace(id=1)
        point = SimpleNamespace(id=2)
        client = MagicMock()
        client.return_value.run_preflight.return_value = {
            "ok": True,
            "checks": {},
        }
        client.return_value.ticket_coordinator.clear_ticket.side_effect = (
            RuntimeError("token=SENTINEL_TOKEN")
        )
        command = ReadonlyProbeCommand(
            stdout=StringIO(),
            stderr=StringIO(),
        )
        with (
            patch(
                "core.management.commands."
                "arca_homologation_readonly_probe.Company.objects.get",
                return_value=company,
            ),
            patch(
                "core.management.commands."
                "arca_homologation_readonly_probe."
                "FiscalPointOfSale.objects.get",
                return_value=point,
            ),
            patch(
                "core.management.commands."
                "arca_homologation_readonly_probe."
                "evaluate_homologation_readiness",
                return_value=SimpleNamespace(
                    passed=True,
                    error_codes=(),
                ),
            ),
            patch(
                "core.management.commands."
                "arca_homologation_readonly_probe.ArcaWsfeClient",
                client,
            ),
        ):
            with self.assertRaises(CommandError) as context:
                command.handle(company_id=1, point_of_sale_id=2)
        self.assertNotIn("SENTINEL_TOKEN", str(context.exception))
