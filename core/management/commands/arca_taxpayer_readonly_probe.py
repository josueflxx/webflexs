"""Fixed-scope, non-emitting taxpayer lookup probe for a future run."""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from accounts.fiscal_identity import is_valid_cuit, normalize_fiscal_document
from core.models import Company
from core.services.arca_homologation import (
    evaluate_taxpayer_registry_readiness,
)
from core.services.arca_taxpayer import ArcaTaxpayerRegistryClient
from core.services.sensitive_data import sanitize_sensitive_text


def _mask_cuit(value: str) -> str:
    digits = normalize_fiscal_document(value)
    if len(digits) < 2:
        return "not-configured"
    return ("*" * (len(digits) - 2)) + digits[-2:]


class Command(BaseCommand):
    help = (
        "After the taxpayer gate passes, run dummy + getPersona_v2 in "
        "homologation. This command cannot issue vouchers."
    )

    def create_parser(self, *args, **kwargs):
        parser = super().create_parser(*args, **kwargs)
        parser.allow_abbrev = False
        return parser

    def add_arguments(self, parser):
        parser.add_argument("--company-id", required=True, type=int)
        parser.add_argument("--taxpayer-cuit")

    def handle(self, *args, **options):
        del args
        taxpayer_cuit = normalize_fiscal_document(
            options.get("taxpayer_cuit")
            or getattr(settings, "ARCA_TEST_TAXPAYER_CUIT", "")
        )
        if not is_valid_cuit(taxpayer_cuit):
            raise CommandError(
                "CUIT a consultar ausente o invalida. Use --taxpayer-cuit "
                "o ARCA_TEST_TAXPAYER_CUIT."
            )
        try:
            company = Company.objects.get(pk=options["company_id"])
        except Company.DoesNotExist as exc:
            raise CommandError("Empresa no encontrada.") from exc

        gate = evaluate_taxpayer_registry_readiness(
            company=company,
            check_credentials=True,
        )
        if not gate.passed:
            raise CommandError(
                "ARCA_TAXPAYER_READINESS_GATE=FAIL reasons="
                + ",".join(gate.error_codes)
            )

        try:
            client = ArcaTaxpayerRegistryClient(company=company)
            status = client.fetch_service_status()
            if not status.get("ok"):
                raise CommandError(
                    "El dummy del servicio de constancia no devolvio OK."
                )
            result = client.lookup_taxpayer(taxpayer_cuit)
        except Exception as exc:
            raise CommandError(
                "La prueba de padron fallo: "
                + sanitize_sensitive_text(str(exc))
            ) from exc

        self.stdout.write("ARCA_TAXPAYER_READONLY_PROBE=PASS")
        self.stdout.write(
            "ticket_cache_policy=retain_until_renewal_window"
        )
        self.stdout.write("service_status_ok=True")
        self.stdout.write(f"taxpayer_cuit={_mask_cuit(taxpayer_cuit)}")
        self.stdout.write(f"lookup_status={result.status.value}")
        self.stdout.write(f"tax_count={len(result.taxes)}")
        self.stdout.write(f"activity_count={len(result.activities)}")
        self.stdout.write(f"warning_count={len(result.warnings)}")
        self.stdout.write(f"partial={bool(result.is_partial)}")
