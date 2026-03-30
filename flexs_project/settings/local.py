"""
Django local development settings.
Uses SQLite database.
"""

import os

from .base import *

DEBUG = True

# SQLite for local development with optimizations
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
        'OPTIONS': {
            # Increase timeout to prevent "database is locked" errors
            'timeout': 20,
            # Enable WAL mode for better concurrent access
            'init_command': (
                "PRAGMA journal_mode=WAL;"
                "PRAGMA synchronous=NORMAL;"
                "PRAGMA cache_size=-64000;"  # 64MB cache
            ),
        }
    }
}

# Allow all hosts in development
ALLOWED_HOSTS = ['*']

# Email backend in local:
# - default: console (safe for dev)
# - set LOCAL_EMAIL_BACKEND=smtp in .env to send real emails
local_email_backend = os.getenv('LOCAL_EMAIL_BACKEND', '').strip().lower()
if local_email_backend == 'smtp':
    EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
else:
    EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

# Keep login lockout behavior softer in local development so testing flows
# does not leave the main admin user blocked for long periods.
LOGIN_MAX_FAILED_ATTEMPTS = 10
LOGIN_LOCKOUT_SECONDS = 60
LOGIN_ATTEMPT_WINDOW_SECONDS = 5 * 60
