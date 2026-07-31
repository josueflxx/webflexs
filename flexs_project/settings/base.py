"""
Django base settings for FLEXS B2B project.
Shared configuration for all environments.
"""

import os
import json
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


def _env_int(name, default):
    value = os.getenv(name, "")
    if value == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_guard_bool(name, default):
    """Keep ambiguous security booleans visible so the ARCA gate rejects them."""
    raw = os.getenv(name)
    if raw is None or raw == "":
        return bool(default)
    normalized = raw.lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    return raw


def _env_json(name, default):
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        data = json.loads(raw)
        return data
    except json.JSONDecodeError:
        return default


def _env_csv(name, default=""):
    raw = os.getenv(name, default)
    return [item.strip() for item in str(raw).split(",") if item.strip()]

# Build paths inside the project
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.getenv(
    'DJANGO_SECRET_KEY',
    'dev-local-only-flexs-2026-change-this-secret-key-y6K2mP9rQ4tV1xW8zN3',
)

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.getenv('DJANGO_DEBUG', 'True').lower() == 'true'

ALLOWED_HOSTS = os.getenv('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')

# Default company for new client origin (Ubolt by default).
DEFAULT_CLIENT_ORIGIN_COMPANY_SLUG = os.getenv('DEFAULT_CLIENT_ORIGIN_COMPANY_SLUG', 'ubolt')
DEFAULT_CLIENT_IMPORT_COMPANY_SLUGS = [
    slug.strip()
    for slug in os.getenv(
        'DEFAULT_CLIENT_IMPORT_COMPANY_SLUGS',
        DEFAULT_CLIENT_ORIGIN_COMPANY_SLUG,
    ).split(',')
    if slug.strip()
]
_admin_company_access_raw = _env_json("ADMIN_COMPANY_ACCESS_JSON", {})
ADMIN_COMPANY_ACCESS = {
    str(username).strip().lower(): [
        str(slug).strip().lower()
        for slug in (slugs or [])
        if str(slug).strip()
    ]
    for username, slugs in (_admin_company_access_raw or {}).items()
    if str(username).strip()
}
ADMIN_COMPANY_ACCESS_REQUIRE_EXPLICIT = os.getenv(
    "ADMIN_COMPANY_ACCESS_REQUIRE_EXPLICIT",
    "True",
).lower() == "true"
STRICT_COMPANY_ISOLATION = os.getenv("STRICT_COMPANY_ISOLATION", "True").lower() == "true"
TRUSTED_PROXY_IPS = tuple(_env_csv("TRUSTED_PROXY_IPS", "127.0.0.1,::1"))

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # Third-party apps
    'rest_framework',
    'rest_framework.authtoken',
    
    # Local apps
    'core',
    'catalog',
    'accounts',
    'orders',
    'admin_panel',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'core.middleware.SecurityHeadersMiddleware',
    'core.middleware.RequestIDMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'core.middleware.ActiveCompanyMiddleware',
    'core.middleware.ReadOnlyModeMiddleware',
    'core.middleware.SessionIdleTimeoutMiddleware',
    'core.middleware.AuditRequestContextMiddleware',
    'core.middleware.AuthSessionIsolationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'core.middleware.StaffCapabilityMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'core.middleware.UserActivityMiddleware',
]

ROOT_URLCONF = 'flexs_project.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'builtins': [
                'core.templatetags.global_number_format',
            ],
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'core.context_processors.site_settings',
                'core.context_processors.active_admins',
                'core.context_processors.active_company_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'flexs_project.wsgi.application'

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# Internationalization
LANGUAGE_CODE = 'es-ar'
TIME_ZONE = 'America/Argentina/Buenos_Aires'
USE_I18N = True
USE_TZ = True
USE_THOUSAND_SEPARATOR = True
THOUSAND_SEPARATOR = '.'
DECIMAL_SEPARATOR = ','
NUMBER_GROUPING = 3

# Static files (CSS, JavaScript, Images)
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIR = BASE_DIR / 'static'
STATICFILES_DIRS = [STATICFILES_DIR] if STATICFILES_DIR.exists() else []

# Media files (uploads)
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Login/Logout URLs
LOGIN_URL = '/accounts/login/'
LOGIN_REDIRECT_URL = '/accounts/redirect/'
LOGOUT_REDIRECT_URL = '/'

