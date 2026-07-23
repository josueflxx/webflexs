"""Redact secrets before application data reaches logs or persistence."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


REDACTED = "[REDACTED]"
OMITTED = "[OMITTED]"

_SECRET_KEY_PARTS = {
    "authorization",
    "bearer",
    "certificate",
    "cert_path",
    "cms",
    "cookie",
    "credential",
    "credentials",
    "key_path",
    "password",
    "passwd",
    "private_key",
    "privatekey",
    "secret",
    "sign",
    "signature",
    "ticket",
    "token",
}
_RAW_CONTENT_KEYS = {
    "body_xml",
    "raw",
    "request_xml",
    "response_xml",
    "soap_body",
    "ticket_xml",
    "tra_xml",
}

_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----.*?"
    r"-----END(?: [A-Z0-9]+)? PRIVATE KEY-----",
    flags=re.IGNORECASE | re.DOTALL,
)
_XML_SECRET_RE = re.compile(
    r"(<(?:[A-Za-z_][\w.-]*:)?(?:Token|Sign|Password|Passwd|Secret|PrivateKey)>).*?"
    r"(</(?:[A-Za-z_][\w.-]*:)?(?:Token|Sign|Password|Passwd|Secret|PrivateKey)>)",
    flags=re.IGNORECASE | re.DOTALL,
)
_AUTH_HEADER_RE = re.compile(
    r"\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+",
    flags=re.IGNORECASE,
)
_KEY_VALUE_SECRET_RE = re.compile(
    r"\b(token|sign|signature|password|passwd|secret)=([^&\s]+)",
    flags=re.IGNORECASE,
)


def sanitize_sensitive_text(value: Any) -> str:
    """Return a display-safe string with common credential formats redacted."""
    text = str(value or "")
    text = _PRIVATE_KEY_RE.sub(REDACTED, text)
    text = _XML_SECRET_RE.sub(lambda match: f"{match.group(1)}{REDACTED}{match.group(2)}", text)
    text = _AUTH_HEADER_RE.sub(lambda match: f"{match.group(1)} {REDACTED}", text)
    return _KEY_VALUE_SECRET_RE.sub(lambda match: f"{match.group(1)}={REDACTED}", text)


def _normalized_key(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def _is_secret_key(key: Any) -> bool:
    normalized = _normalized_key(key)
    return any(
        normalized == part
        or normalized.startswith(f"{part}_")
        or normalized.endswith(f"_{part}")
        for part in _SECRET_KEY_PARTS
    )


def sanitize_sensitive_payload(value: Any) -> Any:
    """Recursively copy a payload while removing raw transport data and secrets."""
    if isinstance(value, Mapping):
        sanitized = {}
        for key, item in value.items():
            normalized = _normalized_key(key)
            if normalized in _RAW_CONTENT_KEYS:
                sanitized[key] = OMITTED
            elif _is_secret_key(key):
                sanitized[key] = REDACTED
            else:
                sanitized[key] = sanitize_sensitive_payload(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_sensitive_payload(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_sensitive_payload(item) for item in value]
    if isinstance(value, str):
        return sanitize_sensitive_text(value)
    return value
