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
        # Show all staff members (Admins + Operators)
        # We filter by is_staff=True to include Operators
        # Exclude specific testing accounts as requested
        excluded_usernames = ['admin', 'admin_tester']
        
        admins = User.objects.filter(
            is_staff=True,
            is_active=True
        ).exclude(
            username__in=excluded_usernames
        ).select_related('activity').order_by('username')
        return {'active_admins': admins}
    return {}

