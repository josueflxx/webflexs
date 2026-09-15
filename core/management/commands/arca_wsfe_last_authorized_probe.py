"""Fixed-scope, read-only FECompUltimoAutorizado homologation probe."""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.models import Company, FiscalPointOfSale
from core.services.arca_client import ArcaWsfeClient
from core.services.arca_homologation import evaluate_homologation_readiness
from core.services.sensitive_data import sanitize_sensitive_text


EXPECTED_COMPANY_ID = 1
EXPECTED_POINT_OF_SALE_ID = 3
EXPECTED_POINT_OF_SALE_NUMBER = 3
EXPECTED_VOUCHER_TYPE = 1


class Command(BaseCommand):
    help = (
        "After the local gate passes, run only WSAA if required and "
        "FECompUltimoAutorizado for Company 1, POS 3 and Factura A in "
        "homologation. This command cannot emit or query another method."
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
        company_id = options["company_id"]
        point_id = options["point_of_sale_id"]
        if (
            company_id != EXPECTED_COMPANY_ID
            or point_id != EXPECTED_POINT_OF_SALE_ID
        ):
            raise CommandError(
                "El probe tiene alcance fijo: Company 1 y POS ID 3."
            )

        try:
            company = Company.objects.get(pk=company_id)
            point = FiscalPointOfSale.objects.get(
                pk=point_id,
                company=company,
            )
        except (Company.DoesNotExist, FiscalPointOfSale.DoesNotExist) as exc:
            raise CommandError(
                "Empresa o punto de venta autorizado no encontrado."
            ) from exc

        point_number = str(getattr(point, "number", "") or "").strip()
        if (
            not point_number.isdigit()
            or int(point_number) != EXPECTED_POINT_OF_SALE_NUMBER
        ):
            raise CommandError(
                "El POS ID 3 no corresponde al punto de venta autorizado."
            )

        configured_voucher_type = str(
            getattr(settings, "ARCA_DEFAULT_CBTE_TIPO", "") or ""
        ).strip()
        if (
            not configured_voucher_type.isdigit()
            or int(configured_voucher_type) != EXPECTED_VOUCHER_TYPE
        ):
            raise CommandError(
                "El probe requiere Factura A (CbteTipo=1) configurada."
            )

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
            last_authorized = client.fetch_last_authorized_by_type(
                cbte_type=EXPECTED_VOUCHER_TYPE
            )
            if type(last_authorized) is not int or last_authorized < 0:
                raise CommandError(
                    "ARCA devolvio un ultimo autorizado no interpretable."
                )
        except CommandError:
            raise
        except Exception as exc:
            raise CommandError(
                "La consulta del ultimo autorizado fallo: "
                + sanitize_sensitive_text(str(exc))
            ) from exc

        self.stdout.write("ARCA_WSFE_LAST_AUTHORIZED_PROBE=PASS")
        self.stdout.write(
            "ticket_cache_policy=retain_until_renewal_window"
        )
        self.stdout.write(f"company_id={EXPECTED_COMPANY_ID}")
        self.stdout.write(f"point_of_sale_id={EXPECTED_POINT_OF_SALE_ID}")
        self.stdout.write(
            f"point_of_sale_number={EXPECTED_POINT_OF_SALE_NUMBER}"
        )
        self.stdout.write(f"voucher_type={EXPECTED_VOUCHER_TYPE}")
        self.stdout.write("voucher_label=Factura A")
        self.stdout.write(f"last_authorized_number={last_authorized}")
        self.stdout.write("local_state_updated=no")
