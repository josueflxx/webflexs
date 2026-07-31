"""Strict HTTPS transport for ARCA SOAP calls."""

from __future__ import annotations

import ssl
from dataclasses import dataclass
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import (
    HTTPSHandler,
    HTTPRedirectHandler,
    Request,
    build_opener,
)

from core.services.arca_config import (
    ArcaEndpoint,
    ArcaSecurityConfigurationError,
    validate_endpoint_url,
)


class ArcaTransportError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        possibly_sent: bool,
        status_code: Optional[int] = None,
        response_body: bytes = b"",
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.possibly_sent = possibly_sent
        self.status_code = status_code
        self.response_body = response_body


class _RejectRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ArcaTransportError(
            "ARCA intento redirigir la solicitud; redireccion rechazada.",
            error_code="redirect_rejected",
            possibly_sent=True,
            status_code=int(code),
        )


@dataclass(frozen=True)
class ArcaTransportResponse:
    text: str
    status_code: int


class StrictArcaSoapTransport:
    """HTTPS-only transport that never follows redirects."""

    def __init__(self, *, timeout: int = 30, opener=None) -> None:
        self.timeout = max(int(timeout or 30), 5)
        if opener is None:
            context = ssl.create_default_context()
            opener = build_opener(
                HTTPSHandler(context=context),
                _RejectRedirectHandler(),
            )
        self._opener = opener

    def post(
        self,
        *,
        endpoint: ArcaEndpoint,
        soap_action: str,
        envelope: bytes,
        possibly_sent_on_error: bool,
    ) -> ArcaTransportResponse:
        try:
            validated_url = validate_endpoint_url(endpoint.url, endpoint)
        except ArcaSecurityConfigurationError:
            raise

        request = Request(
            url=validated_url,
            data=envelope,
            method="POST",
            headers={
                "Content-Type": "text/xml; charset=utf-8",
                "SOAPAction": str(soap_action or ""),
            },
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                status = int(getattr(response, "status", 200) or 200)
                final_url = str(getattr(response, "url", "") or validated_url)
                if final_url != validated_url:
                    raise ArcaTransportError(
                        "La respuesta ARCA provino de un endpoint diferente.",
                        error_code="redirect_rejected",
                        possibly_sent=possibly_sent_on_error,
                        status_code=status,
                    )
                body = response.read()
                return ArcaTransportResponse(
                    text=bytes(body or b"").decode("utf-8", errors="replace"),
                    status_code=status,
                )
        except ArcaTransportError as exc:
            if exc.possibly_sent == possibly_sent_on_error:
                raise
            raise ArcaTransportError(
                str(exc),
                error_code=exc.error_code,
                possibly_sent=possibly_sent_on_error,
                status_code=exc.status_code,
                response_body=exc.response_body,
            ) from exc
        except HTTPError as exc:
            body = b""
            try:
                body = bytes(exc.read() or b"")
            except Exception:
                body = b""
            error_code = (
                "redirect_rejected"
                if 300 <= int(exc.code) < 400
                else f"http_{int(exc.code)}"
            )
            raise ArcaTransportError(
                "ARCA devolvio un error HTTP.",
                error_code=error_code,
                possibly_sent=possibly_sent_on_error,
                status_code=int(exc.code),
                response_body=body,
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ArcaTransportError(
                "No se pudo completar la comunicacion segura con ARCA.",
                error_code="network_error",
                possibly_sent=possibly_sent_on_error,
            ) from exc
        except Exception as exc:
            raise ArcaTransportError(
                "Fallo inesperado en el transporte SOAP de ARCA.",
                error_code="transport_error",
                possibly_sent=possibly_sent_on_error,
            ) from exc
