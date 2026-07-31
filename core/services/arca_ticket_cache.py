"""Fail-closed WSAA ticket cache with backend-wide singleflight renewal."""

from __future__ import annotations

import hashlib
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional
from urllib.parse import urlsplit

from django.conf import settings
from django.core.cache import cache as default_cache


class ArcaTicketCacheError(RuntimeError):
    def __init__(self, message: str, *, error_code: str) -> None:
        super().__init__(message)
        self.error_code = error_code


@dataclass(frozen=True)
class ArcaAccessTicket:
    token: str
    sign: str
    generation_time: datetime
    expiration_time: datetime


@dataclass(frozen=True)
class ArcaCacheConfiguration:
    valid: bool
    configured: bool
    backend_kind: str
    error_code: str


def configured_arca_cache_prefix() -> str:
    prefix = str(
        getattr(
            settings,
            "ARCA_TOKEN_CACHE_PREFIX",
            "webflexs:arca:homo",
        )
        or ""
    )
    if not re.fullmatch(r"[A-Za-z0-9:_.-]{1,80}", prefix):
        raise ArcaTicketCacheError(
            "Prefijo de cache ARCA ausente o invalido.",
            error_code="cache_prefix_invalid",
        )
    return prefix


def _cache_locations(raw_location) -> tuple[str, ...]:
    if isinstance(raw_location, (list, tuple)):
        return tuple(str(item or "") for item in raw_location)
    return (str(raw_location or ""),)


def inspect_arca_cache_configuration() -> ArcaCacheConfiguration:
    config = (
        (getattr(settings, "CACHES", {}) or {}).get("default", {}) or {}
    )
    backend = str(config.get("BACKEND") or "")
    backend_name = backend.rsplit(".", 1)[-1]
    if not backend:
        return ArcaCacheConfiguration(
            valid=False,
            configured=False,
            backend_kind="none",
            error_code="cache_not_configured",
        )

    if backend_name == "RedisCache":
        locations = _cache_locations(config.get("LOCATION"))
        for location in locations:
            try:
                parsed = urlsplit(location)
                port = parsed.port
            except ValueError:
                parsed = None
                port = None
            if (
                parsed is None
                or parsed.scheme not in {"redis", "rediss"}
                or not parsed.hostname
                or port is None
                or parsed.query
                or parsed.fragment
                or (
                    parsed.path not in {"", "/"}
                    and not re.fullmatch(r"/[0-9]+", parsed.path)
                )
            ):
                return ArcaCacheConfiguration(
                    valid=False,
                    configured=True,
                    backend_kind="redis",
                    error_code="cache_location_invalid",
                )
        return ArcaCacheConfiguration(
            valid=True,
            configured=True,
            backend_kind="redis",
            error_code="",
        )

    if backend_name in {"PyMemcacheCache", "PyLibMCCache"}:
        locations = _cache_locations(config.get("LOCATION"))
        for location in locations:
            try:
                parsed = urlsplit(f"//{location}")
                port = parsed.port
            except ValueError:
                parsed = None
                port = None
            if (
                parsed is None
                or not parsed.hostname
                or port is None
                or "/" in location
                or "\\" in location
            ):
                return ArcaCacheConfiguration(
                    valid=False,
                    configured=True,
                    backend_kind="memcached",
                    error_code="cache_location_invalid",
                )
        return ArcaCacheConfiguration(
            valid=True,
            configured=True,
            backend_kind="memcached",
            error_code="",
        )

    return ArcaCacheConfiguration(
        valid=False,
        configured=True,
        backend_kind="forbidden",
        error_code="cache_backend_forbidden",
    )


def shared_arca_cache_configured() -> bool:
    return inspect_arca_cache_configuration().valid


