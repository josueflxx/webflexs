"""
Django production settings.
Uses PostgreSQL database.
"""

from .base import *

DEBUG = os.getenv('DJANGO_DEBUG', 'False').lower() == 'true'

# PostgreSQL for production
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('DB_NAME', 'flexs_db'),
        'USER': os.getenv('DB_USER', 'flexs_user'),
        'PASSWORD': os.getenv('DB_PASSWORD', ''),
        'HOST': os.getenv('DB_HOST', 'localhost'),
        'PORT': os.getenv('DB_PORT', '5432'),
    }
}

# Security settings for production
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = os.getenv('DJANGO_SECURE_HSTS_PRELOAD', 'True').lower() == 'true'
SECURE_SSL_REDIRECT = os.getenv('DJANGO_SECURE_SSL_REDIRECT', 'True').lower() == 'true'
SESSION_COOKIE_SECURE = os.getenv('DJANGO_SESSION_COOKIE_SECURE', 'True').lower() == 'true'
CSRF_COOKIE_SECURE = os.getenv('DJANGO_CSRF_COOKIE_SECURE', 'True').lower() == 'true'
SESSION_COOKIE_HTTPONLY = os.getenv('DJANGO_SESSION_COOKIE_HTTPONLY', 'True').lower() == 'true'
CSRF_COOKIE_HTTPONLY = os.getenv('DJANGO_CSRF_COOKIE_HTTPONLY', 'False').lower() == 'true'
SESSION_COOKIE_SAMESITE = os.getenv('DJANGO_SESSION_COOKIE_SAMESITE', 'Lax')
CSRF_COOKIE_SAMESITE = os.getenv('DJANGO_CSRF_COOKIE_SAMESITE', 'Lax')
SECURE_REFERRER_POLICY = os.getenv('DJANGO_SECURE_REFERRER_POLICY', 'strict-origin-when-cross-origin')
SECURE_CROSS_ORIGIN_OPENER_POLICY = os.getenv('DJANGO_SECURE_COOP', 'same-origin')
SECURE_CROSS_ORIGIN_RESOURCE_POLICY = os.getenv('DJANGO_SECURE_CORP', 'same-origin')

# If behind Nginx/Proxy with X-Forwarded-Proto
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Trusted origins for CSRF (comma-separated)
raw_csrf_trusted = os.getenv('DJANGO_CSRF_TRUSTED_ORIGINS', '')
if raw_csrf_trusted:
    CSRF_TRUSTED_ORIGINS = [origin.strip() for origin in raw_csrf_trusted.split(',') if origin.strip()]

# Whitenoise for static files
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

# SMTP email for production
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'

# Allowed hosts from environment
ALLOWED_HOSTS = os.getenv('DJANGO_ALLOWED_HOSTS', '').split(',')
