"""
Django local development settings.
Uses SQLite database.
"""

from .base import *

DEBUG = True

# SQLite for local development
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

# Allow all hosts in development
ALLOWED_HOSTS = ['*']

# Show emails in console during development
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
