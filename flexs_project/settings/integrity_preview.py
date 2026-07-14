"""Read-only settings for validating a preserved SQLite candidate database."""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from .local import *  # noqa: F401,F403


_database_value = os.getenv("INTEGRITY_PREVIEW_DATABASE", "").strip()
if not _database_value:
    raise ImproperlyConfigured("Define INTEGRITY_PREVIEW_DATABASE para usar este entorno.")

_database_path = Path(_database_value).resolve()
_configured_local_database = (BASE_DIR / "db.sqlite3").resolve()
if _database_path == _configured_local_database:
    raise ImproperlyConfigured("integrity_preview rehusa abrir db.sqlite3.")
if not _database_path.is_file():
    raise ImproperlyConfigured(f"No existe la copia SQLite: {_database_path}")

DATABASES["default"] = {
    "ENGINE": "django.db.backends.sqlite3",
    "NAME": _database_path,
    "OPTIONS": {
        "timeout": 20,
        "init_command": "PRAGMA query_only=ON;",
    },
}

# Authentication smoke tests use signed cookies so no session row is written.
SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"
INTEGRITY_PREVIEW_MODE = True
