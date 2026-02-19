"""
Context processors for site-wide data.
"""
from .models import SiteSettings
from core.services.presence import build_admin_presence_payload, get_presence_config


def site_settings(request):
    """Add site settings to all template contexts."""
    return {
        'site_settings': SiteSettings.get_settings()
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