# Session/cookie security defaults
SESSION_COOKIE_HTTPONLY = os.getenv('DJANGO_SESSION_COOKIE_HTTPONLY', 'True').lower() == 'true'
CSRF_COOKIE_HTTPONLY = os.getenv('DJANGO_CSRF_COOKIE_HTTPONLY', 'False').lower() == 'true'
SESSION_COOKIE_SAMESITE = os.getenv('DJANGO_SESSION_COOKIE_SAMESITE', 'Lax')
CSRF_COOKIE_SAMESITE = os.getenv('DJANGO_CSRF_COOKIE_SAMESITE', 'Lax')
SESSION_COOKIE_SECURE = os.getenv('DJANGO_SESSION_COOKIE_SECURE', str(not DEBUG)).lower() == 'true'
CSRF_COOKIE_SECURE = os.getenv('DJANGO_CSRF_COOKIE_SECURE', str(not DEBUG)).lower() == 'true'
SESSION_COOKIE_AGE = max(_env_int('DJANGO_SESSION_COOKIE_AGE', 60 * 60 * 8), 300)
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = os.getenv('DJANGO_X_FRAME_OPTIONS', 'DENY')
SECURE_REFERRER_POLICY = os.getenv('DJANGO_SECURE_REFERRER_POLICY', 'strict-origin-when-cross-origin')
SECURE_CROSS_ORIGIN_OPENER_POLICY = os.getenv('DJANGO_SECURE_COOP', 'same-origin')
SECURE_CROSS_ORIGIN_RESOURCE_POLICY = os.getenv('DJANGO_SECURE_CORP', 'same-origin')
SECURITY_PERMISSIONS_POLICY = os.getenv(
    "DJANGO_PERMISSIONS_POLICY",
    "accelerometer=(), autoplay=(), camera=(), geolocation=(), gyroscope=(), "
    "magnetometer=(), microphone=(), payment=(), usb=(), browsing-topics=()",
).strip()
SECURITY_CONTENT_SECURITY_POLICY = os.getenv(
    "DJANGO_CONTENT_SECURITY_POLICY",
    "default-src 'self'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'; "
    "object-src 'none'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://unpkg.com https://dolarapi.com; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://unpkg.com; "
    "font-src 'self' https://fonts.gstatic.com data:; "
    "img-src 'self' data: https:; "
    "connect-src 'self' https://fonts.googleapis.com https://fonts.gstatic.com "
    "https://nominatim.openstreetmap.org https://photon.komoot.io https://dolarapi.com; "
    "frame-src 'none';",
).strip()
SECURITY_CONTENT_SECURITY_POLICY_REPORT_ONLY = os.getenv(
    "DJANGO_CONTENT_SECURITY_POLICY_REPORT_ONLY",
    "",
).strip()

# Session idle timeout / login lockout controls
SESSION_IDLE_TIMEOUT_SECONDS = max(_env_int('DJANGO_SESSION_IDLE_TIMEOUT_SECONDS', 60 * 45), 300)
REMEMBER_ME_SESSION_AGE = max(_env_int('DJANGO_REMEMBER_ME_SESSION_AGE', 60 * 60 * 24 * 30), 300)
LOGIN_MAX_FAILED_ATTEMPTS = max(_env_int('DJANGO_LOGIN_MAX_FAILED_ATTEMPTS', 5), 3)
LOGIN_LOCKOUT_SECONDS = max(_env_int('DJANGO_LOGIN_LOCKOUT_SECONDS', 15 * 60), 60)
LOGIN_ATTEMPT_WINDOW_SECONDS = max(_env_int('DJANGO_LOGIN_ATTEMPT_WINDOW_SECONDS', 15 * 60), 60)
PASSWORD_RESET_TIMEOUT = max(_env_int("DJANGO_PASSWORD_RESET_TIMEOUT", 60 * 60 * 24), 300)
PASSWORD_RESET_WINDOW_SECONDS = max(_env_int("DJANGO_PASSWORD_RESET_WINDOW_SECONDS", 60 * 60), 300)
PASSWORD_RESET_MAX_REQUESTS_PER_IP = max(_env_int("DJANGO_PASSWORD_RESET_MAX_REQUESTS_PER_IP", 8), 1)
PASSWORD_RESET_MAX_REQUESTS_PER_EMAIL = max(_env_int("DJANGO_PASSWORD_RESET_MAX_REQUESTS_PER_EMAIL", 4), 1)
ACCOUNT_REQUEST_MAX_SUBMISSIONS = max(_env_int("ACCOUNT_REQUEST_MAX_SUBMISSIONS", 5), 1)
ACCOUNT_REQUEST_WINDOW_SECONDS = max(_env_int("ACCOUNT_REQUEST_WINDOW_SECONDS", 60 * 60), 60)
ACCOUNT_REQUEST_MIN_INTERVAL_SECONDS = max(
    _env_int("ACCOUNT_REQUEST_MIN_INTERVAL_SECONDS", 10),
    0,
)
ACCOUNT_REQUEST_HONEYPOT_FIELD = os.getenv("ACCOUNT_REQUEST_HONEYPOT_FIELD", "website").strip() or "website"
ALERT_PREPARING_STALE_DAYS = max(_env_int('ALERT_PREPARING_STALE_DAYS', 3), 1)
ALERT_HIGH_DEBT_THRESHOLD = _env_int('ALERT_HIGH_DEBT_THRESHOLD', 500000)
ALERT_IMPORT_ERROR_RATE_PERCENT = max(_env_int('ALERT_IMPORT_ERROR_RATE_PERCENT', 30), 1)

