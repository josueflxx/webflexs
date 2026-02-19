"""
Automatic entity-level audit logging for key ERP models.
"""
from decimal import Decimal
from threading import local

from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from accounts.models import ClientProfile
from catalog.models import Category, Product, Supplier
from core.models import AdminAuditLog
from core.services.audit_context import get_audit_actor, get_audit_meta
from orders.models import Order


MONITORED_MODELS = (Product, Category, Supplier, Order, ClientProfile)
_cache = local()


def _normalize_value(value):
    if value is None:
        return None
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    return value


def _serialize_instance(instance):
    data = {}
    for field in instance._meta.concrete_fields:
        data[field.name] = _normalize_value(getattr(instance, field.attname))
    return data


def _cache_key(model, pk):
    return f"{model._meta.label_lower}:{pk}"


def _get_snapshots():
    if not hasattr(_cache, "snapshots"):
        _cache.snapshots = {}
    return _cache.snapshots


@receiver(pre_save)
def audit_pre_save(sender, instance, **kwargs):
    if sender not in MONITORED_MODELS:
        return
    if not instance.pk:
        return
    current = sender.objects.filter(pk=instance.pk).first()
    if not current:
        return
    snapshots = _get_snapshots()
    snapshots[_cache_key(sender, instance.pk)] = _serialize_instance(current)


@receiver(post_save)
def audit_post_save(sender, instance, created, **kwargs):
    if sender not in MONITORED_MODELS:
        return

    user = get_audit_actor()
    meta = get_audit_meta()
    after_data = _serialize_instance(instance)
    details = {"after": after_data, **meta}

    action = "entity_create" if created else "entity_update"
    if not created:
        snapshots = _get_snapshots()
        before_data = snapshots.pop(_cache_key(sender, instance.pk), {})
        changed = {}
        for key, before_value in before_data.items():
            after_value = after_data.get(key)
            if before_value != after_value:
                changed[key] = {"before": before_value, "after": after_value}
        details["before"] = before_data
        details["changed_fields"] = changed
        if not changed:
            return

    AdminAuditLog.objects.create(
        user=user,
        action=action,
        target_type=sender._meta.label_lower,
        target_id=str(instance.pk),
        details=details,
    )


@receiver(post_delete)
def audit_post_delete(sender, instance, **kwargs):
    if sender not in MONITORED_MODELS:
        return
    user = get_audit_actor()
    meta = get_audit_meta()
    AdminAuditLog.objects.create(
        user=user,
        action="entity_delete",
        target_type=sender._meta.label_lower,
        target_id=str(instance.pk),
        details={"before": _serialize_instance(instance), **meta},
    )
