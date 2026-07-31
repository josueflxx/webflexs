"""Redact secrets before application data reaches logs or persistence."""

from __future__ import annotations

import logging
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
    "passphrase",
    "pkcs7",
    "pkcs8",
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

_PEM_BLOCK_RE = re.compile(
    r"-----BEGIN (?P<kind>(?:[A-Z0-9]+ )?(?:PRIVATE KEY|CERTIFICATE|PKCS7))-----.*?"
    r"-----END (?P=kind)-----",
    flags=re.IGNORECASE | re.DOTALL,
)
_XML_SECRET_RE = re.compile(
    r"(<(?:[A-Za-z_][\w.-]*:)?"
    r"(?:Token|Sign|Password|Passwd|Passphrase|Secret|PrivateKey|loginCmsReturn|in0)"
    r"\b[^>]*>).*?"
    r"(</(?:[A-Za-z_][\w.-]*:)?"
    r"(?:Token|Sign|Password|Passwd|Passphrase|Secret|PrivateKey|loginCmsReturn|in0)\s*>)",
    flags=re.IGNORECASE | re.DOTALL,
)
_AUTH_HEADER_RE = re.compile(
    r"\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+",
    flags=re.IGNORECASE,
)
_KEY_VALUE_SECRET_RE = re.compile(
    r"\b(token|sign|signature|password|passwd|passphrase|secret|"
    r"cert_path|key_path|private_key)=([^&\s,;]+)",
    flags=re.IGNORECASE,
)
_QUOTED_SECRET_RE = re.compile(
    r"(?P<prefix>[\"'](?:token|sign|signature|password|passwd|passphrase|secret|"
    r"cert_path|key_path|private_key)[\"']\s*:\s*)"
    r"(?P<quote>[\"'])(?P<value>.*?)(?P=quote)",
    flags=re.IGNORECASE | re.DOTALL,
)


def sanitize_sensitive_text(value: Any) -> str:
    """Return a display-safe string with common credential formats redacted."""
    text = str(value or "")
    text = _PEM_BLOCK_RE.sub(REDACTED, text)
    text = _XML_SECRET_RE.sub(lambda match: f"{match.group(1)}{REDACTED}{match.group(2)}", text)
    text = _AUTH_HEADER_RE.sub(lambda match: f"{match.group(1)} {REDACTED}", text)
    text = _KEY_VALUE_SECRET_RE.sub(lambda match: f"{match.group(1)}={REDACTED}", text)
    return _QUOTED_SECRET_RE.sub(
        lambda match: f"{match.group('prefix')}{match.group('quote')}"
        f"{REDACTED}{match.group('quote')}",
        text,
    )


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
    if isinstance(value, (bytes, bytearray, memoryview)):
        return REDACTED
    if isinstance(value, str):
        return sanitize_sensitive_text(value)
    return value


class SensitiveDataFilter(logging.Filter):
    """Redact log records before any configured handler receives them."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = sanitize_sensitive_text(record.msg)
        if isinstance(record.args, Mapping):
            record.args = sanitize_sensitive_payload(record.args)
        elif isinstance(record.args, tuple):
            record.args = tuple(sanitize_sensitive_payload(item) for item in record.args)
        elif record.args:
            record.args = sanitize_sensitive_payload(record.args)
        return True


class RedactingFormatter(logging.Formatter):
    """Last-line defense that also redacts formatted tracebacks."""

    def format(self, record: logging.LogRecord) -> str:
        return sanitize_sensitive_text(super().format(record))


def sanitize_sentry_event(event, hint=None):
    """Sentry ``before_send`` hook; never returns the original mutable event."""

    del hint
    sanitized = sanitize_sensitive_payload(event or {})
    return sanitized if isinstance(sanitized, dict) else {}
