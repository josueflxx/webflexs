"""Fixed-scope, read-only FEParamGetPtosVenta homologation probe."""

from django.core.management.base import BaseCommand, CommandError

from core.models import Company, FiscalPointOfSale
from core.services.arca_client import ArcaWsfeClient
from core.services.arca_homologation import evaluate_homologation_readiness
from core.services.sensitive_data import sanitize_sensitive_text


def _safe_catalog_rows(rows):
    safe_rows = []
    for row in rows or ():
        number_raw = str(row.get("Nro") or "").strip()
        if not number_raw.isdigit():
            raise CommandError(
                "ARCA devolvio un punto de venta no interpretable."
            )
        number = int(number_raw)
        if not 0 < number <= 999999:
            raise CommandError(
                "ARCA devolvio un punto de venta fuera de rango."
            )

        emission_raw = str(row.get("EmisionTipo") or "").strip()
        emission_type = "".join(
            character
            for character in emission_raw
            if character.isalnum() or character in "-_"
        )[:20]
        blocked_raw = str(row.get("Bloqueado") or "").strip().upper()
        blocked = blocked_raw if blocked_raw in {"S", "N"} else "UNKNOWN"
        safe_rows.append(
            {
                "number": number,
                "emission_type": emission_type or "UNKNOWN",
                "blocked": blocked,
            }
        )
    return sorted(safe_rows, key=lambda row: row["number"])


class Command(BaseCommand):
    help = (
        "After the local gate passes, run only WSAA and "
        "FEParamGetPtosVenta in homologation. This command cannot run "
        "FEDummy, another catalog, FECompUltimoAutorizado or emission."
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
                "Empresa o punto de venta candidato no encontrado."
            ) from exc

        gate = evaluate_homologation_readiness(
            company=company,
            point_of_sale=point,
            check_credentials=True,
        )
        if not gate.passed:
            raise CommandError(
                "ARCA_HOMOLOGATION_READINESS_GATE=FAIL reasons="
                + ",".join(gate.error_codes)
            )

        try:
            client = ArcaWsfeClient(
                company=company,
                point_of_sale=point,
            )
            result = client.fetch_points_of_sale()
            rows = _safe_catalog_rows(result.get("values", ()))
        except CommandError:
            raise
        except Exception as exc:
            raise CommandError(
                "La consulta de puntos de venta fallo: "
                + sanitize_sensitive_text(str(exc))
            ) from exc

        self.stdout.write("ARCA_WSFE_POINTS_OF_SALE_PROBE=PASS")
        self.stdout.write(
            "ticket_cache_policy=retain_until_renewal_window"
        )
        self.stdout.write(f"point_count={len(rows)}")
        for index, row in enumerate(rows, start=1):
            self.stdout.write(f"point_{index}_number={row['number']}")
            self.stdout.write(
                f"point_{index}_emission_type={row['emission_type']}"
            )
            self.stdout.write(
                f"point_{index}_blocked={row['blocked']}"
            )
