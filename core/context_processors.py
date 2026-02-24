"""
Context processors for site-wide data.
"""
from .models import SiteSettings
from core.services.presence import build_admin_presence_payload, get_presence_config


def _can_use_clamp_measure_feature(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return False
    if user.is_staff:
        return True
    profile = getattr(user, "client_profile", None)
    return bool(profile and getattr(profile, "is_approved", False))


def site_settings(request):
    """Add site settings to all template contexts."""
    return {
        'site_settings': SiteSettings.get_settings(),
        'can_use_clamp_measure': _can_use_clamp_measure_feature(request),
    }


def active_admins(request):
    """Add list of active admin users to all templates."""
    if request.user.is_authenticated and request.user.is_staff:
        config = get_presence_config()
        admins = build_admin_presence_payload()
        return {
            "active_admins": admins,
            "admin_presence_refresh_seconds": config["refresh_seconds"],
            "admin_online_window_seconds": config["online_window_seconds"],
        }
    return {}

