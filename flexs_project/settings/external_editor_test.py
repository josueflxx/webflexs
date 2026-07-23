"""Isolated local settings for testing the external editor integration."""

import os
from pathlib import Path

from .local import *


DATABASES["default"]["NAME"] = Path(
    os.getenv("EXTERNAL_EDITOR_TEST_DB", BASE_DIR / "external_editor_test.sqlite3")
)
FEATURE_EXTERNAL_EDITOR_ENABLED = True
FEATURE_EXTERNAL_EDITOR_WRITES = True
FEATURE_BACKGROUND_JOBS_ENABLED = False
CELERY_TASK_ALWAYS_EAGER = True

# Solo para la base aislada de QA: evita que la creación repetida de usuarios
# domine el tiempo de la suite. Producción conserva los hashers seguros de base.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
