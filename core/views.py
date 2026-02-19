"""
Core app views.
"""
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.views.decorators.http import require_GET
from django.utils import timezone

from core.models import UserActivity
from core.services.presence import build_admin_presence_payload, get_presence_config


def home(request):
    """Home page view."""
    return render(request, 'core/home.html')


@require_GET
def admin_presence(request):
    """Live presence payload for admin sidebar."""
    if not request.user.is_authenticated or not request.user.is_staff:
        return JsonResponse({"detail": "forbidden"}, status=403)

    config = get_presence_config()
    return JsonResponse(
        {
            "admins": build_admin_presence_payload(),
            "refresh_seconds": config["refresh_seconds"],
            "online_window_seconds": config["online_window_seconds"],
        }
    )


@require_POST
def go_offline(request):
    """Mark user as offline (called via beacon on page close)."""
    if request.user.is_authenticated and request.user.is_staff:
        UserActivity.objects.update_or_create(
            user=request.user,
            defaults={
                "is_online": False,
                "last_activity": timezone.now(),
            },
        )
        return JsonResponse({'status': 'ok'})
    return JsonResponse({'status': 'ignored'})
