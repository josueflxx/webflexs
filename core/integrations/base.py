"""Base contracts for outbound integrations."""

from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class IntegrationResult:
    ok: bool
    provider: str
    operation: str
    payload: Dict[str, Any]
    error: str = ""


class BaseIntegrationClient:
    provider_name = "base"

    def send(self, operation: str, payload: Dict[str, Any]) -> IntegrationResult:
        raise NotImplementedError
