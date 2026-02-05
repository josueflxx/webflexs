"""
Context processors for site-wide data.
"""
from .models import SiteSettings


def site_settings(request):
    """Add site settings to all template contexts."""
    return {
        'site_settings': SiteSettings.get_settings()
    }
