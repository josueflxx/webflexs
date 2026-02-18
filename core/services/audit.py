"""
Audit helpers for admin actions.
"""
from core.models import AdminAuditLog


def log_admin_action(request, action, target_type="", target_id="", details=None):
    """
    Persist an admin audit event. Fail-safe by design.
    """
    try:
        user = getattr(request, "user", None)
        if user and not user.is_authenticated:
            user = None

        AdminAuditLog.objects.create(
            user=user,
            action=str(action or "")[:120],
            target_type=str(target_type or "")[:80],
            target_id=str(target_id or "")[:120],
            details=details or {},
        )
    except Exception:
        # Audit should never break business flow.
        return
