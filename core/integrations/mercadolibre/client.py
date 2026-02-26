"""MercadoLibre integration client stub."""

from core.integrations.base import BaseIntegrationClient, IntegrationResult


class MercadoLibreIntegrationClient(BaseIntegrationClient):
    provider_name = "mercadolibre"

    def send(self, operation, payload):
        return IntegrationResult(
            ok=False,
            provider=self.provider_name,
            operation=str(operation or ""),
            payload=dict(payload or {}),
            error="MercadoLibre integration is not configured yet.",
        )
