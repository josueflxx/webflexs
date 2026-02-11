from functools import wraps
from django.contrib import messages
from django.shortcuts import redirect
from django.http import HttpResponseForbidden

def superuser_required_for_modifications(view_func):
    """
    Decorator that strictly forbids non-superusers from making 
    state-changing requests (POST, PUT, DELETE).
    GET, HEAD, OPTIONS are allowed if the user is otherwise authorized.
    """
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        # Allow safe methods
        if request.method in ['GET', 'HEAD', 'OPTIONS']:
            return view_func(request, *args, **kwargs)
        
        # For unsafe methods (POST, DELETE, etc.), check superuser status
        if request.user.is_superuser:
            return view_func(request, *args, **kwargs)
        
        # If not superuser, deny access
        messages.error(request, "No tienes permisos para realizar modificaciones. Contacta al administrador principal (josueflexs).")
        
        # Redirect back to previous page or dashboard if referer is missing
        referer = request.META.get('HTTP_REFERER')
        if referer:
            return redirect(referer)
        return redirect('admin_dashboard')
        
    return _wrapped_view
