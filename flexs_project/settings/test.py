"""Deterministic, offline-safe settings for the automated test suite.

Invoke with::

    python manage.py test --settings=flexs_project.settings.test

These settings deliberately avoid production-strength password hashing because
test fixtures create hundreds of throwaway users. Runtime settings remain
unchanged.
"""

import os
import tempfile
from pathlib import Path


# Set these before importing base so dotenv values cannot initialize an
# external backend while Django is loading the test settings.
os.environ.update(
    {
        "ARCA_ENVIRONMENT": "disabled",
        "ARCA_ENABLED": "False",
        "ARCA_HOMOLOGATION_NETWORK_ENABLED": "False",
        "ARCA_HOMOLOGATION_READ_ENABLED": "False",
        "ARCA_HOMOLOGATION_EMISSION_ENABLED": "False",
        "ARCA_PRODUCTION_ENABLED": "False",
        "READY_ARCA_HOMOLOGACION_READONLY": "False",
        "ARCA_WSASS_AUTHORIZATION_CONFIRMED": "False",
        "ARCA_TOKEN_CACHE_ENABLED": "False",
        "ARCA_TOKEN_CACHE_BACKEND": "",
        "ARCA_TOKEN_CACHE_URL": "",
        "ARCA_TOKEN_CACHE_PREFIX": "webflexs:arca:homo:test",
        "ARCA_CREDENTIALS_CONFIG_JSON": "{}",
        "ARCA_COMPANY_CONFIG_JSON": "{}",
        "CELERY_BROKER_URL": "memory://",
        "CELERY_RESULT_BACKEND": "cache+memory://",
        "FEATURE_BACKGROUND_JOBS_ENABLED": "False",
        "FEATURE_EXTERNAL_EDITOR_ENABLED": "False",
        "FEATURE_EXTERNAL_EDITOR_WRITES": "False",
        "FEATURE_OBSERVABILITY_ENABLED": "False",
        "REDIS_URL": "",
        "SENTRY_DSN": "",
    }
)

from .base import *  # noqa: E402,F403


DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
        "TEST": {
            "NAME": ":memory:",
        },
    }
}

# Test-only: PBKDF2 takes roughly two seconds per fixture hash on the current
# Windows/Python environment and the suite creates hundreds of users.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "flexs-test-cache",
        "TIMEOUT": 300,
    }
}

CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_BEAT_SCHEDULE = {}

ARCA_ENVIRONMENT = "disabled"
ARCA_COMPANY_CONFIG = {}
FEATURE_BACKGROUND_JOBS_ENABLED = False
FEATURE_EXTERNAL_EDITOR_ENABLED = False
FEATURE_EXTERNAL_EDITOR_WRITES = False
FEATURE_OBSERVABILITY_ENABLED = False

_TEST_TEMP_ROOT = Path(tempfile.gettempdir()) / "flexs-tests"
MEDIA_ROOT = _TEST_TEMP_ROOT / "media"
STATIC_ROOT = _TEST_TEMP_ROOT / "static"
BACKUP_ROOT = _TEST_TEMP_ROOT / "backups"
BACKUP_INCLUDE_MEDIA = False