# Feature flags (incremental rollout, no-breaking deployments)
FEATURE_API_V1_ENABLED = os.getenv('FEATURE_API_V1_ENABLED', 'True').lower() == 'true'
FEATURE_EXTERNAL_EDITOR_ENABLED = os.getenv('FEATURE_EXTERNAL_EDITOR_ENABLED', 'False').lower() == 'true'
FEATURE_EXTERNAL_EDITOR_WRITES = os.getenv('FEATURE_EXTERNAL_EDITOR_WRITES', 'False').lower() == 'true'
FEATURE_BACKGROUND_JOBS_ENABLED = os.getenv('FEATURE_BACKGROUND_JOBS_ENABLED', 'False').lower() == 'true'
EXTERNAL_EDITOR_SYNC_LIMIT = max(_env_int('EXTERNAL_EDITOR_SYNC_LIMIT', 250), 1)
FEATURE_ADVANCED_SEARCH_ENABLED = os.getenv('FEATURE_ADVANCED_SEARCH_ENABLED', 'False').lower() == 'true'
FEATURE_OBSERVABILITY_ENABLED = os.getenv('FEATURE_OBSERVABILITY_ENABLED', 'False').lower() == 'true'
FEATURE_READ_ONLY_MODE = os.getenv('FEATURE_READ_ONLY_MODE', 'False').lower() == 'true'
ORDER_REQUIRE_PAYMENT_FOR_CONFIRMATION = os.getenv('ORDER_REQUIRE_PAYMENT_FOR_CONFIRMATION', 'False').lower() == 'true'

# ARCA / WSFE integration. Every capability is disabled unless explicitly
# enabled. Endpoint environment values are still checked against the closed
# allowlist in core.services.arca_config before any network I/O.
ARCA_ENABLED = _env_guard_bool("ARCA_ENABLED", False)
ARCA_ENVIRONMENT = str(
    os.getenv("ARCA_ENVIRONMENT", "disabled") or "disabled"
)
ARCA_HOMOLOGATION_NETWORK_ENABLED = _env_guard_bool(
    "ARCA_HOMOLOGATION_NETWORK_ENABLED",
    False,
)
ARCA_HOMOLOGATION_READ_ENABLED = _env_guard_bool(
    "ARCA_HOMOLOGATION_READ_ENABLED",
    False,
)
ARCA_HOMOLOGATION_EMISSION_ENABLED = _env_guard_bool(
    "ARCA_HOMOLOGATION_EMISSION_ENABLED",
    False,
)
ARCA_PRODUCTION_ENABLED = _env_guard_bool(
    "ARCA_PRODUCTION_ENABLED",
    False,
)
READY_ARCA_HOMOLOGACION_READONLY = _env_guard_bool(
    "READY_ARCA_HOMOLOGACION_READONLY",
    False,
)
ARCA_WSASS_AUTHORIZATION_CONFIRMED = _env_guard_bool(
    "ARCA_WSASS_AUTHORIZATION_CONFIRMED",
    False,
)

