"""Completely offline, sanitized ARCA homologation diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

from core.services.arca_homologation import (
    ArcaHomologationGateResult,
    evaluate_homologation_readiness,
)
from core.services.arca_ticket_cache import inspect_arca_cache_configuration


WAITING_FOR_USER_CODES = frozenset(
    {
        "integration_disabled",
        "homologation_network_disabled",
        "homologation_read_disabled",
        "user_readiness_signal_missing",
        "wsass_authorization_not_confirmed",
        "ticket_cache_disabled",
        "homologation_environment_required",
        "wsaa_endpoint_missing",
        "wsfe_endpoint_missing",
        "wsfe_wsdl_missing",
        "service_id_missing",
        "issuer_cuit_missing",
        "point_of_sale_missing",
        "voucher_type_missing",
        "certificate_path_missing",
        "private_key_path_missing",
        "credential_file_unavailable",
        "cache_not_configured",
    }
)


@dataclass(frozen=True)
class ArcaHomologationDoctorResult:
    status: str
    environment: str
    production_disabled: bool
    emission_disabled: bool
    endpoints_allowlisted: bool
    tls_active: bool
    redaction_active: bool
    cache_state: str
    required_variables_present: bool
    certificate_path_configured: bool
    private_key_path_configured: bool
    certificate_present: bool
    private_key_present: bool
    wsass_authorization_confirmed: bool
    cuit_configured: bool
    point_of_sale_configured: bool
    voucher_type_configured: bool
    user_signal: bool
    gate_possible: bool
    probe_possible: bool
    reasons: tuple[str, ...]


def _exact_flag(name: str, default: bool) -> bool:
    value = getattr(settings, name, default)
    if isinstance(value, bool):
        return value
    normalized = str(value or "").lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    return False


def _path_state(setting_name: str) -> tuple[bool, bool]:
    raw_value = str(getattr(settings, setting_name, "") or "")
    if not raw_value:
        return False, False
    try:
        path = Path(raw_value).expanduser()
        return True, bool(
            path.exists()
            and path.is_file()
            and not path.is_symlink()
        )
    except (OSError, RuntimeError):
        return True, False


def _endpoints_allowlisted(
    gate: ArcaHomologationGateResult,
) -> bool:
    endpoint_errors = {
        "wsaa_endpoint_missing",
        "wsaa_endpoint_not_allowlisted",
        "wsfe_endpoint_missing",
        "wsfe_endpoint_not_allowlisted",
        "wsfe_wsdl_missing",
        "wsfe_wsdl_not_allowlisted",
    }
    return not endpoint_errors.intersection(gate.error_codes)


def evaluate_homologation_doctor() -> ArcaHomologationDoctorResult:
    """Inspect local readiness without DNS, HTTP, WSAA, WSFE or WSDL I/O."""

    gate = evaluate_homologation_readiness(check_credentials=True)
    reasons = gate.error_codes
    if gate.passed:
        status = "PASS"
    elif all(code in WAITING_FOR_USER_CODES for code in reasons):
        status = "WAITING_FOR_USER"
    else:
        status = "FAIL"

    certificate_path_configured, certificate_present = _path_state(
        "ARCA_CERT_PATH"
    )
    private_key_path_configured, private_key_present = _path_state(
        "ARCA_PRIVATE_KEY_PATH"
    )
    cache = inspect_arca_cache_configuration()
    cache_enabled = _exact_flag("ARCA_TOKEN_CACHE_ENABLED", False)
    if not cache_enabled:
        cache_state = "disabled"
    elif cache.valid:
        cache_state = f"ready:{cache.backend_kind}"
    else:
        cache_state = f"invalid:{cache.error_code}"

    endpoints_allowlisted = _endpoints_allowlisted(gate)
    required_variables_present = all(
        (
            gate.environment == "homologation",
            endpoints_allowlisted,
            gate.service_configured,
            gate.cuit_configured,
            gate.point_of_sale_configured,
            gate.voucher_type_configured,
            gate.wsass_authorization_confirmed,
            certificate_path_configured,
            private_key_path_configured,
            cache_enabled,
            cache.valid,
        )
    )
    user_signal = _exact_flag(
        "READY_ARCA_HOMOLOGACION_READONLY",
        False,
    )

    return ArcaHomologationDoctorResult(
        status=status,
        environment=gate.environment,
        production_disabled=not _exact_flag(
            "ARCA_PRODUCTION_ENABLED",
            False,
        ),
        emission_disabled=not _exact_flag(
            "ARCA_HOMOLOGATION_EMISSION_ENABLED",
            False,
        ),
        endpoints_allowlisted=endpoints_allowlisted,
        tls_active=_exact_flag("ARCA_TLS_VERIFY", True),
        redaction_active=_exact_flag("ARCA_REDACT_SECRETS", True),
        cache_state=cache_state,
        required_variables_present=required_variables_present,
        certificate_path_configured=certificate_path_configured,
        private_key_path_configured=private_key_path_configured,
        certificate_present=certificate_present,
        private_key_present=private_key_present,
        wsass_authorization_confirmed=(
            gate.wsass_authorization_confirmed
        ),
        cuit_configured=gate.cuit_configured,
        point_of_sale_configured=gate.point_of_sale_configured,
        voucher_type_configured=gate.voucher_type_configured,
        user_signal=user_signal,
        gate_possible=gate.passed,
        probe_possible=gate.passed and user_signal,
        reasons=reasons,
    )
