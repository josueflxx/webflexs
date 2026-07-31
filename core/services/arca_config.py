"""Closed, fail-safe configuration for the ARCA integration.

Endpoint URLs intentionally live in this module instead of environment
variables.  Changing an endpoint therefore requires a reviewed code change.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping
from urllib.parse import urlsplit

from django.conf import settings


class ArcaSecurityConfigurationError(ValueError):
    """Raised before any I/O when ARCA configuration is unsafe."""

    def __init__(self, message: str, *, error_code: str = "arca_configuration") -> None:
        super().__init__(message)
        self.error_code = error_code


class ArcaEnvironment(str, Enum):
    DISABLED = "disabled"
    HOMOLOGATION = "homologation"
    PRODUCTION = "production"


class ArcaEndpointKind(str, Enum):
    WSAA = "wsaa"
    WSFE = "wsfe"


@dataclass(frozen=True)
class ArcaEndpoint:
    kind: ArcaEndpointKind
    environment: ArcaEnvironment
    url: str
    scheme: str
    host: str
    path: str
    port: int = 443


_ENDPOINTS: Mapping[ArcaEnvironment, Mapping[ArcaEndpointKind, ArcaEndpoint]] = {
    ArcaEnvironment.HOMOLOGATION: {
        ArcaEndpointKind.WSAA: ArcaEndpoint(
            kind=ArcaEndpointKind.WSAA,
            environment=ArcaEnvironment.HOMOLOGATION,
            url="https://wsaahomo.afip.gov.ar/ws/services/LoginCms",
            scheme="https",
            host="wsaahomo.afip.gov.ar",
            path="/ws/services/LoginCms",
        ),
        ArcaEndpointKind.WSFE: ArcaEndpoint(
            kind=ArcaEndpointKind.WSFE,
            environment=ArcaEnvironment.HOMOLOGATION,
            url="https://wswhomo.afip.gov.ar/wsfev1/service.asmx",
            scheme="https",
            host="wswhomo.afip.gov.ar",
            path="/wsfev1/service.asmx",
        ),
    },
    # Production endpoints are deliberately present only so validation can
    # identify and reject them.  resolve_arca_endpoint never returns them.
    ArcaEnvironment.PRODUCTION: {
        ArcaEndpointKind.WSAA: ArcaEndpoint(
            kind=ArcaEndpointKind.WSAA,
            environment=ArcaEnvironment.PRODUCTION,
            url="https://wsaa.afip.gov.ar/ws/services/LoginCms",
            scheme="https",
            host="wsaa.afip.gov.ar",
            path="/ws/services/LoginCms",
        ),
        ArcaEndpointKind.WSFE: ArcaEndpoint(
            kind=ArcaEndpointKind.WSFE,
            environment=ArcaEnvironment.PRODUCTION,
            url="https://servicios1.afip.gov.ar/wsfev1/service.asmx",
            scheme="https",
            host="servicios1.afip.gov.ar",
            path="/wsfev1/service.asmx",
        ),
    },
}


def parse_arca_environment(
    raw_value,
    *,
    allow_internal_value: bool = False,
) -> ArcaEnvironment:
    if raw_value is None or raw_value == "":
        value = ArcaEnvironment.DISABLED.value
    else:
        value = str(raw_value)
    if value == "homologacion":
        return ArcaEnvironment.HOMOLOGATION
    if allow_internal_value and value == ArcaEnvironment.HOMOLOGATION.value:
        return ArcaEnvironment.HOMOLOGATION
    try:
        parsed = ArcaEnvironment(value)
    except ValueError as exc:
        raise ArcaSecurityConfigurationError(
            "Entorno ARCA invalido. Valores permitidos: disabled, homologacion.",
            error_code="invalid_environment",
        ) from exc
    if parsed is ArcaEnvironment.HOMOLOGATION:
        raise ArcaSecurityConfigurationError(
            "Use homologacion como valor externo de ARCA_ENVIRONMENT.",
            error_code="invalid_environment",
        )
    return parsed


def configured_arca_environment() -> ArcaEnvironment:
    return parse_arca_environment(getattr(settings, "ARCA_ENVIRONMENT", "disabled"))


def require_homologation_environment(*, point_environment=None) -> ArcaEnvironment:
    configured = configured_arca_environment()
    if configured is ArcaEnvironment.DISABLED:
        raise ArcaSecurityConfigurationError(
            "Integracion ARCA deshabilitada.",
            error_code="integration_disabled",
        )
    if configured is ArcaEnvironment.PRODUCTION:
        raise ArcaSecurityConfigurationError(
            "Integracion ARCA de produccion bloqueada por politica.",
            error_code="production_blocked",
        )

    if point_environment is not None:
        point = parse_arca_environment(
            point_environment,
            allow_internal_value=True,
        )
        if point is not configured:
            raise ArcaSecurityConfigurationError(
                "El entorno del punto de venta no coincide con el entorno ARCA habilitado.",
                error_code="point_environment_mismatch",
            )
    return configured


def validate_endpoint_url(url: str, expected: ArcaEndpoint) -> str:
    """Validate every URL component against the closed endpoint definition."""

    try:
        parsed = urlsplit(str(url or ""))
    except ValueError as exc:
        raise ArcaSecurityConfigurationError(
            "Endpoint ARCA invalido.",
            error_code="invalid_endpoint",
        ) from exc

    try:
        parsed_port = parsed.port
    except ValueError as exc:
        raise ArcaSecurityConfigurationError(
            "Puerto ARCA invalido.",
            error_code="invalid_endpoint_port",
        ) from exc

    if parsed.scheme.lower() != expected.scheme:
        raise ArcaSecurityConfigurationError(
            "El endpoint ARCA debe usar HTTPS.",
            error_code="endpoint_requires_https",
        )
    if parsed.username is not None or parsed.password is not None:
        raise ArcaSecurityConfigurationError(
            "El endpoint ARCA no admite credenciales en la URL.",
            error_code="endpoint_userinfo_forbidden",
        )
    if (parsed.hostname or "").lower() != expected.host:
        raise ArcaSecurityConfigurationError(
            "Host ARCA no permitido.",
            error_code="endpoint_host_forbidden",
        )
    if parsed_port not in (None, expected.port):
        raise ArcaSecurityConfigurationError(
            "Puerto ARCA no permitido.",
            error_code="endpoint_port_forbidden",
        )
    if parsed.path != expected.path:
        raise ArcaSecurityConfigurationError(
            "Ruta ARCA no permitida.",
            error_code="endpoint_path_forbidden",
        )
    if parsed.query or parsed.fragment:
        raise ArcaSecurityConfigurationError(
            "El endpoint ARCA no admite query ni fragment.",
            error_code="endpoint_suffix_forbidden",
        )
    if parsed.netloc.lower() not in {expected.host, f"{expected.host}:{expected.port}"}:
        raise ArcaSecurityConfigurationError(
            "Autoridad ARCA no permitida.",
            error_code="endpoint_authority_forbidden",
        )
    return expected.url


def resolve_arca_endpoint(
    environment: ArcaEnvironment,
    kind: ArcaEndpointKind,
) -> ArcaEndpoint:
    if environment is ArcaEnvironment.DISABLED:
        raise ArcaSecurityConfigurationError(
            "Integracion ARCA deshabilitada.",
            error_code="integration_disabled",
        )
    if environment is ArcaEnvironment.PRODUCTION:
        raise ArcaSecurityConfigurationError(
            "Endpoints ARCA de produccion bloqueados por politica.",
            error_code="production_blocked",
        )
    try:
        endpoint = _ENDPOINTS[environment][kind]
    except KeyError as exc:
        raise ArcaSecurityConfigurationError(
            "Endpoint ARCA no configurado.",
            error_code="endpoint_not_configured",
        ) from exc
    validate_endpoint_url(endpoint.url, endpoint)
    return endpoint


def is_production_endpoint_url(url: str) -> bool:
    candidate = str(url or "").strip()
    return any(
        candidate == endpoint.url
        for endpoint in _ENDPOINTS[ArcaEnvironment.PRODUCTION].values()
    )
