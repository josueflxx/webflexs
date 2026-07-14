"""Custom permissions for API v1."""

from rest_framework.permissions import BasePermission


class IsStaffUser(BasePermission):
    """Allow access only to authenticated staff users."""

    message = "Solo personal administrativo autorizado."

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        return bool(user and user.is_authenticated and user.is_staff)


class HasAdminCapability(BasePermission):
    """Require the capability declared by ``view.required_capability``."""

    message = "No tienes el permiso operativo requerido."

    def has_permission(self, request, view):
        from core.services.authorization import has_capability

        capability = getattr(view, "required_capability", "")
        return bool(capability and has_capability(getattr(request, "user", None), capability))


class HasRequiredCapabilityWhenStaff(BasePermission):
    """Keep client endpoints available while requiring a capability from staff."""

    message = "No tienes el permiso operativo requerido."

    def has_permission(self, request, view):
        from core.services.authorization import has_capability

        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return False
        if not user.is_staff:
            return True
        capability = getattr(view, "required_staff_capability", "")
        return bool(capability and has_capability(user, capability))
