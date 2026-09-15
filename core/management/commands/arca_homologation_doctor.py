"""Report sanitized ARCA homologation readiness without network I/O."""

from django.core.management.base import BaseCommand, CommandError

from core.models import Company, FiscalPointOfSale
from core.services.arca_doctor import evaluate_homologation_doctor


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


class Command(BaseCommand):
    help = (
        "Diagnose local ARCA homologation readiness without contacting "
        "DNS, WSAA, WSFEv1 or a WSDL endpoint."
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
        result = evaluate_homologation_doctor(
            company=company,
            point_of_sale=point_of_sale,
        )
        self.stdout.write(
            f"ARCA_HOMOLOGATION_DOCTOR={result.status}"
        )
        self.stdout.write(f"environment={result.environment}")
        for name in (
            "production_disabled",
            "emission_disabled",
            "endpoints_allowlisted",
            "tls_active",
            "redaction_active",
            "required_variables_present",
            "certificate_path_configured",
            "private_key_path_configured",
            "certificate_present",
            "private_key_present",
            "certificate_subject_expected",
            "certificate_issuer_expected",
            "credential_validated",
            "wsass_authorization_confirmed",
            "cuit_configured",
            "point_of_sale_configured",
            "voucher_type_configured",
            "user_signal",
            "gate_possible",
            "probe_possible",
        ):
            self.stdout.write(
                f"{name}={_yes_no(bool(getattr(result, name)))}"
            )
        self.stdout.write(f"cache_state={result.cache_state}")
        for reason in result.reasons:
            self.stdout.write(f"reason={reason}")
