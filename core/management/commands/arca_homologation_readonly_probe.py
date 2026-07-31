"""Fixed-scope WSAA/WSFEv1 homologation read-only probe."""

from django.core.management.base import BaseCommand, CommandError

from core.models import Company, FiscalPointOfSale
from core.services.arca_client import ArcaWsfeClient
from core.services.arca_homologation import evaluate_homologation_readiness
from core.services.sensitive_data import sanitize_sensitive_text


def _mask_number(value) -> str:
    digits = "".join(char for char in str(value or "") if char.isdigit())
    if not digits:
        return "not-configured"
    return ("*" * max(len(digits) - 1, 1)) + digits[-1]


class Command(BaseCommand):
    help = (
        "After the local gate passes, run the fixed WSAA/WSFEv1 read-only "
        "homologation sequence. This command cannot emit a voucher."
    )

    def create_parser(self, *args, **kwargs):
        parser = super().create_parser(*args, **kwargs)
        parser.allow_abbrev = False
        return parser

    def add_arguments(self, parser):
        parser.add_argument("--company-id", required=True, type=int)
        parser.add_argument("--point-of-sale-id", required=True, type=int)

    def handle(self, *args, **options):
        del args
        try:
            company = Company.objects.get(pk=options["company_id"])
            point = FiscalPointOfSale.objects.get(
                pk=options["point_of_sale_id"],
                company=company,
            )
        except (Company.DoesNotExist, FiscalPointOfSale.DoesNotExist) as exc:
            raise CommandError(
                "Empresa o punto de venta no encontrado."
            ) from exc

        gate = evaluate_homologation_readiness(
            company=company,
            point_of_sale=point,
            check_credentials=True,
        )
        if not gate.passed:
            reasons = ",".join(gate.error_codes)
            raise CommandError(
                f"ARCA_HOMOLOGATION_READINESS_GATE=FAIL reasons={reasons}"
            )

        client = None
        result = None
        probe_error = None
        try:
            client = ArcaWsfeClient(
                company=company,
                point_of_sale=point,
            )
            result = client.run_preflight()
        except Exception as exc:
            probe_error = exc

        if client is not None:
            try:
                client.ticket_coordinator.clear_ticket()
            except Exception as exc:
                raise CommandError(
                    "La limpieza del cache de solo lectura fallo: "
                    + sanitize_sensitive_text(str(exc))
                ) from exc

        if probe_error is not None:
            raise CommandError(
                "La prueba de solo lectura fallo: "
                + sanitize_sensitive_text(str(probe_error))
            ) from probe_error

        self.stdout.write(
            "ARCA_HOMOLOGATION_READONLY_PROBE="
            + ("PASS" if result.get("ok") else "FAIL")
        )
        self.stdout.write("ticket_cache_cleared=True")
        self.stdout.write(f"environment={result.get('environment')}")
        self.stdout.write(
            "service_status_ok="
            + str(bool(result.get("service_status", {}).get("ok")))
        )
        self.stdout.write(
            "point_of_sale=" + _mask_number(result.get("point_of_sale"))
        )
        self.stdout.write(f"voucher_type={result.get('voucher_type')}")
        catalog_counts = result.get("catalog_counts", {})
        for name in (
            "voucher_types",
            "document_types",
            "vat_rates",
            "currencies",
            "concepts",
            "points_of_sale",
        ):
            self.stdout.write(
                f"catalog_count_{name}="
                + str(int(catalog_counts.get(name, 0) or 0))
            )
        checks = result.get("checks", {})
        for name in (
            "token_obtained",
            "sign_obtained",
            "configured_point_found",
            "configured_voucher_type_found",
        ):
            self.stdout.write(f"{name}={bool(checks.get(name))}")
        self.stdout.write(
            f"last_authorized_number={result.get('last_authorized_number')}"
        )
