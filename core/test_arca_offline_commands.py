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
from core.management.commands.arca_homologation_emission_gate import (
    Command as EmissionGateCommand,
)
from core.management.commands.arca_wsfe_points_of_sale_probe import (
    Command as PointsOfSaleProbeCommand,
)
from core.management.commands.arca_wsfe_last_authorized_probe import (
    Command as LastAuthorizedProbeCommand,
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

    def test_phase11_command_blocks_network_and_reports_all_gates(self):
        stdout = StringIO()
        call_command(
            "arca_phase11_offline_check",
            stdout=stdout,
            stderr=StringIO(),
        )
        output = stdout.getvalue()
        self.assertIn("ARCA_PHASE11_NETWORK_GUARD=ACTIVE", output)
        self.assertIn("doctor=WAITING_FOR_USER", output)
        self.assertIn("gate_wsfe=FAIL", output)
        self.assertIn("gate_taxpayer=FAIL", output)

    def test_emission_gate_is_offline_and_outputs_only_sanitized_state(self):
        document = SimpleNamespace(
            company=SimpleNamespace(pk=1),
            point_of_sale=SimpleNamespace(pk=3),
        )
        stdout = StringIO()
        stderr = StringIO()
        command = EmissionGateCommand(stdout=stdout, stderr=stderr)
        with (
            patch(
                "core.management.commands.arca_homologation_emission_gate."
                "FiscalDocument.objects.select_related"
            ) as selected,
            patch(
                "core.management.commands.arca_homologation_emission_gate."
                "evaluate_homologation_emission_readiness",
                return_value=SimpleNamespace(
                    passed=False,
                    error_codes=("homologation_emission_disabled",),
                ),
            ),
            patch("socket.getaddrinfo", side_effect=_network_forbidden),
            patch("socket.create_connection", side_effect=_network_forbidden),
            patch("socket.socket.connect", side_effect=_network_forbidden),
        ):
            selected.return_value.prefetch_related.return_value.get.return_value = document
            command.handle(document_id=77)
        output = stdout.getvalue()
        self.assertIn("ARCA_HOMOLOGATION_EMISSION_NETWORK_GUARD=ACTIVE", output)
        self.assertIn("network_used=no", output)
        self.assertIn("ARCA_HOMOLOGATION_EMISSION_GATE=FAIL", output)
        self.assertIn("reason=homologation_emission_disabled", stderr.getvalue())
        self.assertNotIn("snapshot", output.lower())

    def test_offline_diagnostics_require_company_for_point_selection(self):
        for command_name in (
            "arca_homologation_doctor",
            "arca_homologation_gate",
            "arca_phase11_offline_check",
        ):
            with self.subTest(command=command_name):
                with self.assertRaises(CommandError):
                    call_command(
                        command_name,
                        point_of_sale_id=1,
                        stdout=StringIO(),
                        stderr=StringIO(),
                    )

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
        client.return_value.ticket_coordinator.clear_ticket.assert_not_called()
        self.assertIn(
            "ARCA_HOMOLOGATION_READONLY_PROBE=PASS",
            output,
        )
        self.assertIn(
            "ticket_cache_policy=retain_until_renewal_window",
            output,
        )
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
        client.return_value.ticket_coordinator.clear_ticket.assert_not_called()
        client.assert_called_once_with(
            company=company,
            point_of_sale=point,
        )

    def test_mocked_probe_never_deletes_a_valid_cached_ticket(self):
        company = SimpleNamespace(id=1)
        point = SimpleNamespace(id=2)
        client = MagicMock()
        client.return_value.run_preflight.return_value = {
            "ok": True,
            "checks": {},
        }
        client.return_value.ticket_coordinator.clear_ticket.side_effect = (
            AssertionError("valid ticket must be retained")
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
            command.handle(company_id=1, point_of_sale_id=2)
        client.return_value.ticket_coordinator.clear_ticket.assert_not_called()


class ArcaPointsOfSaleProbeCommandTests(SimpleTestCase):
    def test_parser_rejects_every_remote_scope_override(self):
        parser = PointsOfSaleProbeCommand().create_parser(
            "manage.py",
            "arca_wsfe_points_of_sale_probe",
        )
        for argument in (
            "--url=https://example.invalid",
            "--method=FEDummy",
            "--voucher-type=1",
            "--taxpayer-cuit=30693450239",
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

    def test_gate_failure_stops_before_client_construction(self):
        company = SimpleNamespace(id=1)
        point = SimpleNamespace(id=2)
        client_factory = MagicMock()
        command = PointsOfSaleProbeCommand(
            stdout=StringIO(),
            stderr=StringIO(),
        )
        with (
            patch(
                "core.management.commands.arca_wsfe_points_of_sale_probe."
                "Company.objects.get",
                return_value=company,
            ),
            patch(
                "core.management.commands.arca_wsfe_points_of_sale_probe."
                "FiscalPointOfSale.objects.get",
                return_value=point,
            ),
            patch(
                "core.management.commands.arca_wsfe_points_of_sale_probe."
                "evaluate_homologation_readiness",
                return_value=SimpleNamespace(
                    passed=False,
                    error_codes=("point_of_sale_missing",),
                ),
            ),
            patch(
                "core.management.commands.arca_wsfe_points_of_sale_probe."
                "ArcaWsfeClient",
                client_factory,
            ),
        ):
            with self.assertRaises(CommandError):
                command.handle(company_id=1, point_of_sale_id=2)
        client_factory.assert_not_called()

    def test_calls_only_points_catalog_and_prints_sanitized_rows(self):
        company = SimpleNamespace(id=1)
        point = SimpleNamespace(id=2)
        stdout = StringIO()
        client_factory = MagicMock()
        client = client_factory.return_value
        client.fetch_points_of_sale.return_value = {
            "method": "FEParamGetPtosVenta",
            "values": [
                {
                    "Nro": "12",
                    "EmisionTipo": "CAE",
                    "Bloqueado": "N",
                    "Token": "SENTINEL-TOKEN",
                },
                {
                    "Nro": "3",
                    "EmisionTipo": "CAEA<script>",
                    "Bloqueado": "S",
                    "Sign": "SENTINEL-SIGN",
                },
            ],
        }
        command = PointsOfSaleProbeCommand(
            stdout=stdout,
            stderr=StringIO(),
        )
        with (
            patch(
                "core.management.commands.arca_wsfe_points_of_sale_probe."
                "Company.objects.get",
                return_value=company,
            ),
            patch(
                "core.management.commands.arca_wsfe_points_of_sale_probe."
                "FiscalPointOfSale.objects.get",
                return_value=point,
            ),
            patch(
                "core.management.commands.arca_wsfe_points_of_sale_probe."
                "evaluate_homologation_readiness",
                return_value=SimpleNamespace(passed=True, error_codes=()),
            ),
            patch(
                "core.management.commands.arca_wsfe_points_of_sale_probe."
                "ArcaWsfeClient",
                client_factory,
            ),
        ):
            command.handle(company_id=1, point_of_sale_id=2)

        output = stdout.getvalue()
        client.fetch_points_of_sale.assert_called_once_with()
        client.fetch_service_status.assert_not_called()
        client.fetch_readonly_catalogs.assert_not_called()
        client.fetch_last_authorized_by_type.assert_not_called()
        client.emit_fiscal_document.assert_not_called()
        client.ticket_coordinator.clear_ticket.assert_not_called()
        self.assertIn("ARCA_WSFE_POINTS_OF_SALE_PROBE=PASS", output)
        self.assertIn("point_count=2", output)
        self.assertIn("point_1_number=3", output)
        self.assertIn("point_1_emission_type=CAEAscript", output)
        self.assertIn("point_1_blocked=S", output)
        self.assertIn("point_2_number=12", output)
        self.assertIn("point_2_emission_type=CAE", output)
        self.assertIn("point_2_blocked=N", output)
        self.assertIn(
            "ticket_cache_policy=retain_until_renewal_window",
            output,
        )
        self.assertNotIn("SENTINEL-TOKEN", output)
        self.assertNotIn("SENTINEL-SIGN", output)

    def test_failure_is_sanitized_and_ticket_is_not_deleted(self):
        company = SimpleNamespace(id=1)
        point = SimpleNamespace(id=2)
        client_factory = MagicMock()
        client = client_factory.return_value
        client.fetch_points_of_sale.side_effect = RuntimeError(
            "token=SENTINEL-TOKEN sign=SENTINEL-SIGN"
        )
        command = PointsOfSaleProbeCommand(
            stdout=StringIO(),
            stderr=StringIO(),
        )
        with (
            patch(
                "core.management.commands.arca_wsfe_points_of_sale_probe."
                "Company.objects.get",
                return_value=company,
            ),
            patch(
                "core.management.commands.arca_wsfe_points_of_sale_probe."
                "FiscalPointOfSale.objects.get",
                return_value=point,
            ),
            patch(
                "core.management.commands.arca_wsfe_points_of_sale_probe."
                "evaluate_homologation_readiness",
                return_value=SimpleNamespace(passed=True, error_codes=()),
            ),
            patch(
                "core.management.commands.arca_wsfe_points_of_sale_probe."
                "ArcaWsfeClient",
                client_factory,
            ),
        ):
            with self.assertRaises(CommandError) as context:
                command.handle(company_id=1, point_of_sale_id=2)
        message = str(context.exception)
        self.assertNotIn("SENTINEL-TOKEN", message)
        self.assertNotIn("SENTINEL-SIGN", message)
        client.ticket_coordinator.clear_ticket.assert_not_called()

    def test_unparseable_point_fails_closed_without_raw_value(self):
        company = SimpleNamespace(id=1)
        point = SimpleNamespace(id=2)
        client_factory = MagicMock()
        client_factory.return_value.fetch_points_of_sale.return_value = {
            "values": [{"Nro": "SECRET-NUMBER"}],
        }
        command = PointsOfSaleProbeCommand(
            stdout=StringIO(),
            stderr=StringIO(),
        )
        with (
            patch(
                "core.management.commands.arca_wsfe_points_of_sale_probe."
                "Company.objects.get",
                return_value=company,
            ),
            patch(
                "core.management.commands.arca_wsfe_points_of_sale_probe."
                "FiscalPointOfSale.objects.get",
                return_value=point,
            ),
            patch(
                "core.management.commands.arca_wsfe_points_of_sale_probe."
                "evaluate_homologation_readiness",
                return_value=SimpleNamespace(passed=True, error_codes=()),
            ),
            patch(
                "core.management.commands.arca_wsfe_points_of_sale_probe."
                "ArcaWsfeClient",
                client_factory,
            ),
        ):
            with self.assertRaises(CommandError) as context:
                command.handle(company_id=1, point_of_sale_id=2)
        self.assertNotIn("SECRET-NUMBER", str(context.exception))


@override_settings(ARCA_DEFAULT_CBTE_TIPO="1")
class ArcaLastAuthorizedProbeCommandTests(SimpleTestCase):
    MODULE = (
        "core.management.commands.arca_wsfe_last_authorized_probe"
    )

    def _valid_identity(self):
        return (
            SimpleNamespace(id=1),
            SimpleNamespace(
                id=3,
                number="3",
                environment="homologation",
            ),
        )

    def test_parser_rejects_every_remote_scope_override(self):
        parser = LastAuthorizedProbeCommand().create_parser(
            "manage.py",
            "arca_wsfe_last_authorized_probe",
        )
        for argument in (
            "--url=https://example.invalid",
            "--method=FEDummy",
            "--voucher-type=6",
            "--taxpayer-cuit=30693450239",
        ):
            with self.subTest(argument=argument):
                with self.assertRaises(CommandError):
                    parser.parse_args(
                        [
                            "--company-id=1",
                            "--point-of-sale-id=3",
                            argument,
                        ]
                    )

    def test_rejects_another_company_or_point_before_database_access(self):
        command = LastAuthorizedProbeCommand(
            stdout=StringIO(),
            stderr=StringIO(),
        )
        with patch(f"{self.MODULE}.Company.objects.get") as get_company:
            with self.assertRaises(CommandError):
                command.handle(company_id=2, point_of_sale_id=3)
            with self.assertRaises(CommandError):
                command.handle(company_id=1, point_of_sale_id=4)
        get_company.assert_not_called()

    def test_gate_failure_stops_before_client_construction(self):
        company, point = self._valid_identity()
        client_factory = MagicMock()
        command = LastAuthorizedProbeCommand(
            stdout=StringIO(),
            stderr=StringIO(),
        )
        with (
            patch(
                f"{self.MODULE}.Company.objects.get",
                return_value=company,
            ),
            patch(
                f"{self.MODULE}.FiscalPointOfSale.objects.get",
                return_value=point,
            ),
            patch(
                f"{self.MODULE}.evaluate_homologation_readiness",
                return_value=SimpleNamespace(
                    passed=False,
                    error_codes=("credential_invalid",),
                ),
            ),
            patch(f"{self.MODULE}.ArcaWsfeClient", client_factory),
        ):
            with self.assertRaises(CommandError):
                command.handle(company_id=1, point_of_sale_id=3)
        client_factory.assert_not_called()

    @override_settings(ARCA_DEFAULT_CBTE_TIPO="6")
    def test_rejects_non_factura_a_before_gate_and_client(self):
        company, point = self._valid_identity()
        gate = MagicMock()
        client_factory = MagicMock()
        command = LastAuthorizedProbeCommand(
            stdout=StringIO(),
            stderr=StringIO(),
        )
        with (
            patch(
                f"{self.MODULE}.Company.objects.get",
                return_value=company,
            ),
            patch(
                f"{self.MODULE}.FiscalPointOfSale.objects.get",
                return_value=point,
            ),
            patch(
                f"{self.MODULE}.evaluate_homologation_readiness",
                gate,
            ),
            patch(f"{self.MODULE}.ArcaWsfeClient", client_factory),
        ):
            with self.assertRaises(CommandError):
                command.handle(company_id=1, point_of_sale_id=3)
        gate.assert_not_called()
        client_factory.assert_not_called()

    def test_calls_only_last_authorized_for_factura_a(self):
        company, point = self._valid_identity()
        stdout = StringIO()
        client_factory = MagicMock()
        client = client_factory.return_value
        client.fetch_last_authorized_by_type.return_value = 27
        command = LastAuthorizedProbeCommand(
            stdout=stdout,
            stderr=StringIO(),
        )
        with (
            patch(
                f"{self.MODULE}.Company.objects.get",
                return_value=company,
            ),
            patch(
                f"{self.MODULE}.FiscalPointOfSale.objects.get",
                return_value=point,
            ),
            patch(
                f"{self.MODULE}.evaluate_homologation_readiness",
                return_value=SimpleNamespace(passed=True, error_codes=()),
            ),
            patch(f"{self.MODULE}.ArcaWsfeClient", client_factory),
        ):
            command.handle(company_id=1, point_of_sale_id=3)

        output = stdout.getvalue()
        client.fetch_last_authorized_by_type.assert_called_once_with(
            cbte_type=1
        )
        client.fetch_service_status.assert_not_called()
        client.fetch_points_of_sale.assert_not_called()
        client.fetch_readonly_catalogs.assert_not_called()
        client.emit_fiscal_document.assert_not_called()
        client.ticket_coordinator.clear_ticket.assert_not_called()
        self.assertIn("ARCA_WSFE_LAST_AUTHORIZED_PROBE=PASS", output)
        self.assertIn("company_id=1", output)
        self.assertIn("point_of_sale_id=3", output)
        self.assertIn("point_of_sale_number=3", output)
        self.assertIn("voucher_type=1", output)
        self.assertIn("voucher_label=Factura A", output)
        self.assertIn("last_authorized_number=27", output)
        self.assertIn("local_state_updated=no", output)
        self.assertIn(
            "ticket_cache_policy=retain_until_renewal_window",
            output,
        )

    def test_failure_is_sanitized_and_ticket_is_not_deleted(self):
        company, point = self._valid_identity()
        client_factory = MagicMock()
        client = client_factory.return_value
        client.fetch_last_authorized_by_type.side_effect = RuntimeError(
            "token=SENTINEL-TOKEN sign=SENTINEL-SIGN"
        )
        command = LastAuthorizedProbeCommand(
            stdout=StringIO(),
            stderr=StringIO(),
        )
        with (
            patch(
                f"{self.MODULE}.Company.objects.get",
                return_value=company,
            ),
            patch(
                f"{self.MODULE}.FiscalPointOfSale.objects.get",
                return_value=point,
            ),
            patch(
                f"{self.MODULE}.evaluate_homologation_readiness",
                return_value=SimpleNamespace(passed=True, error_codes=()),
            ),
            patch(f"{self.MODULE}.ArcaWsfeClient", client_factory),
        ):
            with self.assertRaises(CommandError) as context:
                command.handle(company_id=1, point_of_sale_id=3)
        message = str(context.exception)
        self.assertNotIn("SENTINEL-TOKEN", message)
        self.assertNotIn("SENTINEL-SIGN", message)
        client.ticket_coordinator.clear_ticket.assert_not_called()

    def test_invalid_result_fails_closed(self):
        company, point = self._valid_identity()
        client_factory = MagicMock()
        client_factory.return_value.fetch_last_authorized_by_type.return_value = (
            "SECRET-NUMBER"
        )
        command = LastAuthorizedProbeCommand(
            stdout=StringIO(),
            stderr=StringIO(),
        )
        with (
            patch(
                f"{self.MODULE}.Company.objects.get",
                return_value=company,
            ),
            patch(
                f"{self.MODULE}.FiscalPointOfSale.objects.get",
                return_value=point,
            ),
            patch(
                f"{self.MODULE}.evaluate_homologation_readiness",
                return_value=SimpleNamespace(passed=True, error_codes=()),
            ),
            patch(f"{self.MODULE}.ArcaWsfeClient", client_factory),
        ):
            with self.assertRaises(CommandError) as context:
                command.handle(company_id=1, point_of_sale_id=3)
        self.assertNotIn("SECRET-NUMBER", str(context.exception))
