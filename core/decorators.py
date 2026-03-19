from functools import wraps
from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect

PRIMARY_SUPERADMIN_USERNAME = getattr(settings, "ADMIN_PRIMARY_SUPERADMIN_USERNAME", "josueflexs")


def superuser_required_for_modifications(view_func):
    """
    Decorator that strictly forbids non-superusers from making 
    state-changing requests (POST, PUT, DELETE).
    """
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        # Allow safe methods
        if request.method in ['GET', 'HEAD', 'OPTIONS']:
            return view_func(request, *args, **kwargs)
        
        # For unsafe methods, allow only primary superadmin.
        if (
            request.user.is_superuser
            and str(getattr(request.user, "username", "")).strip().lower()
            == str(PRIMARY_SUPERADMIN_USERNAME).strip().lower()
        ):
            return view_func(request, *args, **kwargs)
        
        # If not superuser, deny access
        messages.error(
            request,
            f'No tienes permisos para realizar modificaciones. Contacta al administrador principal ({PRIMARY_SUPERADMIN_USERNAME}).',
        )
        
        # Redirect back to previous page or dashboard if referer is missing
        referer = request.META.get('HTTP_REFERER')
        if referer:
            return redirect(referer)
        return redirect('admin_dashboard')
        
    return _wrapped_view
