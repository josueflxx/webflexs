"""Fiscal readiness helpers (pre-ARCA)."""
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import os
from typing import List, Tuple

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from core.models import (
    FISCAL_CREDIT_NOTE_DOC_TYPES,
    FISCAL_DOC_TYPE_FA,
    FISCAL_DOC_TYPE_FB,
    FISCAL_DOC_TYPE_FC,
    FISCAL_DOC_TYPE_NCA,
    FISCAL_DOC_TYPE_NCB,
    FISCAL_DOC_TYPE_NCC,
    FISCAL_STATUS_AUTHORIZED,
    FISCAL_STATUS_EXTERNAL_RECORDED,
    FISCAL_STATUS_VOIDED,
    FiscalPointOfSale,
)


def _resolve_company_cfg(all_cfg, company):
    if not isinstance(all_cfg, dict):
        return {}

    slug = str(getattr(company, "slug", "") or "").strip()
    company_id = str(getattr(company, "id", "") or "").strip()

    if slug and isinstance(all_cfg.get(slug), dict):
        return all_cfg.get(slug) or {}
    if company_id and isinstance(all_cfg.get(company_id), dict):
        return all_cfg.get(company_id) or {}

    slug_l = slug.lower()
    id_l = company_id.lower()
    for key, value in all_cfg.items():
        if not isinstance(value, dict):
            continue
        key_l = str(key).strip().lower()
        if slug_l and key_l == slug_l:
            return value
        if id_l and key_l == id_l:
            return value
    return {}


def _get_company_arca_env_config(company, environment: str):
    all_cfg = getattr(settings, "ARCA_COMPANY_CONFIG", {}) or {}
    company_cfg = _resolve_company_cfg(all_cfg, company)
    if not isinstance(company_cfg, dict):
        return {}
    env_key = str(environment or "homologation").strip().lower() or "homologation"
    if env_key in company_cfg and isinstance(company_cfg.get(env_key), dict):
        return company_cfg.get(env_key) or {}
    return company_cfg if isinstance(company_cfg, dict) else {}


