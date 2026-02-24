"""
Audit helpers for admin actions.
"""
from datetime import date, datetime
from decimal import Decimal

from django.db.models import Model

from core.models import AdminAuditLog
from core.services.audit_context import get_audit_meta


def _serialize_value(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Model):
        return value.pk
    if isinstance(value, dict):
        return {str(k): _serialize_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_serialize_value(v) for v in value]
    return value


def model_snapshot(instance, fields):
    """
    Return a JSON-serializable snapshot for selected model fields.
    """
    if instance is None:
        return {}

    snapshot = {}
    for field_name in fields:
        snapshot[field_name] = _serialize_value(getattr(instance, field_name, None))
    return snapshot


def log_admin_change(request, action, target_type="", target_id="", before=None, after=None, extra=None):
    """
    Log auditable changes using a standard before/after envelope.
    """
    details = {
        "before": _serialize_value(before or {}),
        "after": _serialize_value(after or {}),
    }
    if extra:
        details.update(_serialize_value(extra))
    log_admin_action(
        request=request,
        action=action,
        target_type=target_type,
        target_id=target_id,
        details=details,
    )


def log_admin_action(request, action, target_type="", target_id="", details=None):
    """
    Persist an admin audit event. Fail-safe by design.
    """
    try:
        user = getattr(request, "user", None)
        if user and not user.is_authenticated:
            user = None

        meta = get_audit_meta()
        payload = details or {}
        if meta:
            payload = {
                **payload,
                "meta": {
                    "ip_address": meta.get("ip_address", ""),
                    "user_agent": meta.get("user_agent", ""),
                },
            }

        AdminAuditLog.objects.create(
            user=user,
            action=str(action or "")[:120],
            target_type=str(target_type or "")[:80],
            target_id=str(target_id or "")[:120],
            details=payload,
        )
    except Exception:
        # Audit should never break business flow.
        return
