"""Signed public links and normalized presentation data for commercial documents."""

from __future__ import annotations

from decimal import Decimal

from django.core import signing
from django.core.signing import BadSignature
from django.shortcuts import get_object_or_404

from core.models import FiscalDocument, InternalDocument
from core.services.sales_documents import build_internal_document_display_items


PUBLIC_DOCUMENT_SALT = "flexs.public-commercial-document.v1"


def build_public_document_token(document):
    """Create a tamper-proof token without exposing a sequential public URL."""
    if isinstance(document, InternalDocument):
        kind = "internal"
    elif isinstance(document, FiscalDocument):
        kind = "fiscal"
    else:
        raise TypeError("Tipo de documento no compartible.")
    return signing.dumps(
        {
            "v": 1,
            "kind": kind,
            "id": document.pk,
            "company": document.company_id,
        },
        salt=PUBLIC_DOCUMENT_SALT,
        compress=True,
    )


def load_public_document(token):
    """Resolve a signed token and reject altered or incomplete payloads."""
    try:
        payload = signing.loads(token, salt=PUBLIC_DOCUMENT_SALT)
    except (BadSignature, TypeError, ValueError):
        return None, ""
    if not isinstance(payload, dict) or payload.get("v") != 1:
        return None, ""
    document_id = payload.get("id")
    company_id = payload.get("company")
    kind = str(payload.get("kind") or "").strip().lower()
    if not isinstance(document_id, int) or not isinstance(company_id, int):
        return None, ""
    if kind == "internal":
        document = get_object_or_404(
            InternalDocument.objects.select_related(
                "company",
                "client_profile",
                "client_company_ref__client_profile",
                "order",
                "payment",
                "sales_document_type",
            ).prefetch_related("order__items"),
            pk=document_id,
            company_id=company_id,
        )
        return document, kind
    if kind == "fiscal":
        document = get_object_or_404(
            FiscalDocument.objects.select_related(
                "company",
                "client_profile",
                "client_company_ref__client_profile",
                "order",
                "point_of_sale",
                "sales_document_type",
            ).prefetch_related("items"),
            pk=document_id,
            company_id=company_id,
        )
        return document, kind
    return None, ""


def _money(value):
    try:
        return Decimal(str(value or 0))
    except (ArithmeticError, TypeError, ValueError):
        return Decimal("0.00")


def _client_from_document(document):
    return document.client_profile or getattr(document.client_company_ref, "client_profile", None)


def build_public_document_context(document, kind):
    """Build the deliberately limited payload shown to anonymous visitors."""
    company = document.company
    client = _client_from_document(document)
    order = getattr(document, "order", None)

    if kind == "fiscal":
        snapshot = document.fiscal_snapshot if isinstance(document.fiscal_snapshot, dict) else {}
        emitter = snapshot.get("emitter", {}) if isinstance(snapshot.get("emitter", {}), dict) else {}
        client_snapshot = snapshot.get("client", {}) if isinstance(snapshot.get("client", {}), dict) else {}
        operation = snapshot.get("operation", {}) if isinstance(snapshot.get("operation", {}), dict) else {}
        rows = [
            {
                "sku": item.sku or "-",
                "description": item.description,
                "quantity": item.quantity,
                "unit_price": item.unit_price_net,
                "discount": item.discount_percentage,
                "total": item.total_amount,
            }
            for item in document.items.all().order_by("line_number")
        ]
        subtotal = document.subtotal_net
        discount = document.discount_total
        total = document.total
        issued_at = document.issued_at or document.created_at
        status_label = document.get_status_display()
        client_name = str(client_snapshot.get("name") or "").strip()
        client_document = str(client_snapshot.get("document_number") or "").strip()
        client_address = " / ".join(
            filter(
                None,
                [
                    str(client_snapshot.get("fiscal_address") or "").strip(),
                    str(client_snapshot.get("fiscal_city") or "").strip(),
                    str(client_snapshot.get("fiscal_province") or "").strip(),
                ],
            )
        )
        observations = str(operation.get("notes") or "").strip()
        company_name = str(emitter.get("legal_name") or emitter.get("name") or "").strip()
        company_cuit = str(emitter.get("cuit") or document.issuer_cuit_snapshot or "").strip()
        company_address = " / ".join(
            filter(
                None,
                [
                    str(emitter.get("fiscal_address") or "").strip(),
                    str(emitter.get("fiscal_city") or "").strip(),
                    str(emitter.get("fiscal_province") or "").strip(),
                ],
            )
        )
    else:
        rows = []
        for item in build_internal_document_display_items(document):
            if isinstance(item, dict):
                getter = item.get
            else:
                getter = lambda key, default=None, obj=item: getattr(obj, key, default)
            rows.append(
                {
                    "sku": getter("product_sku", "-") or "-",
                    "description": getter("product_name", "-") or "-",
                    "quantity": getter("quantity", 0),
                    "unit_price": getter("price_at_purchase", 0),
                    "discount": getter("discount_percentage_used", 0),
                    "total": getter("subtotal", 0),
                }
            )
        subtotal = getattr(order, "subtotal", Decimal("0.00"))
        discount = getattr(order, "discount_amount", Decimal("0.00"))
        total = getattr(order, "total", Decimal("0.00"))
        issued_at = document.issued_at
        status_label = "Anulado" if document.is_cancelled else "Emitido"
        client_name = getattr(client, "company_name", "") or getattr(order, "client_company", "")
        client_document = getattr(client, "cuit_dni", "") or getattr(order, "client_cuit", "")
        client_address = getattr(client, "fiscal_address", "") or getattr(client, "address", "") or getattr(order, "client_address", "")
        observations = getattr(order, "notes", "") or ""
        company_name = company.legal_name or company.name
        company_cuit = company.cuit
        company_address = " / ".join(
            filter(None, [company.fiscal_address, company.fiscal_city, company.fiscal_province])
        )

    return {
        "document": document,
        "document_kind": kind,
        "document_label": document.commercial_type_label,
        "document_number": document.display_number,
        "issued_at": issued_at,
        "status_label": status_label,
        "company_name": company_name or company.name,
        "company_cuit": company_cuit or "-",
        "company_address": company_address or "-",
        "company_email": company.email or "",
        "client_name": client_name or "Consumidor final",
        "client_number": getattr(client, "pk", None) or "-",
        "client_document": client_document or "-",
        "client_address": client_address or "-",
        "rows": rows,
        "subtotal": _money(subtotal),
        "discount": _money(discount),
        "total": _money(total),
        "observations": observations,
    }
