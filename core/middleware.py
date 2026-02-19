"""
User activity tracking middleware.
"""
from django.conf import settings
from django.utils import timezone
from django.utils.cache import patch_vary_headers
from django.core.cache import cache
from django.db import DatabaseError
from core.models import UserActivity
from core.services.audit_context import clear_request_context, set_request_context


class UserActivityMiddleware:
    """Update user activity on each request."""
    
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        if request.user.is_authenticated and request.user.is_staff:
            tracked_prefixes = ("/admin-panel/", "/api/admin-presence/")
            if not request.path.startswith(tracked_prefixes):
                return self.get_response(request)

            # Avoid hammering SQLite with one write per request.
            touch_key = f"user-activity-touch:{request.user.pk}"
            touch_interval = max(
                int(getattr(settings, "ADMIN_PRESENCE_TOUCH_INTERVAL_SECONDS", 30)),
                5,
            )
            should_touch = cache.add(
                touch_key,
                True,
                timeout=touch_interval,
            )
            if should_touch:
                try:
                    UserActivity.objects.update_or_create(
                        user=request.user,
                        defaults={
                            'last_activity': timezone.now(),
                            'is_online': True
                        }
                    )
                except DatabaseError:
                    # Activity tracking must never break main request flow.
                    pass

        response = self.get_response(request)
        return response


class AuditRequestContextMiddleware:
    """
    Expose current request metadata to signal-based audit logging.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        set_request_context(request)
        try:
            response = self.get_response(request)
        finally:
            clear_request_context()
        return response


class AuthSessionIsolationMiddleware:
    """
    Prevent shared/proxy cache from serving authenticated pages across users.
    """

    SENSITIVE_PREFIXES = (
        "/accounts/",
        "/admin-panel/",
        "/pedidos/",
        "/catalogo/",
        "/api/",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        is_sensitive_path = request.path.startswith(self.SENSITIVE_PREFIXES)
        if request.user.is_authenticated or is_sensitive_path:
            patch_vary_headers(response, ("Cookie",))
            response["Cache-Control"] = "private, no-store, no-cache, must-revalidate, max-age=0"
            response["Pragma"] = "no-cache"
            response["Expires"] = "0"

        return response
