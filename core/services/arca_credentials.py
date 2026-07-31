"""Backend-only ARCA credential loading and offline validation."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Mapping, Optional

from django.conf import settings

from core.services.arca_config import ArcaEnvironment


class ArcaCredentialError(ValueError):
    """A non-sensitive credential validation failure."""

    def __init__(self, message: str, *, error_code: str) -> None:
        super().__init__(message)
        self.error_code = error_code


@dataclass(frozen=True)
class ArcaCredentialSpec:
    credential_id: str
    environment: ArcaEnvironment
    issuer_cuit: str
    cert_path: Path = field(repr=False)
    key_path: Path = field(repr=False)
    expected_fingerprint_sha256: str = field(default="", repr=False)


@dataclass(frozen=True)
class ArcaCredentialMetadata:
    credential_id: str
    environment: ArcaEnvironment
    issuer_cuit: str
    fingerprint_sha256: str
    not_before: datetime
    not_after: datetime
    subject_cuit_matches: bool


def _digits(value: Any) -> str:
    return re.sub(r"\D+", "", str(value or ""))


def _company_config(config: Mapping[str, Any], company) -> Mapping[str, Any]:
    if not isinstance(config, Mapping):
        return {}
    candidates = (
        str(getattr(company, "slug", "") or "").strip(),
        str(getattr(company, "id", "") or "").strip(),
    )
    for candidate in candidates:
        if candidate and isinstance(config.get(candidate), Mapping):
            return config[candidate]
    normalized = {candidate.lower() for candidate in candidates if candidate}
    for key, value in config.items():
        if str(key).strip().lower() in normalized and isinstance(value, Mapping):
            return value
    return {}


def resolve_credential_spec(
    *,
    company,
    environment: ArcaEnvironment,
    config: Optional[Mapping[str, Any]] = None,
) -> ArcaCredentialSpec:
    all_config = (
        config
        if config is not None
        else getattr(settings, "ARCA_COMPANY_CONFIG", {}) or {}
    )
    company_config = _company_config(all_config, company)
    env_config = company_config.get(environment.value, {})
    if not isinstance(env_config, Mapping):
        env_config = {}
    if config is None and not env_config:
        # Single-company homologation setup. Values remain backend-only and
        # are accepted only when the closed readiness gate is explicitly
        # enabled. Multi-company deployments can keep using the JSON mapping.
        env_config = {
            "credential_id": getattr(settings, "ARCA_CREDENTIAL_ID", ""),
            "environment": environment.value,
            "cuit": getattr(settings, "ARCA_CUIT", ""),
            "cert_path": getattr(settings, "ARCA_CERT_PATH", ""),
            "key_path": getattr(settings, "ARCA_PRIVATE_KEY_PATH", ""),
            "fingerprint_sha256": getattr(
                settings,
                "ARCA_EXPECTED_CERT_SHA256",
                "",
            ),
        }

    label = str(env_config.get("environment") or "").strip().lower()
    if label != environment.value:
        raise ArcaCredentialError(
            "La credencial ARCA no esta etiquetada para el entorno activo.",
            error_code="credential_environment_mismatch",
        )

    credential_id = str(env_config.get("credential_id") or "").strip()
    if not credential_id or len(credential_id) > 120:
        raise ArcaCredentialError(
            "Identificador de credencial ARCA ausente o invalido.",
            error_code="credential_id_invalid",
        )

    issuer_cuit = _digits(env_config.get("cuit") or getattr(company, "cuit", ""))
    company_cuit = _digits(getattr(company, "cuit", ""))
    if len(issuer_cuit) != 11:
        raise ArcaCredentialError(
            "CUIT emisor ARCA ausente o invalido.",
            error_code="credential_cuit_invalid",
        )
    if company_cuit and company_cuit != issuer_cuit:
        raise ArcaCredentialError(
            "El CUIT de la credencial no coincide con la empresa.",
            error_code="credential_cuit_mismatch",
        )

    cert_path = Path(str(env_config.get("cert_path") or "").strip())
    key_path = Path(str(env_config.get("key_path") or "").strip())
    if not str(cert_path) or str(cert_path) == ".":
        raise ArcaCredentialError(
            "Certificado ARCA no configurado.",
            error_code="credential_certificate_missing",
        )
    if not str(key_path) or str(key_path) == ".":
        raise ArcaCredentialError(
            "Clave privada ARCA no configurada.",
            error_code="credential_private_key_missing",
        )

    expected_fingerprint = re.sub(
        r"[^0-9a-f]+",
        "",
        str(env_config.get("fingerprint_sha256") or "").lower(),
    )
    if expected_fingerprint and len(expected_fingerprint) != 64:
        raise ArcaCredentialError(
            "Huella esperada de certificado ARCA invalida.",
            error_code="credential_fingerprint_invalid",
        )

    return ArcaCredentialSpec(
        credential_id=credential_id,
        environment=environment,
        issuer_cuit=issuer_cuit,
        cert_path=cert_path,
        key_path=key_path,
        expected_fingerprint_sha256=expected_fingerprint,
    )


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except (ValueError, OSError):
        return False


def _has_link_or_junction_component(path: Path) -> bool:
    try:
        candidate = path.expanduser().absolute()
    except (OSError, RuntimeError):
        return True
    for component in (candidate, *candidate.parents):
        try:
            is_junction = bool(
                getattr(component, "is_junction", lambda: False)()
            )
            if component.is_symlink() or is_junction:
                return True
        except OSError:
            return True
    return False


def _validate_path(
    path: Path,
    *,
    private: bool,
    permission_runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> Path:
    if _has_link_or_junction_component(path):
        raise ArcaCredentialError(
            "La credencial ARCA no puede atravesar enlaces o junctions.",
            error_code="credential_file_type_invalid",
        )
    try:
        resolved = path.expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ArcaCredentialError(
            "Archivo de credencial ARCA inexistente o inaccesible.",
            error_code="credential_file_unavailable",
        ) from exc

    if path.is_symlink() or not resolved.is_file():
        raise ArcaCredentialError(
            "La credencial ARCA debe ser un archivo regular no enlazado.",
            error_code="credential_file_type_invalid",
        )
    try:
        if resolved.stat().st_size <= 0:
            raise ArcaCredentialError(
                "El archivo de credencial ARCA esta vacio.",
                error_code="credential_file_empty",
            )
    except OSError as exc:
        raise ArcaCredentialError(
            "Archivo de credencial ARCA inaccesible.",
            error_code="credential_file_unavailable",
        ) from exc

    forbidden_roots = [Path(settings.BASE_DIR).resolve()]
    for setting_name in ("STATIC_ROOT", "MEDIA_ROOT"):
        raw_root = getattr(settings, setting_name, None)
        if raw_root:
            forbidden_roots.append(Path(raw_root).resolve())
    if any(_is_within(resolved, root) for root in forbidden_roots):
        raise ArcaCredentialError(
            "Las credenciales ARCA deben montarse fuera del repositorio y archivos publicables.",
            error_code="credential_path_forbidden",
        )

    if private and os.name == "posix":
        mode = stat.S_IMODE(resolved.stat().st_mode)
        if mode & 0o077:
            raise ArcaCredentialError(
                "Los permisos de la clave privada ARCA son demasiado amplios.",
                error_code="credential_key_permissions",
            )
    if private and os.name == "nt":
        permissions = _safe_run(
            permission_runner,
            ["icacls", str(resolved)],
        )
        output = bytes(permissions.stdout or b"").decode(
            "utf-8",
            errors="replace",
        )
        broad_principals = (
            "everyone:",
            "authenticated users:",
            "builtin\\users:",
            "todos:",
            "usuarios autentificados:",
            "usuarios autenticados:",
        )
        if (
            permissions.returncode != 0
            or not output.strip()
            or any(
                principal in output.lower()
                for principal in broad_principals
            )
        ):
            raise ArcaCredentialError(
                "Los permisos de la clave privada ARCA no son restrictivos.",
                error_code="credential_key_permissions",
            )
    return resolved


def _safe_run(
    runner: Callable[..., subprocess.CompletedProcess],
    command: list[str],
) -> subprocess.CompletedProcess:
    try:
        return runner(
            command,
            check=False,
            capture_output=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ArcaCredentialError(
            "No se pudo ejecutar la validacion criptografica local.",
            error_code="openssl_unavailable",
        ) from exc


def _parse_openssl_date(raw: str) -> datetime:
    try:
        parsed = parsedate_to_datetime(raw.strip())
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ArcaCredentialError(
            "OpenSSL devolvio una fecha de certificado invalida.",
            error_code="credential_certificate_dates_invalid",
        ) from exc


def validate_credential_offline(
    spec: ArcaCredentialSpec,
    *,
    now: Optional[datetime] = None,
    openssl_bin: str = "openssl",
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> ArcaCredentialMetadata:
    """Validate certificate/key without contacting ARCA or exposing contents."""

    if spec.environment is not ArcaEnvironment.HOMOLOGATION:
        raise ArcaCredentialError(
            "Solo se admiten credenciales etiquetadas para homologacion.",
            error_code="credential_environment_blocked",
        )
    executable_name = Path(str(openssl_bin or "")).name.lower()
    if executable_name not in {"openssl", "openssl.exe"}:
        raise ArcaCredentialError(
            "Ejecutable criptografico no permitido.",
            error_code="openssl_binary_forbidden",
        )

    cert_path = _validate_path(
        spec.cert_path,
        private=False,
        permission_runner=runner,
    )
    key_path = _validate_path(
        spec.key_path,
        private=True,
        permission_runner=runner,
    )

    dates_process = _safe_run(
        runner,
        [openssl_bin, "x509", "-in", str(cert_path), "-noout", "-dates"],
    )
    if dates_process.returncode != 0:
        raise ArcaCredentialError(
            "Formato de certificado ARCA invalido.",
            error_code="credential_certificate_invalid",
        )
    dates_output = bytes(dates_process.stdout or b"").decode("ascii", errors="replace")
    not_before_match = re.search(r"^notBefore=(.+)$", dates_output, flags=re.MULTILINE)
    not_after_match = re.search(r"^notAfter=(.+)$", dates_output, flags=re.MULTILINE)
    if not not_before_match or not not_after_match:
        raise ArcaCredentialError(
            "No se pudo determinar la vigencia del certificado ARCA.",
            error_code="credential_certificate_dates_missing",
        )
    not_before = _parse_openssl_date(not_before_match.group(1))
    not_after = _parse_openssl_date(not_after_match.group(1))
    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if current_time < not_before:
        raise ArcaCredentialError(
            "El certificado ARCA todavia no esta vigente.",
            error_code="credential_certificate_not_yet_valid",
        )
    if current_time >= not_after:
        raise ArcaCredentialError(
            "El certificado ARCA esta vencido.",
            error_code="credential_certificate_expired",
        )

    subject_process = _safe_run(
        runner,
        [
            openssl_bin,
            "x509",
            "-in",
            str(cert_path),
            "-noout",
            "-subject",
            "-nameopt",
            "RFC2253",
        ],
    )
    subject_output = bytes(subject_process.stdout or b"").decode(
        "utf-8",
        errors="replace",
    )
    subject_match = re.search(
        r"serialNumber\s*=\s*CUIT\s*([0-9]{11})",
        subject_output,
        flags=re.IGNORECASE,
    )
    if (
        subject_process.returncode != 0
        or not subject_match
        or subject_match.group(1) != spec.issuer_cuit
    ):
        raise ArcaCredentialError(
            "El subject del certificado no coincide con el CUIT configurado.",
            error_code="credential_subject_cuit_mismatch",
        )

    cert_public = _safe_run(
        runner,
        [openssl_bin, "x509", "-in", str(cert_path), "-pubkey", "-noout"],
    )
    if cert_public.returncode != 0 or not cert_public.stdout:
        raise ArcaCredentialError(
            "No se pudo leer la clave publica del certificado ARCA.",
            error_code="credential_certificate_public_key_invalid",
        )

    key_check = _safe_run(
        runner,
        [
            openssl_bin,
            "pkey",
            "-in",
            str(key_path),
            "-check",
            "-noout",
            "-passin",
            "pass:",
        ],
    )
    if key_check.returncode != 0:
        raise ArcaCredentialError(
            "La clave privada ARCA no supero la verificacion de integridad.",
            error_code="credential_private_key_invalid",
        )

    key_public = _safe_run(
        runner,
        [
            openssl_bin,
            "pkey",
            "-in",
            str(key_path),
            "-pubout",
            "-passin",
            "pass:",
        ],
    )
    if key_public.returncode != 0 or not key_public.stdout:
        raise ArcaCredentialError(
            "Formato de clave privada ARCA invalido o clave cifrada no configurada.",
            error_code="credential_private_key_invalid",
        )
    if bytes(cert_public.stdout).strip() != bytes(key_public.stdout).strip():
        raise ArcaCredentialError(
            "El certificado ARCA y la clave privada no corresponden.",
            error_code="credential_key_mismatch",
        )

    cert_der = _safe_run(
        runner,
        [openssl_bin, "x509", "-in", str(cert_path), "-outform", "DER"],
    )
    if cert_der.returncode != 0 or not cert_der.stdout:
        raise ArcaCredentialError(
            "No se pudo calcular la huella del certificado ARCA.",
            error_code="credential_fingerprint_unavailable",
        )
    fingerprint = hashlib.sha256(bytes(cert_der.stdout)).hexdigest()
    if (
        spec.expected_fingerprint_sha256
        and fingerprint != spec.expected_fingerprint_sha256
    ):
        raise ArcaCredentialError(
            "La huella del certificado ARCA no coincide con la configuracion.",
            error_code="credential_fingerprint_mismatch",
        )

    return ArcaCredentialMetadata(
        credential_id=spec.credential_id,
        environment=spec.environment,
        issuer_cuit=spec.issuer_cuit,
        fingerprint_sha256=fingerprint,
        not_before=not_before,
        not_after=not_after,
        subject_cuit_matches=True,
    )


def iter_configured_credential_entries(
    config: Mapping[str, Any],
) -> Iterable[tuple[str, Mapping[str, Any]]]:
    if not isinstance(config, Mapping):
        return
    for company_key, company_config in config.items():
        if not isinstance(company_config, Mapping):
            continue
        environment_config = company_config.get(ArcaEnvironment.HOMOLOGATION.value)
        if isinstance(environment_config, Mapping):
            yield str(company_key), environment_config