def _utc_datetime(value) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise ArcaTicketCacheError(
                "Fecha de Ticket de Acceso invalida.",
                error_code="ticket_time_invalid",
            ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class ArcaTicketCoordinator:
    def __init__(
        self,
        *,
        issuer_cuit: str,
        environment: str,
        service: str,
        credential_fingerprint: str,
        cache_backend=None,
        require_shared: bool = True,
        lock_seconds: int = 60,
        wait_seconds: float = 5.0,
        renewal_margin_seconds: int = 120,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if require_shared and not shared_arca_cache_configured():
            raise ArcaTicketCacheError(
                "WSAA requiere cache compartido en homologacion.",
                error_code="wsaa_shared_cache_required",
            )
        self.cache = cache_backend or default_cache
        self.lock_seconds = max(int(lock_seconds or 60), 30)
        self.wait_seconds = max(float(wait_seconds or 5), 0.1)
        self.renewal_margin_seconds = max(int(renewal_margin_seconds or 120), 30)
        self.sleeper = sleeper
        identity = "|".join(
            (
                str(issuer_cuit or ""),
                str(environment or ""),
                str(service or ""),
                str(credential_fingerprint or ""),
            )
        )
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        prefix = configured_arca_cache_prefix()
        self.ticket_key = f"{prefix}:wsaa:ticket:{digest}"
        self.lock_key = f"{prefix}:wsaa:lock:{digest}"

    def _deserialize(self, raw) -> Optional[ArcaAccessTicket]:
        if not isinstance(raw, dict):
            return None
        token = str(raw.get("token") or "")
        sign = str(raw.get("sign") or "")
        if not token or not sign:
            return None
        try:
            generation = _utc_datetime(raw.get("generation_time"))
            expiration = _utc_datetime(raw.get("expiration_time"))
        except ArcaTicketCacheError:
            return None
        now = datetime.now(timezone.utc)
        if generation > now + timedelta(seconds=30):
            return None
        if expiration <= now + timedelta(seconds=self.renewal_margin_seconds):
            return None
        if generation >= expiration:
            return None
        return ArcaAccessTicket(
            token=token,
            sign=sign,
            generation_time=generation,
            expiration_time=expiration,
        )

    def get_valid_ticket(self) -> Optional[ArcaAccessTicket]:
        try:
            return self._deserialize(self.cache.get(self.ticket_key))
        except Exception as exc:
            raise ArcaTicketCacheError(
                "Cache WSAA no disponible.",
                error_code="wsaa_cache_unavailable",
            ) from exc

    def clear_ticket(self) -> None:
        """Delete the exact Token/Sign entry without enumerating the cache."""

        try:
            self.cache.delete(self.ticket_key)
        except Exception as exc:
            raise ArcaTicketCacheError(
                "No se pudo eliminar Ticket de Acceso del cache.",
                error_code="wsaa_cache_delete_failed",
            ) from exc

    def _store(self, ticket: ArcaAccessTicket) -> None:
        parsed = self._deserialize(
            {
                "token": ticket.token,
                "sign": ticket.sign,
                "generation_time": ticket.generation_time.isoformat(),
                "expiration_time": ticket.expiration_time.isoformat(),
            }
        )
        if parsed is None:
            raise ArcaTicketCacheError(
                "Ticket de Acceso invalido; no se almaceno.",
                error_code="wsaa_ticket_invalid",
            )
        now = datetime.now(timezone.utc)
        ttl = int((parsed.expiration_time - now).total_seconds())
        ttl -= self.renewal_margin_seconds
        if ttl < 1:
            raise ArcaTicketCacheError(
                "Ticket de Acceso sin vigencia suficiente.",
                error_code="wsaa_ticket_ttl_invalid",
            )
        try:
            self.cache.set(
                self.ticket_key,
                {
                    "token": parsed.token,
                    "sign": parsed.sign,
                    "generation_time": parsed.generation_time.isoformat(),
                    "expiration_time": parsed.expiration_time.isoformat(),
                },
                timeout=ttl,
            )
        except Exception as exc:
            raise ArcaTicketCacheError(
                "No se pudo almacenar Ticket de Acceso en cache compartido.",
                error_code="wsaa_cache_write_failed",
            ) from exc

    def _try_acquire(self) -> bool:
        owner_marker = hashlib.sha256(secrets.token_bytes(32)).hexdigest()
        try:
            return bool(
                self.cache.add(
                    self.lock_key,
                    owner_marker,
                    timeout=self.lock_seconds,
                )
            )
        except Exception as exc:
            raise ArcaTicketCacheError(
                "No se pudo adquirir exclusion WSAA.",
                error_code="wsaa_lock_unavailable",
            ) from exc

    def get_or_create(
        self,
        loader: Callable[[], ArcaAccessTicket],
    ) -> ArcaAccessTicket:
        existing = self.get_valid_ticket()
        if existing is not None:
            return existing

        deadline = time.monotonic() + self.wait_seconds
        while True:
            if self._try_acquire():
                # Double check after winning the lease.  The lease is not
                # deleted: it expires shortly, avoiding a compare/delete race.
                existing = self.get_valid_ticket()
                if existing is not None:
                    return existing
                ticket = loader()
                self._store(ticket)
                return ticket

            existing = self.get_valid_ticket()
            if existing is not None:
                return existing
            if time.monotonic() >= deadline:
                raise ArcaTicketCacheError(
                    "Renovacion WSAA en curso o bloqueada; operacion detenida.",
                    error_code="wsaa_singleflight_timeout",
                )
            self.sleeper(0.05)
