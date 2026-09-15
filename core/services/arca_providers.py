"""Provider adapters for the direct, read-only ARCA clients."""

from core.services.arca_client import ArcaWsfeClient
from core.services.arca_taxpayer import ArcaTaxpayerRegistryClient
from core.services.fiscal_provider import (
    FiscalVoucherProvider,
    TaxpayerRegistryProvider,
)


class DirectArcaTaxpayerRegistryProvider(TaxpayerRegistryProvider):
    def __init__(self, *, company, client_factory=ArcaTaxpayerRegistryClient):
        self.client = client_factory(company=company)

    def lookup_taxpayer(self, cuit: str):
        return self.client.lookup_taxpayer(cuit)


class DirectArcaWsfeReadProvider(FiscalVoucherProvider):
    def __init__(self, *, company, point_of_sale, client_factory=ArcaWsfeClient):
        self.client = client_factory(
            company=company,
            point_of_sale=point_of_sale,
        )

    def get_server_status(self):
        return self.client.fetch_service_status()

    def list_points_of_sale(self):
        return self.client.fetch_points_of_sale()

    def get_last_voucher(self, *, doc_type: str) -> int:
        return self.client.fetch_last_authorized_number(doc_type=doc_type)

    def get_voucher(self, *, fiscal_document):
        return self.client.consult_fiscal_document(
            fiscal_document=fiscal_document
        )
