"""Offline-safe test settings backed by PostgreSQL.

The regular test suite remains SQLite-fast. Use this module for migration,
constraint, locking, and fiscal integration checks that must exercise the
production database engine without loading production settings or secrets.
"""

import os

from .test import *  # noqa: F403


DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("POSTGRES_TEST_DB", "webflexs_test"),
        "USER": os.getenv("POSTGRES_TEST_USER", "postgres"),
        "PASSWORD": os.getenv("POSTGRES_TEST_PASSWORD", ""),
        "HOST": os.getenv("POSTGRES_TEST_HOST", "127.0.0.1"),
        "PORT": os.getenv("POSTGRES_TEST_PORT", "5432"),
        "CONN_MAX_AGE": 0,
    }
}
