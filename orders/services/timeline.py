"""Unified chronological history for an operational order."""

from core.models import AdminAuditLog


def build_order_timeline(order, *, include_internal=True, limit=100):
    if not order or not order.pk:
        return []

    events = []

    def add(occurred_at, event_type, title, summary="", actor="", amount=None, state="info"):
        if not occurred_at:
            return
        events.append({
            "occurred_at": occurred_at,
            "event_type": event_type,
            "title": title,
            "summary": summary,
            "actor": actor or "Sistema",
            "amount": amount,
            "state": state,
        })

    add(
        order.created_at,
        "created",
        "Pedido creado",
        f"Canal {order.get_origin_channel_display()} · total ${order.total:.2f}",
        getattr(getattr(order, "user", None), "username", "Sistema"),
        order.total,
        "success",
    )

    for row in order.status_history.select_related("changed_by").all():
        from_label = dict(order.STATUS_CHOICES).get(row.from_status, row.from_status or "Inicio")
        to_label = dict(order.STATUS_CHOICES).get(row.to_status, row.to_status)
        add(
            row.created_at,
            "status",
            f"Estado: {from_label} → {to_label}",
            row.note,
            getattr(row.changed_by, "username", "Sistema"),
            state="danger" if row.to_status == order.STATUS_CANCELLED else "info",
        )

    for payment in order.payments.select_related("created_by").all():
        add(
            payment.cancelled_at or payment.paid_at,
            "payment",
            "Pago anulado" if payment.is_cancelled else "Pago registrado",
            f"{payment.get_method_display()} · {payment.reference or 'sin referencia'}",
            getattr(payment.created_by, "username", "Sistema"),
            payment.amount,
            "danger" if payment.is_cancelled else "success",
        )

    for document in order.documents.select_related("sales_document_type").all():
        add(
            document.cancelled_at or document.issued_at,
            "internal_document",
            f"{document.commercial_type_label} {'anulado' if document.is_cancelled else 'generado'}",
            document.display_number,
            state="danger" if document.is_cancelled else "success",
        )

    for document in order.fiscal_documents.select_related("point_of_sale", "sales_document_type").all():
        add(
            document.issued_at or document.created_at,
            "fiscal_document",
            f"{document.commercial_type_label}: {document.get_status_display()}",
            document.display_number,
            amount=document.total,
            state="success" if document.status in {"authorized", "external_recorded"} else "info",
        )

    if order.synced_at:
        add(
            order.synced_at,
            "sync",
            "Pedido sincronizado",
            f"{order.external_system or 'Sistema externo'} · {order.external_number or order.external_id or '-'}",
            state="success" if order.sync_status == order.SYNC_STATUS_SYNCED else "danger",
        )

    if include_internal:
        audit_rows = AdminAuditLog.objects.filter(
            company=order.company,
            target_id=str(order.pk),
            target_type__in=["order", "orders.order"],
        ).select_related("user")[:50]
        for row in audit_rows:
            changed_fields = row.details.get("changed_fields", {}) if isinstance(row.details, dict) else {}
            add(
                row.created_at,
                "audit",
                row.action.replace("_", " ").capitalize(),
                ", ".join(changed_fields.keys()) if changed_fields else "Cambio administrativo",
                getattr(row.user, "username", "Sistema"),
            )

    events.sort(key=lambda event: event["occurred_at"], reverse=True)
    return events[:limit]
