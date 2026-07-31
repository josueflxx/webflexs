"""Read-only commercial timeline assembled from existing auditable sources."""

from decimal import Decimal

from django.urls import reverse

from catalog.models import SupplierCostHistory
from core.models import (
    AdminAuditLog,
    FiscalDocumentItem,
    STOCK_MOVEMENT_IN,
    STOCK_MOVEMENT_OUT,
    StockMovement,
)
from orders.models import Order, OrderItem


TIMELINE_KIND_CHOICES = [
    ("", "Todo"),
    ("sale", "Comprobantes"),
    ("order", "Pedidos"),
    ("stock", "Stock"),
    ("cost", "Costos"),
    ("audit", "Cambios"),
]

PRODUCT_CHANGE_LABELS = {
    "sku": "SKU",
    "name": "Nombre",
    "supplier": "Proveedor",
    "supplier_ref_id": "Proveedor normalizado",
    "cost": "Costo",
    "price": "Precio neto",
    "iva_rate": "IVA",
    "stock": "Stock global",
    "tracks_stock": "Control de stock",
    "allow_negative_stock": "Stock negativo",
    "is_sellable": "Disponible para venta",
    "is_purchasable": "Disponible para compra",
    "category_id": "Categoria principal",
    "is_active": "Estado",
}


def _actor_label(user):
    if not user:
        return "Sistema"
    return user.get_full_name() or user.username


def _format_value(value):
    if value is None or value == "":
        return "-"
    if isinstance(value, bool):
        return "Si" if value else "No"
    return str(value)


def _audit_description(details, *, include_costs=True):
    details = details if isinstance(details, dict) else {}
    before = details.get("before") if isinstance(details.get("before"), dict) else {}
    after = details.get("after") if isinstance(details.get("after"), dict) else {}
    changes = []
    for field_name in sorted(set(before) | set(after)):
        if not include_costs and field_name in {"cost", "supplier", "supplier_ref_id"}:
            continue
        previous = before.get(field_name)
        current = after.get(field_name)
        if previous == current:
            continue
        label = PRODUCT_CHANGE_LABELS.get(field_name, field_name.replace("_", " ").title())
        changes.append(f"{label}: {_format_value(previous)} → {_format_value(current)}")
    if changes:
        return " · ".join(changes[:8])
    reason = str(details.get("reason") or details.get("observation") or "").strip()
    return reason or "Cambio registrado en auditoria."


def _stock_signed_quantity(movement):
    quantity = Decimal(movement.quantity or 0)
    if movement.movement_type == STOCK_MOVEMENT_OUT:
        return -quantity
    if movement.movement_type == STOCK_MOVEMENT_IN:
        return quantity
    return Decimal("0")


