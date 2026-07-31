"""Report sanitized ARCA homologation readiness without network I/O."""

from django.core.management.base import BaseCommand

from core.services.arca_doctor import evaluate_homologation_doctor


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


class Command(BaseCommand):
    help = (
        "Diagnose local ARCA homologation readiness without contacting "
        "DNS, WSAA, WSFEv1 or a WSDL endpoint."
    )

    def handle(self, *args, **options):
        del args, options
        result = evaluate_homologation_doctor()
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
