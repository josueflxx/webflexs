"""ARCA integration client stub.

Use this client as extension point when ARCA sync is enabled.
"""

from core.integrations.base import BaseIntegrationClient, IntegrationResult


class ArcaIntegrationClient(BaseIntegrationClient):
    provider_name = "arca"

    def send(self, operation, payload):
        return IntegrationResult(
            ok=False,
            provider=self.provider_name,
            operation=str(operation or ""),
            payload=dict(payload or {}),
            error="ARCA integration is not configured yet.",
        )
