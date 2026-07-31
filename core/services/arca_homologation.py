"""Fail-closed guardrails for ARCA homologation read-only access.

This module performs no network I/O. It centralizes the explicit activation
hierarchy that must pass before the SOAP client can contact WSAA or WSFEv1.
Emission remains blocked in code for the whole pre-emission stage.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from django.conf import settings

from accounts.fiscal_identity import is_valid_cuit, normalize_fiscal_document
from core.services.arca_config import (
    ArcaEndpointKind,
    ArcaEnvironment,
    ArcaSecurityConfigurationError,
    configured_arca_environment,
    resolve_arca_endpoint,
)
from core.services.arca_credentials import (
    ArcaCredentialError,
    ArcaCredentialMetadata,
    resolve_credential_spec,
    validate_credential_offline,
)
from core.services.arca_ticket_cache import (
    ArcaTicketCacheError,
    configured_arca_cache_prefix,
    inspect_arca_cache_configuration,
)


class ArcaHomologationReadinessError(RuntimeError):
    """A sanitized readiness failure raised before network I/O."""

    def __init__(self, message: str, *, error_code: str) -> None:
        super().__init__(message)
        self.error_code = error_code


class ARCAEmissionDisabledError(ArcaHomologationReadinessError):
    """Raised whenever this stage is asked to construct an emission request."""


@dataclass(frozen=True)
class ArcaHomologationGateResult:
    passed: bool
    error_codes: tuple[str, ...]
    environment: str
    wsaa_host: str
    wsfe_host: str
    service_configured: bool
    cuit_configured: bool
    point_of_sale_configured: bool
    voucher_type_configured: bool
    wsass_authorization_confirmed: bool
    cache_configured: bool
    credential_metadata: Optional[ArcaCredentialMetadata] = None


def _flag(name: str, default: bool) -> bool:
    value = getattr(settings, name, default)
    if isinstance(value, bool):
        return value
    normalized = str(value or "").lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ArcaHomologationReadinessError(
        "Bandera ARCA invalida.",
        error_code=f"{name.lower()}_invalid",
    )


def _configured_endpoint_values() -> tuple[str, str, str]:
    return (
        str(getattr(settings, "ARCA_WSAA_URL", "") or ""),
        str(getattr(settings, "ARCA_WSFE_URL", "") or ""),
        str(getattr(settings, "ARCA_WSFE_WSDL", "") or ""),
    )


def _outside_repository(path_value: str) -> bool:
    if not path_value:
        return False
    try:
        path = Path(path_value).expanduser().resolve(strict=False)
        repository = Path(settings.BASE_DIR).resolve(strict=True)
        path.relative_to(repository)
    except ValueError:
        return True
    except (OSError, RuntimeError):
        return False
    return False


def evaluate_homologation_readiness(
    *,
    company: Any = None,
    point_of_sale: Any = None,
    check_credentials: bool = True,
) -> ArcaHomologationGateResult:
    """Return sanitized gate state without performing network I/O."""

    errors: list[str] = []
    try:
        environment = configured_arca_environment()
    except ArcaSecurityConfigurationError as exc:
        environment = ArcaEnvironment.DISABLED
        errors.append(exc.error_code)

    expected_wsaa = resolve_arca_endpoint(
        ArcaEnvironment.HOMOLOGATION,
        ArcaEndpointKind.WSAA,
    )
    expected_wsfe = resolve_arca_endpoint(
        ArcaEnvironment.HOMOLOGATION,
        ArcaEndpointKind.WSFE,
    )
    wsaa_url, wsfe_url, wsfe_wsdl = _configured_endpoint_values()

    flag_values: dict[str, bool] = {}
    for name, default in (
        ("ARCA_ENABLED", False),
        ("ARCA_HOMOLOGATION_NETWORK_ENABLED", False),
        ("ARCA_HOMOLOGATION_READ_ENABLED", False),
        ("ARCA_HOMOLOGATION_EMISSION_ENABLED", False),
        ("ARCA_PRODUCTION_ENABLED", False),
        ("READY_ARCA_HOMOLOGACION_READONLY", False),
        ("ARCA_WSASS_AUTHORIZATION_CONFIRMED", False),
        ("ARCA_TLS_VERIFY", True),
        ("ARCA_REDACT_SECRETS", True),
        ("ARCA_TOKEN_CACHE_ENABLED", False),
    ):
        try:
            flag_values[name] = _flag(name, default)
        except ArcaHomologationReadinessError as exc:
            errors.append(exc.error_code)
            flag_values[name] = default

    if not flag_values["ARCA_ENABLED"]:
        errors.append("integration_disabled")
    if not flag_values["ARCA_HOMOLOGATION_NETWORK_ENABLED"]:
        errors.append("homologation_network_disabled")
    if not flag_values["ARCA_HOMOLOGATION_READ_ENABLED"]:
        errors.append("homologation_read_disabled")
    if flag_values["ARCA_HOMOLOGATION_EMISSION_ENABLED"]:
        errors.append("homologation_emission_must_remain_disabled")
    if flag_values["ARCA_PRODUCTION_ENABLED"]:
        errors.append("production_enabled")
    if not flag_values["READY_ARCA_HOMOLOGACION_READONLY"]:
        errors.append("user_readiness_signal_missing")
    if not flag_values["ARCA_WSASS_AUTHORIZATION_CONFIRMED"]:
        errors.append("wsass_authorization_not_confirmed")
    if not flag_values["ARCA_TLS_VERIFY"]:
        errors.append("tls_verification_disabled")
    if not flag_values["ARCA_REDACT_SECRETS"]:
        errors.append("secret_redaction_disabled")
    if not flag_values["ARCA_TOKEN_CACHE_ENABLED"]:
        errors.append("ticket_cache_disabled")

    if environment is not ArcaEnvironment.HOMOLOGATION:
        errors.append(
            "production_blocked"
            if environment is ArcaEnvironment.PRODUCTION
            else "homologation_environment_required"
        )

    if not wsaa_url:
        errors.append("wsaa_endpoint_missing")
    elif wsaa_url != expected_wsaa.url:
        errors.append("wsaa_endpoint_not_allowlisted")
    if not wsfe_url:
        errors.append("wsfe_endpoint_missing")
    elif wsfe_url != expected_wsfe.url:
        errors.append("wsfe_endpoint_not_allowlisted")
    if not wsfe_wsdl:
        errors.append("wsfe_wsdl_missing")
    elif wsfe_wsdl != f"{expected_wsfe.url}?WSDL":
        errors.append("wsfe_wsdl_not_allowlisted")

    service = str(getattr(settings, "ARCA_SERVICE_ID", "") or "")
    service_configured = bool(
        service
        and len(service) <= 80
        and re.fullmatch(r"[A-Za-z0-9_.:-]+", service)
    )
    if not service:
        errors.append("service_id_missing")
    elif not service_configured:
        errors.append("service_id_invalid")

    raw_cuit = str(getattr(settings, "ARCA_CUIT", "") or "")
    cuit = normalize_fiscal_document(raw_cuit)
    cuit_configured = is_valid_cuit(cuit)
    if not raw_cuit:
        errors.append("issuer_cuit_missing")
    elif not cuit_configured:
        errors.append("issuer_cuit_invalid")
    company_cuit = normalize_fiscal_document(getattr(company, "cuit", ""))
    if company_cuit and cuit and company_cuit != cuit:
        errors.append("issuer_cuit_company_mismatch")

    point_value = str(getattr(settings, "ARCA_PTO_VTA", "") or "")
    point_of_sale_configured = bool(
        point_value.isdigit()
        and 0 < int(point_value) <= 999999
    )
    if not point_value:
        errors.append("point_of_sale_missing")
    elif not point_of_sale_configured:
        errors.append("point_of_sale_invalid")
    model_point = str(getattr(point_of_sale, "number", "") or "").strip()
    if (
        model_point.isdigit()
        and point_value.isdigit()
        and int(model_point) != int(point_value)
    ):
        errors.append("point_of_sale_mismatch")
    point_environment = str(
        getattr(point_of_sale, "environment", "") or ""
    ).strip()
    if point_environment and point_environment != ArcaEnvironment.HOMOLOGATION.value:
        errors.append("point_of_sale_environment_mismatch")

    voucher_value = str(
        getattr(settings, "ARCA_DEFAULT_CBTE_TIPO", "") or ""
    )
    voucher_type_configured = bool(
        voucher_value.isdigit()
        and 0 < int(voucher_value) <= 999
    )
    if not voucher_value:
        errors.append("voucher_type_missing")
    elif not voucher_type_configured:
        errors.append("voucher_type_invalid")

    cache_configuration = inspect_arca_cache_configuration()
    if flag_values["ARCA_TOKEN_CACHE_ENABLED"]:
        configured_cache_backend = str(
            getattr(settings, "ARCA_TOKEN_CACHE_BACKEND", "") or ""
        )
        if configured_cache_backend not in {"", "redis", "memcached"}:
            errors.append("cache_backend_setting_invalid")
        if not cache_configuration.valid:
            errors.append(cache_configuration.error_code)
        try:
            configured_arca_cache_prefix()
        except ArcaTicketCacheError as exc:
            errors.append(exc.error_code)
    # WebFlexs uses the configured shared Django cache (normally Redis).
    # Persisting Token/Sign in a local file is intentionally unsupported.
    if str(getattr(settings, "ARCA_TOKEN_CACHE_PATH", "") or "").strip():
        errors.append("file_ticket_cache_forbidden")
    if str(
        getattr(settings, "ARCA_PRIVATE_KEY_PASSPHRASE_FILE", "") or ""
    ).strip():
        errors.append("passphrase_file_not_supported")

    cert_path = str(getattr(settings, "ARCA_CERT_PATH", "") or "")
    key_path = str(
        getattr(settings, "ARCA_PRIVATE_KEY_PATH", "") or ""
    )
    if not cert_path:
        errors.append("certificate_path_missing")
    elif not _outside_repository(cert_path):
        errors.append("certificate_path_forbidden")
    if not key_path:
        errors.append("private_key_path_missing")
    elif not _outside_repository(key_path):
        errors.append("private_key_path_forbidden")

    now = datetime.now(timezone.utc)
    if not (2024 <= now.year <= 2100):
        errors.append("system_clock_unreasonable")

    credential_metadata = None
    if check_credentials and not any(
        code in errors
        for code in (
            "certificate_path_missing_or_forbidden",
            "certificate_path_missing",
            "certificate_path_forbidden",
            "private_key_path_missing",
            "private_key_path_forbidden",
            "issuer_cuit_missing",
            "issuer_cuit_invalid",
        )
    ):
        try:
            spec = resolve_credential_spec(
                company=company,
                environment=ArcaEnvironment.HOMOLOGATION,
            )
            credential_metadata = validate_credential_offline(
                spec,
                openssl_bin=str(
                    getattr(settings, "ARCA_OPENSSL_BIN", "openssl")
                ),
            )
        except ArcaCredentialError as exc:
            errors.append(exc.error_code)

    unique_errors = tuple(dict.fromkeys(errors))
    return ArcaHomologationGateResult(
        passed=not unique_errors,
        error_codes=unique_errors,
        environment=environment.value,
        wsaa_host=expected_wsaa.host,
        wsfe_host=expected_wsfe.host,
        service_configured=service_configured,
        cuit_configured=cuit_configured,
        point_of_sale_configured=point_of_sale_configured,
        voucher_type_configured=voucher_type_configured,
        wsass_authorization_confirmed=flag_values[
            "ARCA_WSASS_AUTHORIZATION_CONFIRMED"
        ],
        cache_configured=cache_configuration.valid,
        credential_metadata=credential_metadata,
    )


def require_homologation_read_access(
    *,
    company: Any = None,
    point_of_sale: Any = None,
) -> ArcaHomologationGateResult:
    """Fail before network I/O unless every read-only guard is explicit."""

    result = evaluate_homologation_readiness(
        company=company,
        point_of_sale=point_of_sale,
        check_credentials=False,
    )
    if not result.passed:
        raise ArcaHomologationReadinessError(
            "Compuerta ARCA de homologacion no aprobada.",
            error_code=result.error_codes[0],
        )
    return result


def block_arca_emission() -> None:
    """Hard block for FECAESolicitar during the read-only homologation stage."""

    raise ARCAEmissionDisabledError(
        "La emision ARCA permanece bloqueada durante esta etapa.",
        error_code="arca_emission_disabled",
    )
