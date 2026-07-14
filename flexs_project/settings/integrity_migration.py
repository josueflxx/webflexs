"""Guarded settings for applying migrations to a preserved SQLite working copy."""

import json
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from .local import *  # noqa: F401,F403


_database_value = os.getenv("INTEGRITY_MIGRATION_DATABASE", "").strip()
if not _database_value:
    raise ImproperlyConfigured("Define INTEGRITY_MIGRATION_DATABASE para usar este entorno.")

_database_path = Path(_database_value).resolve()
_configured_local_database = (BASE_DIR / "db.sqlite3").resolve()
if _database_path == _configured_local_database:
    raise ImproperlyConfigured("integrity_migration rehusa abrir db.sqlite3.")
if _database_path.name != "database_working.sqlite3" or not _database_path.is_file():
    raise ImproperlyConfigured("Solo se acepta una copia existente database_working.sqlite3.")

_manifest_path = _database_path.parent / "manifest.json"
if not _manifest_path.is_file():
    raise ImproperlyConfigured("La copia de trabajo no tiene manifest.json de preservacion.")
try:
    _manifest = json.loads(_manifest_path.read_text(encoding="utf-8"))
except (OSError, ValueError) as exc:
    raise ImproperlyConfigured("No se pudo validar el manifest de preservacion.") from exc
if Path((_manifest.get("working_copy") or {}).get("path", "")).resolve() != _database_path:
    raise ImproperlyConfigured("El manifest no corresponde a la copia solicitada.")

DATABASES["default"] = {
    "ENGINE": "django.db.backends.sqlite3",
    "NAME": _database_path,
    "OPTIONS": {
        "timeout": 60,
        "init_command": "PRAGMA foreign_keys=ON; PRAGMA journal_mode=DELETE;",
    },
}

SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"
INTEGRITY_MIGRATION_MODE = True
