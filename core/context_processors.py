"""
Context processors for site-wide data.
"""
from .models import SiteSettings


def site_settings(request):
    """Add site settings to all template contexts."""
    return {
        'site_settings': SiteSettings.get_settings()
    }


def active_admins(request):
    """Add list of active admin users to all templates."""
    from django.contrib.auth.models import User
    if request.user.is_authenticated and request.user.is_staff:
        # Only show these 4 specific admin accounts
        admin_usernames = ['josueflexs', 'fedeflexs', 'ricardoroces', 'brianroces']
        admins = User.objects.filter(
            username__in=admin_usernames
        ).select_related('activity').order_by('username')
        return {'active_admins': admins}
    return {}

