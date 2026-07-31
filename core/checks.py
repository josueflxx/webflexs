"""Django system checks for fail-safe ARCA startup configuration."""

from __future__ import annotations

from django.conf import settings
from django.core.checks import Error, Info, Tags, register

from core.services.arca_config import (
    ArcaEnvironment,
    ArcaSecurityConfigurationError,
    configured_arca_environment,
)
from core.services.arca_homologation import (
    evaluate_homologation_readiness,
)


@register(Tags.security)
def arca_security_configuration_check(app_configs, **kwargs):
    del app_configs, kwargs
    messages = []
    try:
        environment = configured_arca_environment()
    except ArcaSecurityConfigurationError as exc:
        return [
            Error(
                str(exc),
                id="arca.E001",
            )
        ]

    activation_requested = any(
        bool(getattr(settings, name, False))
        for name in (
            "ARCA_ENABLED",
            "ARCA_HOMOLOGATION_NETWORK_ENABLED",
            "ARCA_HOMOLOGATION_READ_ENABLED",
            "ARCA_HOMOLOGATION_EMISSION_ENABLED",
            "ARCA_PRODUCTION_ENABLED",
            "READY_ARCA_HOMOLOGACION_READONLY",
            "ARCA_WSASS_AUTHORIZATION_CONFIRMED",
        )
    )
    if environment is ArcaEnvironment.DISABLED and not activation_requested:
        return [
            Info(
                "Integracion ARCA deshabilitada (modo seguro por defecto).",
                id="arca.I001",
            )
        ]
    if environment is ArcaEnvironment.PRODUCTION:
        return [
            Error(
                "Integracion ARCA de produccion bloqueada por politica.",
                id="arca.E002",
            )
        ]

    result = evaluate_homologation_readiness(check_credentials=True)
    for error_code in result.error_codes:
        messages.append(
            Error(
                f"Compuerta ARCA no aprobada ({error_code}).",
                id="arca.E010",
            )
        )

    if not messages:
        messages.append(
            Info(
                "Integracion ARCA configurada exclusivamente para homologacion.",
                id="arca.I002",
            )
        )
    return messages