def _is_blank(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


def _to_decimal(value):
    if value is None:
        return Decimal("0.00")
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0.00")


def _normalize_digits(value) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _resolve_order_sale_condition(order) -> str:
    """
    Resolve commercial sale condition for due-date defaults.
    account -> cuenta corriente
    cash -> contado
    """
    if not order:
        return "account"
    client_company = getattr(order, "client_company_ref", None)
    client_category = getattr(client_company, "client_category", None)
    if client_category and getattr(client_category, "default_sale_condition", ""):
        return str(client_category.default_sale_condition).strip().lower()
    if str(getattr(order, "billing_mode", "")).strip().lower() == "official":
        return "account"
    return "cash"


def resolve_payment_due_date(*, order=None, issued_at=None):
    """
    Compute a default due date for fiscal documents.
    The result is a snapshot value and should be persisted on FiscalDocument.
    """
    if isinstance(issued_at, datetime):
        base_date = timezone.localtime(issued_at).date()
    elif isinstance(issued_at, date):
        base_date = issued_at
    else:
        base_date = timezone.localdate()

    sale_condition = _resolve_order_sale_condition(order)
    if sale_condition == "cash":
        due_days = int(getattr(settings, "FISCAL_CASH_DUE_DAYS", 0) or 0)
    else:
        due_days = int(getattr(settings, "FISCAL_ACCOUNT_DUE_DAYS", 30) or 30)
    if due_days < 0:
        due_days = 0
    return base_date + timedelta(days=due_days)


def _append_order_amount_errors(order, errors: List[str]):
    items_qs = getattr(order, "items", None)
    if items_qs is None:
        errors.append("El pedido no expone detalle de items para validar factura.")
        return

    items = list(items_qs.all())
    if not items:
        errors.append("El pedido no tiene items para facturar.")
        return

    calculated_subtotal = Decimal("0.00")
    for idx, item in enumerate(items, start=1):
        quantity = _to_decimal(getattr(item, "quantity", 0))
        unit_price = _to_decimal(getattr(item, "price_at_purchase", 0))
        if quantity <= 0:
            errors.append(f"Item #{idx} con cantidad invalida.")
        if unit_price < 0:
            errors.append(f"Item #{idx} con precio unitario invalido.")
        line_total = _to_decimal(getattr(item, "subtotal", None))
        if line_total <= 0 and quantity > 0 and unit_price >= 0:
            line_total = (unit_price * quantity).quantize(Decimal("0.01"))
        calculated_subtotal += line_total

    calculated_subtotal = calculated_subtotal.quantize(Decimal("0.01"))
    order_subtotal = _to_decimal(getattr(order, "subtotal", 0)).quantize(Decimal("0.01"))
    order_discount = _to_decimal(getattr(order, "discount_amount", 0)).quantize(Decimal("0.01"))
    order_total = _to_decimal(getattr(order, "total", 0)).quantize(Decimal("0.01"))

    if order_subtotal <= 0:
        errors.append("El subtotal del pedido debe ser mayor a cero.")
    if order_total <= 0:
        errors.append("El total del pedido debe ser mayor a cero.")
    if order_discount < 0:
        errors.append("El descuento del pedido no puede ser negativo.")

    subtotal_delta = abs(calculated_subtotal - order_subtotal)
    if subtotal_delta > Decimal("2.00"):
        errors.append(
            "El subtotal del pedido no coincide con los items cargados."
        )

    expected_total = (calculated_subtotal - order_discount).quantize(Decimal("0.01"))
    if expected_total < 0:
        errors.append("El total calculado del pedido no puede ser negativo.")
    total_delta = abs(expected_total - order_total)
    if total_delta > Decimal("2.00"):
        errors.append(
            "El total del pedido no coincide con subtotal y descuentos."
        )


def _append_client_tax_errors(client_profile, errors: List[str]):
    doc_type = str(getattr(client_profile, "document_type", "") or "").strip().lower()
    doc_number = (
        getattr(client_profile, "document_number", "")
        or getattr(client_profile, "cuit_dni", "")
        or ""
    )
    digits = _normalize_digits(doc_number)
    if doc_type in {"cuit", "cuil"} and len(digits) != 11:
        errors.append("El documento fiscal del cliente debe tener 11 digitos (CUIT/CUIL).")
    if doc_type == "dni" and len(digits) < 7:
        errors.append("El DNI del cliente parece invalido.")


def is_invoice_ready(order) -> Tuple[bool, List[str]]:
    """
    Validate if an order is ready for fiscal invoicing.

    Returns:
        (is_ready, errors)
    """
    errors: List[str] = []
    if not order:
        return False, ["Pedido invalido."]

    company = getattr(order, "company", None)
    if not company:
        errors.append("Pedido sin empresa asignada.")
    client_company = getattr(order, "client_company_ref", None)
    if not client_company:
        errors.append("Pedido sin cliente empresa.")

    if company and client_company and client_company.company_id != company.id:
        errors.append("La empresa del pedido no coincide con el cliente empresa.")

    allowed_statuses = {
        getattr(order, "STATUS_CONFIRMED", "confirmed"),
        getattr(order, "STATUS_PREPARING", "preparing"),
        getattr(order, "STATUS_SHIPPED", "shipped"),
        getattr(order, "STATUS_DELIVERED", "delivered"),
    }
    if getattr(order, "status", None) not in allowed_statuses:
        errors.append("El pedido no esta en estado facturable.")
    _append_order_amount_errors(order, errors)

    if company:
        company_requirements = [
            ("legal_name", "Razon social de la empresa"),
            ("cuit", "CUIT de la empresa"),
            ("tax_condition", "Condicion fiscal de la empresa"),
            ("fiscal_address", "Domicilio fiscal de la empresa"),
            ("fiscal_city", "Localidad fiscal de la empresa"),
            ("fiscal_province", "Provincia fiscal de la empresa"),
            ("postal_code", "Codigo postal de la empresa"),
            ("point_of_sale_default", "Punto de venta de la empresa"),
        ]
        for field, label in company_requirements:
            if _is_blank(getattr(company, field, "")):
                errors.append(f"Falta {label}.")

    client_profile = None
    if client_company:
        client_profile = getattr(client_company, "client_profile", None)
    if not client_profile and getattr(order, "user_id", None):
        client_profile = getattr(order.user, "client_profile", None)
    if not client_profile:
        errors.append("Pedido sin perfil de cliente asociado.")
    else:
        client_requirements = [
            ("document_type", "Tipo de documento del cliente"),
            ("document_number", "Numero de documento del cliente"),
            ("iva_condition", "Condicion IVA del cliente"),
            ("fiscal_address", "Domicilio fiscal del cliente"),
            ("fiscal_city", "Localidad fiscal del cliente"),
            ("fiscal_province", "Provincia fiscal del cliente"),
            ("postal_code", "Codigo postal del cliente"),
        ]
        for field, label in client_requirements:
            if _is_blank(getattr(client_profile, field, "")):
                errors.append(f"Falta {label}.")
        _append_client_tax_errors(client_profile, errors)

    return len(errors) == 0, errors


def is_company_fiscal_ready(company) -> Tuple[bool, List[str]]:
    """Validate if a company is minimally ready for fiscal emission flows."""
    errors: List[str] = []
    if not company:
        return False, ["Empresa invalida."]

    company_requirements = [
        ("legal_name", "Razon social de la empresa"),
        ("cuit", "CUIT de la empresa"),
        ("tax_condition", "Condicion fiscal de la empresa"),
        ("fiscal_address", "Domicilio fiscal de la empresa"),
        ("fiscal_city", "Localidad fiscal de la empresa"),
        ("fiscal_province", "Provincia fiscal de la empresa"),
        ("postal_code", "Codigo postal de la empresa"),
        ("point_of_sale_default", "Punto de venta default de la empresa"),
    ]
    for field, label in company_requirements:
        if _is_blank(getattr(company, field, "")):
            errors.append(f"Falta {label}.")

    points = FiscalPointOfSale.objects.filter(company=company)
    active_points = points.filter(is_active=True)
    default_point = points.filter(is_default=True).first()

    if not active_points.exists():
        errors.append("No existe ningun punto de venta fiscal activo.")
    if not default_point:
        errors.append("No existe punto de venta fiscal default.")
    else:
        if not default_point.is_active:
            errors.append("El punto de venta fiscal default esta inactivo.")
        company_default = str(getattr(company, "point_of_sale_default", "") or "").strip()
        if company_default and default_point.number != company_default:
            errors.append(
                "El punto de venta default de la empresa no coincide con el POS default marcado."
            )
        env_cfg = _get_company_arca_env_config(company, getattr(default_point, "environment", "homologation"))
        cert_path = str(env_cfg.get("cert_path", "") or "").strip()
        key_path = str(env_cfg.get("key_path", "") or "").strip()
        if not cert_path:
            errors.append("Falta cert_path ARCA para el entorno del POS default.")
        elif not os.path.exists(cert_path):
            errors.append("El cert_path ARCA del POS default no existe en el servidor.")
        if not key_path:
            errors.append("Falta key_path ARCA para el entorno del POS default.")
        elif not os.path.exists(key_path):
            errors.append("El key_path ARCA del POS default no existe en el servidor.")

    return len(errors) == 0, errors


def validate_credit_note_relationship(document) -> Tuple[bool, List[str]]:
    """
    Validate linkage rules for credit notes (NCA/NCB/NCC).
    Returns (is_valid, errors).
    """
    errors: List[str] = []
    if not document:
        return False, ["Comprobante fiscal invalido."]

    if document.doc_type not in FISCAL_CREDIT_NOTE_DOC_TYPES:
        return True, []

    related = getattr(document, "related_document", None)
    if not related:
        return False, ["La nota de credito debe vincularse a una factura base."]
    if related.pk == document.pk:
        errors.append("La nota de credito no puede referenciarse a si misma.")
    if related.company_id != document.company_id:
        errors.append("La factura base pertenece a otra empresa.")

    expected_base_type_by_credit_note = {
        FISCAL_DOC_TYPE_NCA: FISCAL_DOC_TYPE_FA,
        FISCAL_DOC_TYPE_NCB: FISCAL_DOC_TYPE_FB,
        FISCAL_DOC_TYPE_NCC: FISCAL_DOC_TYPE_FC,
    }
    expected_base_type = expected_base_type_by_credit_note.get(document.doc_type)
    if not expected_base_type:
        errors.append("Tipo de nota de credito no soportado.")
        return False, errors
    if related.doc_type != expected_base_type:
        errors.append("La letra/tipo de la nota de credito no coincide con la factura base.")

    if related.status not in {FISCAL_STATUS_AUTHORIZED, FISCAL_STATUS_EXTERNAL_RECORDED}:
        errors.append("La factura base debe estar emitida/cerrada para aceptar nota de credito.")
    if related.status == FISCAL_STATUS_VOIDED:
        errors.append("La factura base esta anulada.")
    if (
        related.client_company_ref_id
        and document.client_company_ref_id
        and related.client_company_ref_id != document.client_company_ref_id
    ):
        errors.append("La nota de credito debe apuntar al mismo cliente empresa de la factura base.")

    related_total = abs(_to_decimal(getattr(related, "total", 0)).quantize(Decimal("0.01")))
    current_total = abs(_to_decimal(getattr(document, "total", 0)).quantize(Decimal("0.01")))
    if current_total > related_total:
        errors.append("El total de la nota de credito no puede superar la factura base.")

    already_credited = (
        related.credit_notes.exclude(pk=document.pk)
        .exclude(status=FISCAL_STATUS_VOIDED)
        .aggregate(total=Sum("total"))
        .get("total")
        or Decimal("0.00")
    )
    already_credited = abs(_to_decimal(already_credited).quantize(Decimal("0.01")))
    if already_credited + current_total > related_total + Decimal("0.01"):
        errors.append("El credito acumulado supera el total de la factura base.")

    return len(errors) == 0, errors
