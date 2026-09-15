"""Run Phase 1.1 readiness checks with outbound network primitives blocked."""

from unittest.mock import patch

from django.core.management.base import BaseCommand, CommandError

from core.models import Company, FiscalPointOfSale
from core.services.arca_doctor import evaluate_homologation_doctor
from core.services.arca_homologation import (
    evaluate_homologation_readiness,
    evaluate_taxpayer_registry_readiness,
)


def _network_forbidden(*_args, **_kwargs):
    raise AssertionError("Network is forbidden during ARCA Phase 1.1.")


class Command(BaseCommand):
    help = (
        "Run doctor and both ARCA gates with DNS, sockets and SOAP transport "
        "blocked. This command never runs a probe."
    )

    def add_arguments(self, parser):
        parser.add_argument("--company-id", type=int)
        parser.add_argument("--point-of-sale-id", type=int)

    def handle(self, *args, **options):
        del args
        company = None
        point_of_sale = None
        company_id = options.get("company_id")
        point_id = options.get("point_of_sale_id")
        if point_id and not company_id:
            raise CommandError(
                "--point-of-sale-id requiere --company-id."
            )
        if company_id:
            try:
                company = Company.objects.get(pk=company_id)
            except Company.DoesNotExist as exc:
                raise CommandError("Empresa no encontrada.") from exc
        if point_id:
            try:
                point_of_sale = FiscalPointOfSale.objects.get(
                    pk=point_id,
                    company=company,
                )
            except FiscalPointOfSale.DoesNotExist as exc:
                raise CommandError(
                    "Punto de venta no encontrado para la empresa."
                ) from exc

        with (
            patch("socket.getaddrinfo", side_effect=_network_forbidden),
            patch("socket.create_connection", side_effect=_network_forbidden),
            patch("socket.socket.connect", side_effect=_network_forbidden),
            patch(
                "urllib.request.OpenerDirector.open",
                side_effect=_network_forbidden,
            ),
            patch(
                "core.services.arca_transport.StrictArcaSoapTransport.post",
                side_effect=_network_forbidden,
            ),
        ):
            doctor = evaluate_homologation_doctor(
                company=company,
                point_of_sale=point_of_sale,
            )
            wsfe = evaluate_homologation_readiness(
                company=company,
                point_of_sale=point_of_sale,
                check_credentials=True,
            )
            taxpayer = evaluate_taxpayer_registry_readiness(
                company=company,
                check_credentials=True,
            )

        self.stdout.write("ARCA_PHASE11_NETWORK_GUARD=ACTIVE")
        self.stdout.write(f"doctor={doctor.status}")
        self.stdout.write(
            "gate_wsfe=" + ("PASS" if wsfe.passed else "FAIL")
        )
        self.stdout.write(
            "gate_taxpayer=" + ("PASS" if taxpayer.passed else "FAIL")
        )
        self.stdout.write(
            "credential_validated="
            + ("yes" if doctor.credential_validated else "no")
        )
        for reason in wsfe.error_codes:
            self.stdout.write(f"reason_wsfe={reason}")
        for reason in taxpayer.error_codes:
            self.stdout.write(f"reason_taxpayer={reason}")

