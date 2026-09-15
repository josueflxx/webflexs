"""Evaluate one exact homologation emission canary with network blocked."""

from unittest.mock import patch

from django.core.management.base import BaseCommand, CommandError

from core.models import FiscalDocument
from core.services.arca_homologation import (
    evaluate_homologation_emission_readiness,
)


def _network_forbidden(*_args, **_kwargs):
    raise AssertionError("Network is forbidden during the emission gate.")


class Command(BaseCommand):
    help = (
        "Validate one exact local homologation Factura A canary with DNS, "
        "sockets and SOAP transport blocked. It never emits."
    )

    def add_arguments(self, parser):
        parser.add_argument("--document-id", type=int, required=True)

    def handle(self, *args, **options):
        del args
        try:
            document = (
                FiscalDocument.objects.select_related("company", "point_of_sale")
                .prefetch_related("items")
                .get(pk=options["document_id"])
            )
        except FiscalDocument.DoesNotExist as exc:
            raise CommandError("Documento fiscal no encontrado.") from exc

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
            result = evaluate_homologation_emission_readiness(
                fiscal_document=document,
                company=document.company,
                point_of_sale=document.point_of_sale,
                phase="prepare",
                check_credentials=True,
            )

        self.stdout.write("ARCA_HOMOLOGATION_EMISSION_NETWORK_GUARD=ACTIVE")
        self.stdout.write("network_used=no")
        self.stdout.write(
            "ARCA_HOMOLOGATION_EMISSION_GATE="
            + ("PASS" if result.passed else "FAIL")
        )
        for error_code in result.error_codes:
            self.stderr.write(f"reason={error_code}")
