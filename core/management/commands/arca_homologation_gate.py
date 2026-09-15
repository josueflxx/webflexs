"""Evaluate the local, no-network ARCA homologation readiness gate."""

from django.core.management.base import BaseCommand, CommandError

from core.models import Company, FiscalPointOfSale
from core.services.arca_homologation import evaluate_homologation_readiness


class Command(BaseCommand):
    help = (
        "Validate ARCA homologation read-only configuration and credentials "
        "without contacting WSAA or WSFEv1."
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
        result = evaluate_homologation_readiness(
            company=company,
            point_of_sale=point_of_sale,
            check_credentials=True,
        )
        if result.passed:
            self.stdout.write("ARCA_HOMOLOGATION_READINESS_GATE=PASS")
            return

        self.stdout.write("ARCA_HOMOLOGATION_READINESS_GATE=FAIL")
        for error_code in result.error_codes:
            self.stderr.write(f"reason={error_code}")
