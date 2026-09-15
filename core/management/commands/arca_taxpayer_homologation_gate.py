"""Evaluate the offline taxpayer-registry homologation gate."""

from django.core.management.base import BaseCommand, CommandError

from core.models import Company
from core.services.arca_homologation import (
    evaluate_taxpayer_registry_readiness,
)


class Command(BaseCommand):
    help = (
        "Validate taxpayer-registry homologation configuration and "
        "credentials without DNS, WSAA or getPersona_v2."
    )

    def add_arguments(self, parser):
        parser.add_argument("--company-id", type=int)

    def handle(self, *args, **options):
        del args
        company = None
        company_id = options.get("company_id")
        if company_id:
            try:
                company = Company.objects.get(pk=company_id)
            except Company.DoesNotExist as exc:
                raise CommandError("Empresa no encontrada.") from exc
        result = evaluate_taxpayer_registry_readiness(
            company=company,
            check_credentials=True,
        )
        if result.passed:
            self.stdout.write("ARCA_TAXPAYER_READINESS_GATE=PASS")
            return
        self.stdout.write("ARCA_TAXPAYER_READINESS_GATE=FAIL")
        for error_code in result.error_codes:
            self.stderr.write(f"reason={error_code}")