ARCA_WSAA_URL = str(os.getenv("ARCA_WSAA_URL", "") or "")
ARCA_WSFE_URL = str(os.getenv("ARCA_WSFE_URL", "") or "")
ARCA_WSFE_WSDL = str(os.getenv("ARCA_WSFE_WSDL", "") or "")
ARCA_CONNECT_TIMEOUT_SECONDS = max(
    _env_int("ARCA_CONNECT_TIMEOUT_SECONDS", 10),
    5,
)
ARCA_READ_TIMEOUT_SECONDS = max(_env_int("ARCA_READ_TIMEOUT_SECONDS", 30), 5)
# Compatibility alias used by the existing strict SOAP transport.
ARCA_TIMEOUT_SECONDS = ARCA_READ_TIMEOUT_SECONDS
ARCA_TA_RENEWAL_MARGIN_SECONDS = max(
    _env_int("ARCA_TA_RENEWAL_MARGIN_SECONDS", 120),
    30,
)
ARCA_TLS_VERIFY = _env_guard_bool("ARCA_TLS_VERIFY", True)
ARCA_REDACT_SECRETS = _env_guard_bool("ARCA_REDACT_SECRETS", True)
ARCA_TOKEN_CACHE_ENABLED = _env_guard_bool(
    "ARCA_TOKEN_CACHE_ENABLED",
    False,
)
ARCA_TOKEN_CACHE_BACKEND = str(
    os.getenv("ARCA_TOKEN_CACHE_BACKEND", "") or ""
)
ARCA_TOKEN_CACHE_URL = str(
    os.getenv("ARCA_TOKEN_CACHE_URL", "") or ""
)
ARCA_TOKEN_CACHE_PREFIX = str(
    os.getenv(
        "ARCA_TOKEN_CACHE_PREFIX",
        "webflexs:arca:homo",
    )
)
ARCA_TOKEN_CACHE_PATH = str(
    os.getenv("ARCA_TOKEN_CACHE_PATH", "") or ""
).strip()

ARCA_CREDENTIAL_ID = str(os.getenv("ARCA_CREDENTIAL_ID", "") or "").strip()
ARCA_SERVICE_ID = str(os.getenv("ARCA_SERVICE_ID", "") or "").strip()
ARCA_CUIT = str(os.getenv("ARCA_CUIT", "") or "").strip()
ARCA_PTO_VTA = str(os.getenv("ARCA_PTO_VTA", "") or "").strip()
ARCA_DEFAULT_CBTE_TIPO = str(
    os.getenv("ARCA_DEFAULT_CBTE_TIPO", "") or ""
).strip()
ARCA_CERT_PATH = str(os.getenv("ARCA_CERT_PATH", "") or "").strip()
ARCA_PRIVATE_KEY_PATH = str(
    os.getenv("ARCA_PRIVATE_KEY_PATH", "") or ""
).strip()
ARCA_PRIVATE_KEY_PASSPHRASE_FILE = str(
    os.getenv("ARCA_PRIVATE_KEY_PASSPHRASE_FILE", "") or ""
).strip()
ARCA_EXPECTED_CERT_SHA256 = str(
    os.getenv("ARCA_EXPECTED_CERT_SHA256", "") or ""
).strip()

ARCA_OPENSSL_BIN = "openssl"
# The identifier must be confirmed by the user in WSASS; there is no fallback.
ARCA_WSAA_SERVICE = ARCA_SERVICE_ID
ARCA_WSAA_LOCK_SECONDS = max(_env_int("ARCA_WSAA_LOCK_SECONDS", 60), 30)
ARCA_WSAA_WAIT_SECONDS = max(_env_int("ARCA_WSAA_WAIT_SECONDS", 5), 1)
# JSON backend-only. Every entry must carry an explicit environment label.
# The legacy variable is read only as a migration bridge; unsafe/untagged
# entries are rejected by the credential loader.
ARCA_COMPANY_CONFIG = _env_json(
    "ARCA_CREDENTIALS_CONFIG_JSON",
    _env_json("ARCA_COMPANY_CONFIG_JSON", {}),
)

# Fiscal emission behavior tuning
FISCAL_RETRY_MINUTES = max(_env_int("FISCAL_RETRY_MINUTES", 10), 1)
FISCAL_MAX_AUTO_RETRIES = max(_env_int("FISCAL_MAX_AUTO_RETRIES", 5), 1)
FISCAL_SUBMITTING_TIMEOUT_MINUTES = max(_env_int("FISCAL_SUBMITTING_TIMEOUT_MINUTES", 20), 5)

# ARCA numbering synchronization policy: never | first | always
FISCAL_ARCA_LAST_AUTH_SYNC_POLICY = str(
    os.getenv("FISCAL_ARCA_LAST_AUTH_SYNC_POLICY", "first") or "first"
).strip().lower()
if FISCAL_ARCA_LAST_AUTH_SYNC_POLICY not in {"never", "first", "always"}:
    FISCAL_ARCA_LAST_AUTH_SYNC_POLICY = "first"
