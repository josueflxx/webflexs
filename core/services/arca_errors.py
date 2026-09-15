"""Shared, sanitized errors for direct ARCA clients."""

from __future__ import annotations

from typing import Any, Dict, Optional


class ArcaClientError(Exception):
    """Base error for direct ARCA integrations."""


class ArcaConfigurationError(ArcaClientError):
    """Missing or unsafe ARCA configuration detected before network I/O."""


class ArcaTemporaryError(ArcaClientError):
    """Technical ARCA/transport error with an explicit delivery boundary."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "temporary_error",
        request_payload: Optional[Dict[str, Any]] = None,
        response_payload: Optional[Dict[str, Any]] = None,
        possibly_sent: bool = False,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.request_payload = request_payload or {}
        self.response_payload = response_payload or {}
        self.possibly_sent = bool(possibly_sent)

