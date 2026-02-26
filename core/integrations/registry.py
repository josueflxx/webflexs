"""Simple registry for integration clients."""

from core.integrations.arca.client import ArcaIntegrationClient
from core.integrations.mercadolibre.client import MercadoLibreIntegrationClient


INTEGRATION_CLIENTS = {
    "arca": ArcaIntegrationClient,
    "mercadolibre": MercadoLibreIntegrationClient,
}


def get_integration_client(provider_name):
    provider = str(provider_name or "").strip().lower()
    cls = INTEGRATION_CLIENTS.get(provider)
    if not cls:
        raise ValueError(f"Proveedor de integracion no soportado: {provider_name}")
    return cls()
