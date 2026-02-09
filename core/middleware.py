"""
User activity tracking middleware.
"""
from django.utils import timezone
from core.models import UserActivity


class UserActivityMiddleware:
    """Update user activity on each request."""
    
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        if request.user.is_authenticated and request.user.is_staff:
            UserActivity.objects.update_or_create(
                user=request.user,
                defaults={
                    'last_activity': timezone.now(),
                    'is_online': True
                }
            )
        
        response = self.get_response(request)
        return response
