"""Database-backed ARCA parameter validation.

The database rows must come from a versioned fixture or a future authenticated
parameter synchronization. Application code does not treat a hard-coded list as
the definitive fiscal catalog.
"""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone

from core.models import (
    ArcaReceiverIvaConditionParameter,
    ArcaVatRateParameter,
)
from core.services.fiscal_integrity import quantize_rate


def _currently_valid(queryset, *, at=None):
    at = at or timezone.now()
    return queryset.filter(is_active=True).filter(
        Q(valid_from__isnull=True) | Q(valid_from__lte=at),
        Q(valid_until__isnull=True) | Q(valid_until__gte=at),
    )


def voucher_class_for_doc_type(doc_type: str) -> str:
    normalized = str(doc_type or "").strip().upper()
    if normalized.endswith("A"):
        return "A"
    if normalized.endswith("B"):
        return "B"
    if normalized.endswith("C"):
        return "C"
    raise ValidationError("El tipo fiscal no permite determinar la clase del comprobante.")


def get_receiver_iva_condition(*, arca_id, doc_type, at=None):
    if arca_id in (None, ""):
        raise ValidationError(
            "El cliente no tiene CondicionIVAReceptorId validada para facturacion ARCA."
        )
    condition = _currently_valid(
        ArcaReceiverIvaConditionParameter.objects.all(),
        at=at,
    ).filter(arca_id=arca_id).first()
    if not condition:
        raise ValidationError(
            "La condicion IVA ARCA del receptor no existe, esta inactiva o fuera de vigencia."
        )
    allowed = {
        str(value).strip().upper()
        for value in (condition.voucher_classes or [])
        if str(value).strip()
    }
    normalized_doc_type = str(doc_type or "").strip().upper()
    voucher_class = voucher_class_for_doc_type(normalized_doc_type)
    if allowed and normalized_doc_type not in allowed and voucher_class not in allowed:
        raise ValidationError(
            f"La condicion IVA {condition.description} no es compatible con comprobantes clase {voucher_class}."
        )
    return condition


def validate_client_receiver_iva(*, client_profile, doc_type, at=None):
    if not client_profile:
        raise ValidationError("El comprobante fiscal no tiene receptor asociado.")
    source = str(getattr(client_profile, "iva_condition_source", "") or "")
    if source in {"", "unknown", "legacy_pending"}:
        raise ValidationError(
            "La condicion IVA del cliente esta pendiente de validacion fiscal."
        )
    if not getattr(client_profile, "iva_condition_validated_at", None):
        raise ValidationError(
            "La condicion IVA del cliente no tiene fecha de validacion."
        )
    return get_receiver_iva_condition(
        arca_id=getattr(client_profile, "iva_condition_arca_id", None),
        doc_type=doc_type,
        at=at,
    )


def get_vat_rate_parameter(rate: Decimal, *, at=None):
    normalized_rate = quantize_rate(rate)
    parameter = _currently_valid(
        ArcaVatRateParameter.objects.all(),
        at=at,
    ).filter(rate=normalized_rate).first()
    if not parameter:
        raise ValidationError(
            f"La alicuota IVA {normalized_rate}% no esta validada en el catalogo de parametros ARCA."
        )
    return parameter