FISCAL_ARCA_REQUIRE_LAST_AUTH_SYNC = os.getenv(
    "FISCAL_ARCA_REQUIRE_LAST_AUTH_SYNC",
    "False",
).lower() == "true"

# Automatic item tax breakdown for fiscal docs:
# - net: order line prices are net and IVA is added
# - gross: order line prices are final and IVA is split out
FISCAL_AUTO_ITEM_TAX_ENABLED = os.getenv("FISCAL_AUTO_ITEM_TAX_ENABLED", "True").lower() == "true"
FISCAL_ITEM_TAX_CALCULATION_MODE = str(
    os.getenv("FISCAL_ITEM_TAX_CALCULATION_MODE", "net") or "net"
).strip().lower()
if FISCAL_ITEM_TAX_CALCULATION_MODE not in {"net", "gross"}:
    FISCAL_ITEM_TAX_CALCULATION_MODE = "net"
FISCAL_APPLY_TAX_TO_MANUAL_DOCS = os.getenv("FISCAL_APPLY_TAX_TO_MANUAL_DOCS", "False").lower() == "true"
FISCAL_DOC_TYPE_DEFAULT_IVA_RATES = _env_json(
    "FISCAL_DOC_TYPE_DEFAULT_IVA_RATES_JSON",
    {
        "FA": "21.00",
        "FB": "21.00",
        "FC": "0.00",
        "NCA": "21.00",
        "NCB": "21.00",
        "NCC": "0.00",
        "NDA": "21.00",
        "NDB": "21.00",
        "NDC": "0.00",
    },
)

# Django REST Framework
REST_FRAMEWORK = {
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 20,
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.SessionAuthentication',
        'rest_framework.authentication.TokenAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
        'rest_framework.throttling.ScopedRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': os.getenv('DRF_RATE_ANON', '60/min'),
        'user': os.getenv('DRF_RATE_USER', '240/min'),
        'api_v1_default': os.getenv('DRF_RATE_API_DEFAULT', '180/min'),
        'api_v1_catalog': os.getenv('DRF_RATE_API_CATALOG', '240/min'),
        'api_v1_admin': os.getenv('DRF_RATE_API_ADMIN', '300/min'),
    },
}

# Email configuration (prepared for future use)
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'  # Prints to console
EMAIL_HOST = os.getenv('EMAIL_HOST', 'smtp.gmail.com')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', 587))
EMAIL_USE_TLS = os.getenv('EMAIL_USE_TLS', 'True').lower() == 'true'
EMAIL_USE_SSL = os.getenv('EMAIL_USE_SSL', 'False').lower() == 'true'
if EMAIL_USE_TLS and EMAIL_USE_SSL:
    EMAIL_USE_SSL = False
EMAIL_TIMEOUT = max(_env_int('EMAIL_TIMEOUT', 20), 1)
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'ventas@flexs.com.ar')
SERVER_EMAIL = os.getenv('SERVER_EMAIL', DEFAULT_FROM_EMAIL)

# CORS settings
CORS_ALLOW_ALL_ORIGINS = DEBUG

# Presence / activity tuning
ADMIN_ONLINE_WINDOW_SECONDS = max(_env_int("ADMIN_ONLINE_WINDOW_SECONDS", 300), 30)
ADMIN_IDLE_WINDOW_SECONDS = max(_env_int("ADMIN_IDLE_WINDOW_SECONDS", 90), 15)
ADMIN_PRESENCE_TOUCH_INTERVAL_SECONDS = max(
    _env_int("ADMIN_PRESENCE_TOUCH_INTERVAL_SECONDS", 30),
    5,
)
ADMIN_PRESENCE_REFRESH_SECONDS = max(_env_int("ADMIN_PRESENCE_REFRESH_SECONDS", 30), 10)
ADMIN_PRESENCE_EXCLUDED_USERS = tuple(
    username.strip()
    for username in os.getenv("ADMIN_PRESENCE_EXCLUDED_USERS", "admin,admin_tester").split(",")
    if username.strip()
)

# Optional shared cache (recommended in production with multiple workers)
REDIS_URL = os.getenv("REDIS_URL", "").strip()
if ARCA_TOKEN_CACHE_BACKEND == "redis":
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": ARCA_TOKEN_CACHE_URL,
            "TIMEOUT": 300,
        }
    }
