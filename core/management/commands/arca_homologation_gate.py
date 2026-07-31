"""Evaluate the local, no-network ARCA homologation readiness gate."""

from django.core.management.base import BaseCommand

from core.services.arca_homologation import evaluate_homologation_readiness


class Command(BaseCommand):
    help = (
        "Validate ARCA homologation read-only configuration and credentials "
        "without contacting WSAA or WSFEv1."
    )

    def handle(self, *args, **options):
        del args, options
        result = evaluate_homologation_readiness(check_credentials=True)
        if result.passed:
            self.stdout.write("ARCA_HOMOLOGATION_READINESS_GATE=PASS")
            return

        self.stdout.write("ARCA_HOMOLOGATION_READINESS_GATE=FAIL")
        for error_code in result.error_codes:
            self.stderr.write(f"reason={error_code}")