def build_product_timeline(product, *, kind="", limit=100, include_costs=True):
    selected_kind = str(kind or "").strip().lower()
    if selected_kind not in {value for value, _label in TIMELINE_KIND_CHOICES}:
        selected_kind = ""
    per_source_limit = max(min(int(limit or 100), 200), 20)
    entries = []

    if selected_kind in {"", "stock"}:
        movements = (
            StockMovement.objects.filter(product=product)
            .select_related(
                "created_by",
                "warehouse",
                "warehouse__company",
                "order",
                "fiscal_document",
                "sales_document_type",
            )
            .order_by("-created_at", "-pk")[:per_source_limit]
        )
        for movement in movements:
            signed_quantity = _stock_signed_quantity(movement)
            link = ""
            link_label = ""
            if movement.fiscal_document_id:
                link = reverse("admin_fiscal_document_detail", args=[movement.fiscal_document_id])
                link_label = "Ver comprobante"
            elif movement.order_id:
                link = reverse("admin_order_detail", args=[movement.order_id])
                link_label = "Ver pedido"
            entries.append(
                {
                    "occurred_at": movement.created_at,
                    "kind": "stock",
                    "kind_label": "Stock",
                    "title": f"{movement.get_movement_type_display()} en {movement.warehouse.name if movement.warehouse_id else 'stock global'}",
                    "description": movement.notes or "Movimiento comercial de stock.",
                    "actor": _actor_label(movement.created_by),
                    "quantity": movement.quantity,
                    "signed_quantity": signed_quantity,
                    "warehouse": movement.warehouse.name if movement.warehouse_id else "",
                    "company": movement.company.name if movement.company_id else "",
                    "link": link,
                    "link_label": link_label,
                    "status": (
                        "Aplicado al saldo"
                        if movement.warehouse_balance_applied_at
                        else movement.warehouse_balance_error or "Registrado"
                    ),
                }
            )

    if include_costs and selected_kind in {"", "cost"}:
        cost_changes = (
            SupplierCostHistory.objects.filter(product_supplier__product=product)
            .select_related("product_supplier__supplier", "changed_by")
            .order_by("-created_at", "-pk")[:per_source_limit]
        )
        for change in cost_changes:
            supplier = change.product_supplier.supplier
            description_parts = [
                f"{_format_value(change.previous_cost)} → {_format_value(change.new_cost)} {change.currency}",
            ]
            if change.difference_percentage is not None:
                description_parts.append(f"{change.difference_percentage}%")
            if change.reason:
                description_parts.append(change.reason)
            entries.append(
                {
                    "occurred_at": change.created_at,
                    "kind": "cost",
                    "kind_label": "Costo",
                    "title": f"Cambio de costo · {supplier.name}",
                    "description": " · ".join(description_parts),
                    "actor": _actor_label(change.changed_by),
                    "previous_cost": change.previous_cost,
                    "new_cost": change.new_cost,
                    "company": "",
                    "link": reverse("admin_supplier_detail", args=[supplier.pk]),
                    "link_label": "Ver proveedor",
                    "status": change.source or "manual",
                }
            )

    if selected_kind in {"", "sale"}:
        fiscal_items = (
            FiscalDocumentItem.objects.filter(product=product)
            .select_related(
                "fiscal_document",
                "fiscal_document__company",
                "fiscal_document__order",
                "fiscal_document__order__assigned_to",
                "fiscal_document__sales_document_type",
            )
            .order_by("-fiscal_document__created_at", "-pk")[:per_source_limit]
        )
        for item in fiscal_items:
            document = item.fiscal_document
            seller = document.order.assigned_to if document.order_id else None
            entries.append(
                {
                    "occurred_at": document.issued_at or document.created_at,
                    "kind": "sale",
                    "kind_label": "Comprobante",
                    "title": f"{document.commercial_type_label} {document.display_number}",
                    "description": (
                        f"{item.quantity} × {item.unit_price_net} neto · "
                        f"IVA {item.iva_rate}% · Total {item.total_amount}"
                    ),
                    "actor": _actor_label(seller),
                    "quantity": item.quantity,
                    "unit_price": item.unit_price_net,
                    "total": item.total_amount,
                    "company": document.company.name,
                    "link": reverse("admin_fiscal_document_detail", args=[document.pk]),
                    "link_label": "Ver comprobante",
                    "status": document.get_status_display(),
                }
            )

    if selected_kind in {"", "order"}:
        order_items = (
            OrderItem.objects.filter(
                product=product,
                order__fiscal_documents__isnull=True,
            )
            .exclude(order__status=Order.STATUS_CANCELLED)
            .select_related("order", "order__company", "order__assigned_to")
            .distinct()
            .order_by("-order__created_at", "-pk")[:per_source_limit]
        )
        for item in order_items:
            order = item.order
            entries.append(
                {
                    "occurred_at": order.created_at,
                    "kind": "order",
                    "kind_label": "Pedido",
                    "title": f"Pedido #{order.pk} · {order.get_status_display()}",
                    "description": (
                        f"{item.quantity} × {item.price_at_purchase} · "
                        f"Subtotal {item.subtotal}"
                    ),
                    "actor": _actor_label(order.assigned_to),
                    "quantity": item.quantity,
                    "unit_price": item.price_at_purchase,
                    "total": item.subtotal,
                    "company": order.company.name if order.company_id else "",
                    "link": reverse("admin_order_detail", args=[order.pk]),
                    "link_label": "Ver pedido",
                    "status": order.get_status_display(),
                }
            )

    if selected_kind in {"", "audit"}:
        audit_rows = (
            AdminAuditLog.objects.filter(
                target_type="product",
                target_id=str(product.pk),
            )
            .select_related("user", "company")
            .order_by("-created_at", "-pk")[:per_source_limit]
        )
        for audit in audit_rows:
            entries.append(
                {
                    "occurred_at": audit.created_at,
                    "kind": "audit",
                    "kind_label": "Cambio",
                    "title": audit.action.replace("_", " ").title(),
                    "description": _audit_description(
                        audit.details,
                        include_costs=include_costs,
                    ),
                    "actor": _actor_label(audit.user),
                    "company": audit.company.name if audit.company_id else "",
                    "link": "",
                    "link_label": "",
                    "status": "Auditado",
                }
            )

    entries.sort(
        key=lambda entry: (
            entry.get("occurred_at"),
            entry.get("kind", ""),
        ),
        reverse=True,
    )
    return entries[: max(min(int(limit or 100), 200), 20)]
