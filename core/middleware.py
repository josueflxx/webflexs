"""
User activity tracking middleware.
"""
from django.utils import timezone
from django.core.cache import cache
from django.db import DatabaseError
from core.models import UserActivity


class UserActivityMiddleware:
    """Update user activity on each request."""
    
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        if request.user.is_authenticated and request.user.is_staff:
            # Avoid hammering SQLite with one write per request.
            touch_key = f"user-activity-touch:{request.user.pk}"
            should_touch = cache.get(touch_key) is None
            if should_touch:
                cache.set(touch_key, True, timeout=30)
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
