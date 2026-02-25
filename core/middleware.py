"""
User activity tracking middleware.
"""
import uuid
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth import logout
from django.conf import settings
from django.utils import timezone
from django.utils.cache import patch_vary_headers
from django.core.cache import cache
from django.db import DatabaseError
from django.shortcuts import redirect
from django.urls import reverse
from core.models import UserActivity
from core.services.audit_context import clear_request_context, set_request_context


class RequestIDMiddleware:
    """
    Inject stable request id for tracing across logs/responses.
    """

    HEADER_NAME = "X-Request-ID"
    META_KEY = "HTTP_X_REQUEST_ID"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        incoming = str(request.META.get(self.META_KEY, "")).strip()
        request_id = incoming or str(uuid.uuid4())
        request.request_id = request_id
        response = self.get_response(request)
        response[self.HEADER_NAME] = request_id
        return response


class SessionIdleTimeoutMiddleware:
    """
    Expire authenticated sessions after inactivity timeout.
    """

    SESSION_TS_KEY = "_last_activity_ts"
    EXCLUDED_PREFIXES = ("/accounts/login/", "/accounts/logout/", "/static/", "/media/")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and not request.path.startswith(self.EXCLUDED_PREFIXES):
            now_ts = int(timezone.now().timestamp())
            timeout_seconds = max(int(getattr(settings, "SESSION_IDLE_TIMEOUT_SECONDS", 2700)), 300)
            last_activity_ts = int(request.session.get(self.SESSION_TS_KEY, now_ts))

            if now_ts - last_activity_ts > timeout_seconds:
                logout(request)
                messages.info(request, "Tu sesion expiro por inactividad. Inicia sesion nuevamente.")
                login_url = reverse("login")
                params = urlencode({"next": request.get_full_path()})
                return redirect(f"{login_url}?{params}")

            request.session[self.SESSION_TS_KEY] = now_ts

        return self.get_response(request)


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
