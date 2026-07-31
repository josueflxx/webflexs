from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'

    def ready(self):
        # Register audit signal handlers.
        import core.signals  # noqa: F401
        # Register fail-safe integration checks.
        import core.checks  # noqa: F401