elif ARCA_TOKEN_CACHE_BACKEND == "memcached":
    CACHES = {
        "default": {
            "BACKEND": (
                "django.core.cache.backends.memcached.PyMemcacheCache"
            ),
            "LOCATION": ARCA_TOKEN_CACHE_URL,
            "TIMEOUT": 300,
        }
    }
elif REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
            "TIMEOUT": 300,
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "flexs-local-cache",
            "TIMEOUT": 300,
        }
    }

IMPORT_MAX_FILE_SIZE_BYTES = max(
    _env_int("IMPORT_MAX_FILE_SIZE_BYTES", 10 * 1024 * 1024),
    1024 * 1024,
)
IMPORT_ALLOWED_CONTENT_TYPES = tuple(
    _env_csv(
        "IMPORT_ALLOWED_CONTENT_TYPES",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,"
        "application/octet-stream,application/zip",
    )
)

# Background jobs (Phase 2)
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", REDIS_URL or "redis://127.0.0.1:6379/0")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", CELERY_BROKER_URL)
CELERY_TASK_DEFAULT_QUEUE = os.getenv("CELERY_TASK_DEFAULT_QUEUE", "flexs-default")
CELERY_TASK_ALWAYS_EAGER = os.getenv("CELERY_TASK_ALWAYS_EAGER", "False").lower() == "true"
CELERY_TASK_EAGER_PROPAGATES = os.getenv("CELERY_TASK_EAGER_PROPAGATES", "False").lower() == "true"

from celery.schedules import crontab
CELERY_BEAT_SCHEDULE = {
    "retry_stuck_fiscal_documents": {
        "task": "core.retry_stuck_fiscal_documents_task",
        "schedule": crontab(minute="*/10"),
    },
    "automatic_system_backup": {
        "task": "core.create_automatic_backup_task",
        "schedule": crontab(
            hour=max(min(_env_int("BACKUP_SCHEDULE_HOUR", 2), 23), 0),
            minute=max(min(_env_int("BACKUP_SCHEDULE_MINUTE", 30), 59), 0),
        ),
    },
    "retry_pending_webhooks": {
        "task": "core.retry_pending_webhooks_task",
        "schedule": crontab(minute="*/5"),
    },
}

BACKUP_ROOT = Path(os.getenv("BACKUP_ROOT", str(BASE_DIR / "backups" / "automatic")))
BACKUP_RETENTION_DAYS = max(_env_int("BACKUP_RETENTION_DAYS", 30), 1)
BACKUP_INCLUDE_MEDIA = os.getenv("BACKUP_INCLUDE_MEDIA", "True").lower() == "true"
WEBHOOK_ALLOW_INSECURE_URLS = os.getenv("WEBHOOK_ALLOW_INSECURE_URLS", str(DEBUG)).lower() == "true"
WEBHOOK_ALLOW_PRIVATE_TARGETS = os.getenv("WEBHOOK_ALLOW_PRIVATE_TARGETS", "False").lower() == "true"
WEBHOOK_MAX_ATTEMPTS = max(_env_int("WEBHOOK_MAX_ATTEMPTS", 6), 1)
WEBHOOK_TIMEOUT_SECONDS = max(_env_int("WEBHOOK_TIMEOUT_SECONDS", 10), 1)

# Logging / observability (Phase 5)
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "redact_sensitive": {
            "()": "core.services.sensitive_data.SensitiveDataFilter",
        },
    },
    "formatters": {
        "verbose": {
            "()": "core.services.sensitive_data.RedactingFormatter",
            "format": "[%(asctime)s] %(levelname)s %(name)s %(message)s",
        },
        "simple": {
            "()": "core.services.sensitive_data.RedactingFormatter",
            "format": "%(levelname)s %(name)s %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose" if not DEBUG else "simple",
            "filters": ["redact_sensitive"],
        },
    },
    "root": {
        "handlers": ["console"],
        "level": LOG_LEVEL,
    },
}

if FEATURE_OBSERVABILITY_ENABLED:
    sentry_dsn = os.getenv("SENTRY_DSN", "").strip()
    if sentry_dsn:
        try:
            import sentry_sdk
            from sentry_sdk.integrations.django import DjangoIntegration
            from core.services.sensitive_data import sanitize_sentry_event

            sentry_sdk.init(
                dsn=sentry_dsn,
                integrations=[DjangoIntegration()],
                traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.05")),
                send_default_pii=False,
                environment=os.getenv("SENTRY_ENVIRONMENT", "local" if DEBUG else "production"),
                before_send=sanitize_sentry_event,
            )
        except Exception:
            # Never fail startup because of optional observability integration.
            pass
