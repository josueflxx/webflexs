"""
Core app views.
"""
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt


def home(request):
    """Home page view."""
    return render(request, 'core/home.html')


@csrf_exempt
@require_POST
def go_offline(request):
    """Mark user as offline (called via beacon on page close)."""
    if request.user.is_authenticated and request.user.is_staff:
        from core.models import UserActivity
        UserActivity.objects.filter(user=request.user).update(is_online=False)
        return JsonResponse({'status': 'ok'})
    return JsonResponse({'status': 'ignored'})
