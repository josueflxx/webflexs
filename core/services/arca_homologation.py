"""Fail-closed guardrails for ARCA homologation access.

This module performs no network I/O. It centralizes the explicit activation
hierarchy that must pass before the SOAP client can contact WSAA or WSFEv1.
Emission requires an exact, expiring, one-shot approval bound to one immutable
local fiscal document; production remains blocked unconditionally.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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


@dataclass(frozen=True)
class ArcaTaxpayerRegistryGateResult:
    passed: bool
    error_codes: tuple[str, ...]
    environment: str
    service_configured: bool
    endpoint_configured: bool
    user_signal: bool


@dataclass(frozen=True)
class ArcaHomologationEmissionAuthorization:
    """Non-secret capability for one exact homologation authorization attempt."""

    company_id: int
    point_of_sale_id: int
    document_id: int
    snapshot_hash: str
    approved_attempt: int
    expires_at: datetime
    fingerprint: str


@dataclass(frozen=True)
class ArcaHomologationEmissionGateResult:
    passed: bool
    error_codes: tuple[str, ...]
    authorization: Optional[ArcaHomologationEmissionAuthorization] = None


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
    _require_wsfe: bool = True,
    _emission_mode: bool = False,
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
        ("READY_ARCA_HOMOLOGACION_EMISSION", False),
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
    if _emission_mode:
        if not flag_values["ARCA_HOMOLOGATION_EMISSION_ENABLED"]:
            errors.append("homologation_emission_disabled")
        if not flag_values["READY_ARCA_HOMOLOGACION_EMISSION"]:
            errors.append("user_emission_readiness_signal_missing")
    elif flag_values["ARCA_HOMOLOGATION_EMISSION_ENABLED"]:
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
    if _require_wsfe:
        if not wsfe_url:
            errors.append("wsfe_endpoint_missing")
        elif wsfe_url != expected_wsfe.url:
            errors.append("wsfe_endpoint_not_allowlisted")
        if not wsfe_wsdl:
            errors.append("wsfe_wsdl_missing")
        elif wsfe_wsdl != f"{expected_wsfe.url}?WSDL":
            errors.append("wsfe_wsdl_not_allowlisted")

    service = str(
        getattr(settings, "ARCA_WSFE_SERVICE_ID", "")
        or getattr(settings, "ARCA_SERVICE_ID", "")
        or ""
    )
    service_configured = service == "wsfe"
    if _require_wsfe:
        if not service:
            errors.append("service_id_missing")
        elif not service_configured:
            errors.append("service_id_not_allowlisted")

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
    if _require_wsfe:
        if not point_value:
            errors.append("point_of_sale_missing")
        elif not point_of_sale_configured:
            errors.append("point_of_sale_invalid")
    model_point = str(getattr(point_of_sale, "number", "") or "").strip()
    if (
        _require_wsfe
        and model_point.isdigit()
        and point_value.isdigit()
        and int(model_point) != int(point_value)
    ):
        errors.append("point_of_sale_mismatch")
    point_environment = str(
        getattr(point_of_sale, "environment", "") or ""
    ).strip()
    if (
        _require_wsfe
        and point_environment
        and point_environment != ArcaEnvironment.HOMOLOGATION.value
    ):
        errors.append("point_of_sale_environment_mismatch")

    voucher_value = str(
        getattr(settings, "ARCA_DEFAULT_CBTE_TIPO", "") or ""
    )
    voucher_type_configured = bool(
        voucher_value.isdigit()
        and 0 < int(voucher_value) <= 999
    )
    if _require_wsfe:
        if not voucher_value:
            errors.append("voucher_type_missing")
        elif not voucher_type_configured:
            errors.append("voucher_type_invalid")

    cache_configuration = inspect_arca_cache_configuration()
    if flag_values["ARCA_TOKEN_CACHE_ENABLED"]:
        configured_cache_backend = str(
            getattr(settings, "ARCA_TOKEN_CACHE_BACKEND", "") or ""
        )
        if configured_cache_backend not in {"", "redis", "memcached", "locmem"}:
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

    expected_subject_cn = str(
        getattr(settings, "ARCA_EXPECTED_CERT_SUBJECT_CN", "") or ""
    ).strip()
    expected_issuer_cn = str(
        getattr(settings, "ARCA_EXPECTED_CERT_ISSUER_CN", "") or ""
    ).strip()
    if not expected_subject_cn:
        errors.append("certificate_subject_cn_missing")
    elif len(expected_subject_cn) > 120 or any(
        character in expected_subject_cn for character in "\r\n"
    ):
        errors.append("certificate_subject_cn_invalid")
    if not expected_issuer_cn:
        errors.append("certificate_issuer_cn_missing")
    elif len(expected_issuer_cn) > 120 or any(
        character in expected_issuer_cn for character in "\r\n"
    ):
        errors.append("certificate_issuer_cn_invalid")

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


def _positive_setting(name: str, errors: list[str]) -> Optional[int]:
    raw = str(getattr(settings, name, "") or "").strip()
    prefix = name.lower()
    if not raw:
        errors.append(f"{prefix}_missing")
        return None
    if not raw.isdigit() or int(raw) <= 0:
        errors.append(f"{prefix}_invalid")
        return None
    return int(raw)


def _approval_expiration(errors: list[str]) -> Optional[datetime]:
    raw = str(
        getattr(
            settings,
            "ARCA_HOMOLOGATION_EMISSION_APPROVAL_EXPIRES_AT",
            "",
        )
        or ""
    ).strip()
    if not raw:
        errors.append("arca_homologation_emission_approval_expires_at_missing")
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        errors.append("arca_homologation_emission_approval_expires_at_invalid")
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        errors.append("arca_homologation_emission_approval_expires_at_timezone_missing")
        return None
    parsed = parsed.astimezone(timezone.utc)
    now = datetime.now(timezone.utc)
    if parsed <= now:
        errors.append("arca_homologation_emission_approval_expired")
    if parsed > now + timedelta(hours=2):
        errors.append("arca_homologation_emission_approval_window_too_large")
    return parsed


def _authorization_fingerprint(
    *,
    company_id: int,
    point_of_sale_id: int,
    document_id: int,
    snapshot_hash: str,
    approved_attempt: int,
    expires_at: datetime,
) -> str:
    canonical = "|".join(
        (
            str(company_id),
            str(point_of_sale_id),
            str(document_id),
            snapshot_hash,
            str(approved_attempt),
            expires_at.isoformat(),
        )
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def evaluate_homologation_emission_readiness(
    *,
    fiscal_document: Any,
    company: Any,
    point_of_sale: Any,
    phase: str = "prepare",
    authorization: Optional[ArcaHomologationEmissionAuthorization] = None,
    check_credentials: bool = True,
) -> ArcaHomologationEmissionGateResult:
    """Evaluate one exact Factura A canary without performing network I/O."""

    errors = list(
        evaluate_homologation_readiness(
            company=company,
            point_of_sale=point_of_sale,
            check_credentials=check_credentials,
            _emission_mode=True,
        ).error_codes
    )
    if phase not in {"prepare", "dispatch"}:
        errors.append("homologation_emission_phase_invalid")

    company_id = _positive_setting(
        "ARCA_HOMOLOGATION_EMISSION_COMPANY_ID", errors
    )
    point_of_sale_id = _positive_setting(
        "ARCA_HOMOLOGATION_EMISSION_POINT_OF_SALE_ID", errors
    )
    document_id = _positive_setting(
        "ARCA_HOMOLOGATION_EMISSION_DOCUMENT_ID", errors
    )
    approved_attempt = _positive_setting(
        "ARCA_HOMOLOGATION_EMISSION_APPROVED_ATTEMPT", errors
    )

    configured_hash = str(
        getattr(settings, "ARCA_HOMOLOGATION_EMISSION_SNAPSHOT_HASH", "")
        or ""
    ).strip().lower()
    if not configured_hash:
        errors.append("arca_homologation_emission_snapshot_hash_missing")
    elif not re.fullmatch(r"[0-9a-f]{64}", configured_hash):
        errors.append("arca_homologation_emission_snapshot_hash_invalid")

    expires_at = _approval_expiration(errors)
    candidate = None
    if all(
        value is not None
        for value in (
            company_id,
            point_of_sale_id,
            document_id,
            approved_attempt,
            expires_at,
        )
    ) and re.fullmatch(r"[0-9a-f]{64}", configured_hash):
        candidate = ArcaHomologationEmissionAuthorization(
            company_id=company_id,
            point_of_sale_id=point_of_sale_id,
            document_id=document_id,
            snapshot_hash=configured_hash,
            approved_attempt=approved_attempt,
            expires_at=expires_at,
            fingerprint=_authorization_fingerprint(
                company_id=company_id,
                point_of_sale_id=point_of_sale_id,
                document_id=document_id,
                snapshot_hash=configured_hash,
                approved_attempt=approved_attempt,
                expires_at=expires_at,
            ),
        )

    model_company_id = getattr(company, "pk", None) or getattr(company, "id", None)
    model_point_id = getattr(point_of_sale, "pk", None) or getattr(
        point_of_sale, "id", None
    )
    model_document_id = getattr(fiscal_document, "pk", None) or getattr(
        fiscal_document, "id", None
    )
    if company_id is not None and model_company_id != company_id:
        errors.append("homologation_emission_company_mismatch")
    if point_of_sale_id is not None and model_point_id != point_of_sale_id:
        errors.append("homologation_emission_point_of_sale_mismatch")
    if document_id is not None and model_document_id != document_id:
        errors.append("homologation_emission_document_mismatch")
    if getattr(fiscal_document, "company_id", None) != model_company_id:
        errors.append("homologation_emission_document_company_mismatch")
    if getattr(fiscal_document, "point_of_sale_id", None) != model_point_id:
        errors.append("homologation_emission_document_point_of_sale_mismatch")
    if getattr(point_of_sale, "company_id", None) != model_company_id:
        errors.append("homologation_emission_point_company_mismatch")

    document_hash = str(getattr(fiscal_document, "snapshot_hash", "") or "").lower()
    if not document_hash or document_hash != configured_hash:
        errors.append("homologation_emission_snapshot_mismatch")
    if str(getattr(fiscal_document, "issue_mode", "") or "") != "arca_wsfe":
        errors.append("homologation_emission_issue_mode_invalid")
    if str(getattr(fiscal_document, "doc_type", "") or "") != "FA":
        errors.append("homologation_emission_voucher_not_factura_a")
    if str(getattr(settings, "ARCA_DEFAULT_CBTE_TIPO", "") or "") != "1":
        errors.append("homologation_emission_cbte_type_not_factura_a")
    if str(getattr(fiscal_document, "environment_snapshot", "") or "") != "homologation":
        errors.append("homologation_emission_document_environment_invalid")
    if str(getattr(point_of_sale, "environment", "") or "") != "homologation":
        errors.append("homologation_emission_point_environment_invalid")
    if not bool(getattr(point_of_sale, "is_active", False)):
        errors.append("homologation_emission_point_inactive")

    try:
        authorize_attempts = fiscal_document.emission_attempts.filter(
            operation="authorize"
        ).count()
    except (AttributeError, TypeError, ValueError):
        authorize_attempts = None
        errors.append("homologation_emission_attempt_history_unavailable")

    status = str(getattr(fiscal_document, "status", "") or "")
    number = getattr(fiscal_document, "number", None)
    if phase == "prepare":
        if status != "ready_to_issue" or number is not None:
            errors.append("homologation_emission_document_not_ready")
        if (
            authorize_attempts is not None
            and approved_attempt is not None
            and authorize_attempts != approved_attempt - 1
        ):
            errors.append("homologation_emission_attempt_not_approved")
    elif phase == "dispatch":
        if status != "submitting" or not isinstance(number, int) or number <= 0:
            errors.append("homologation_emission_document_not_submitting")
        if (
            authorize_attempts is not None
            and approved_attempt is not None
            and authorize_attempts != approved_attempt
        ):
            errors.append("homologation_emission_attempt_not_approved")
        if authorization is None or candidate is None or authorization != candidate:
            errors.append("homologation_emission_capability_mismatch")

    unique_errors = tuple(dict.fromkeys(errors))
    return ArcaHomologationEmissionGateResult(
        passed=not unique_errors,
        error_codes=unique_errors,
        authorization=candidate if not unique_errors else None,
    )


def require_homologation_emission_access(
    *,
    fiscal_document: Any,
    company: Any,
    point_of_sale: Any,
    check_credentials: bool = True,
) -> ArcaHomologationEmissionAuthorization:
    result = evaluate_homologation_emission_readiness(
        fiscal_document=fiscal_document,
        company=company,
        point_of_sale=point_of_sale,
        phase="prepare",
        check_credentials=check_credentials,
    )
    if not result.passed or result.authorization is None:
        raise ARCAEmissionDisabledError(
            "Compuerta de emision ARCA de homologacion no aprobada.",
            error_code=result.error_codes[0] if result.error_codes else "arca_emission_disabled",
        )
    return result.authorization


def require_homologation_emission_dispatch_access(
    *,
    fiscal_document: Any,
    company: Any,
    point_of_sale: Any,
    authorization: Optional[ArcaHomologationEmissionAuthorization],
) -> ArcaHomologationEmissionAuthorization:
    result = evaluate_homologation_emission_readiness(
        fiscal_document=fiscal_document,
        company=company,
        point_of_sale=point_of_sale,
        phase="dispatch",
        authorization=authorization,
        check_credentials=False,
    )
    if not result.passed or result.authorization is None:
        raise ARCAEmissionDisabledError(
            "Compuerta de despacho ARCA de homologacion no aprobada.",
            error_code=result.error_codes[0] if result.error_codes else "arca_emission_disabled",
        )
    return result.authorization


def require_homologation_recovery_access(
    *,
    fiscal_document: Any,
    company: Any,
    point_of_sale: Any,
) -> ArcaHomologationGateResult:
    """Allow query-only recovery whether the canary window is open or closed."""

    try:
        emission_enabled = _flag("ARCA_HOMOLOGATION_EMISSION_ENABLED", False)
    except ArcaHomologationReadinessError as exc:
        raise exc
    result = evaluate_homologation_readiness(
        company=company,
        point_of_sale=point_of_sale,
        check_credentials=False,
        _emission_mode=emission_enabled,
    )
    identity_ok = (
        getattr(fiscal_document, "company_id", None)
        == (getattr(company, "pk", None) or getattr(company, "id", None))
        and getattr(fiscal_document, "point_of_sale_id", None)
        == (getattr(point_of_sale, "pk", None) or getattr(point_of_sale, "id", None))
        and str(getattr(fiscal_document, "environment_snapshot", "") or "")
        == "homologation"
    )
    if not result.passed or not identity_ok:
        error_code = (
            result.error_codes[0]
            if result.error_codes
            else "homologation_recovery_identity_mismatch"
        )
        raise ArcaHomologationReadinessError(
            "Compuerta ARCA de recuperacion no aprobada.",
            error_code=error_code,
        )
    return result


def evaluate_taxpayer_registry_readiness(
    *,
    company: Any = None,
    check_credentials: bool = True,
) -> ArcaTaxpayerRegistryGateResult:
    """Extend the homologation gate for the independent taxpayer service."""

    base = evaluate_homologation_readiness(
        company=company,
        check_credentials=check_credentials,
        _require_wsfe=False,
    )
    errors = list(base.error_codes)
    expected = resolve_arca_endpoint(
        ArcaEnvironment.HOMOLOGATION,
        ArcaEndpointKind.TAXPAYER_REGISTRY,
    )

    try:
        read_enabled = _flag("ARCA_TAXPAYER_READ_ENABLED", False)
    except ArcaHomologationReadinessError as exc:
        errors.append(exc.error_code)
        read_enabled = False
    try:
        user_signal = _flag("READY_ARCA_TAXPAYER_READONLY", False)
    except ArcaHomologationReadinessError as exc:
        errors.append(exc.error_code)
        user_signal = False

    if not read_enabled:
        errors.append("taxpayer_read_disabled")
    if not user_signal:
        errors.append("taxpayer_user_signal_missing")

    service = str(
        getattr(settings, "ARCA_TAXPAYER_SERVICE_ID", "") or ""
    )
    service_configured = service == "ws_sr_constancia_inscripcion"
    if not service:
        errors.append("taxpayer_service_id_missing")
    elif not service_configured:
        errors.append("taxpayer_service_id_not_allowlisted")

    endpoint_url = str(getattr(settings, "ARCA_TAXPAYER_URL", "") or "")
    wsdl_url = str(getattr(settings, "ARCA_TAXPAYER_WSDL", "") or "")
    endpoint_configured = (
        endpoint_url == expected.url
        and wsdl_url == f"{expected.url}?WSDL"
    )
    if not endpoint_url:
        errors.append("taxpayer_endpoint_missing")
    elif endpoint_url != expected.url:
        errors.append("taxpayer_endpoint_not_allowlisted")
    if not wsdl_url:
        errors.append("taxpayer_wsdl_missing")
    elif wsdl_url != f"{expected.url}?WSDL":
        errors.append("taxpayer_wsdl_not_allowlisted")

    unique_errors = tuple(dict.fromkeys(errors))
    return ArcaTaxpayerRegistryGateResult(
        passed=not unique_errors,
        error_codes=unique_errors,
        environment=base.environment,
        service_configured=service_configured,
        endpoint_configured=endpoint_configured,
        user_signal=user_signal,
    )


def require_taxpayer_registry_read_access(
    *,
    company: Any = None,
) -> ArcaTaxpayerRegistryGateResult:
    result = evaluate_taxpayer_registry_readiness(
        company=company,
        check_credentials=False,
    )
    if not result.passed:
        raise ArcaHomologationReadinessError(
            "Compuerta ARCA de padron no aprobada.",
            error_code=result.error_codes[0],
        )
    return result


def block_arca_emission() -> None:
    """Hard block for every FECAESolicitar call lacking a valid capability."""

    raise ARCAEmissionDisabledError(
        "La emision ARCA permanece bloqueada durante esta etapa.",
        error_code="arca_emission_disabled",
    )
