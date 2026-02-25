"""Project package init."""

try:
    from .celery import app as celery_app  # noqa: F401
except Exception:
    # Celery is optional in local/dev environments.
    celery_app = None
