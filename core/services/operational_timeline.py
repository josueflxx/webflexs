from decimal import Decimal
from urllib.parse import urlencode

from django.db.models import DecimalField, F, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.urls import reverse

from accounts.models import ClientPayment, ClientProfile, ClientTransaction
from catalog.models import Product
from core.models import (
    FISCAL_DOC_TYPE_FA,
    FISCAL_DOC_TYPE_FB,
    FISCAL_DOC_TYPE_FC,
    FISCAL_STATUS_EXTERNAL_RECORDED,
    FISCAL_STATUS_PENDING_RETRY,
    FISCAL_STATUS_READY_TO_ISSUE,
    FISCAL_STATUS_REJECTED,
    FISCAL_STATUS_SUBMITTING,
    FISCAL_STATUS_VOIDED,
    FiscalDocument,
    InternalDocument,
)
from orders.models import Order, OrderRequest, OrderRequestEvent, OrderStatusHistory


INVOICE_FISCAL_DOC_TYPES = (
    FISCAL_DOC_TYPE_FA,
    FISCAL_DOC_TYPE_FB,
    FISCAL_DOC_TYPE_FC,
)
REQUEST_INBOX_STATUSES = (
    OrderRequest.STATUS_SUBMITTED,
    OrderRequest.STATUS_IN_REVIEW,
    OrderRequest.STATUS_PROPOSAL_SENT,
    OrderRequest.STATUS_WAITING_CLIENT,
)
INVOICE_PENDING_FISCAL_STATUSES = (
    FISCAL_STATUS_READY_TO_ISSUE,
    FISCAL_STATUS_PENDING_RETRY,
    FISCAL_STATUS_SUBMITTING,
    FISCAL_STATUS_REJECTED,
)
SALES_WORKSPACE_STAGE_ACTIVE_KEYS = {
    "inbox": ["requests_inbox"],
    "waiting": ["requests_waiting"],
    "drafts": ["order_drafts"],
    "remito": ["remito_queue"],
    "invoice": ["invoice_queue"],
    "collections": ["collection_queue"],
}


def _format_order_amount(order_obj):
    total = getattr(order_obj, "total", None)
    if total is None:
        return None
    return total or Decimal("0.00")


def _clean_parts(*parts):
    return " | ".join(str(part).strip() for part in parts if part)


def _client_scope_filter(client_profile):
    scope = Q(client_company_ref__client_profile=client_profile)
    if client_profile.user_id:
        scope |= Q(user_id=client_profile.user_id)
    return scope


def _client_document_filter(client_profile):
    return Q(client_profile=client_profile) | Q(client_company_ref__client_profile=client_profile)


def _append_event(bucket, *, occurred_at, badge, title, summary="", amount=None, detail_url="", detail_label="Abrir", actor_label="", scope_label="", sort_id=0):
    if not occurred_at:
        return
    bucket.append(
        {
            "occurred_at": occurred_at,
            "badge": badge,
            "title": title,
            "summary": summary,
            "amount": amount,
            "detail_url": detail_url,
            "detail_label": detail_label,
            "actor_label": actor_label,
            "scope_label": scope_label,
            "_sort_id": sort_id,
        }
    )


def _sort_and_trim(events, limit):
    ordered = sorted(
        events,
        key=lambda item: (item["occurred_at"], item.get("_sort_id", 0)),
        reverse=True,
    )
    trimmed = ordered[:limit]
    for item in trimmed:
        item.pop("_sort_id", None)
    return trimmed


def build_client_activity_timeline(client_profile, company=None, limit=12):
    if not isinstance(client_profile, ClientProfile):
        return []

    events = []
    client_scope = _client_scope_filter(client_profile)
    document_scope = _client_document_filter(client_profile)

    order_requests = (
        OrderRequest.objects.select_related("company")
        .filter(client_scope)
    )
    if company:
        order_requests = order_requests.filter(company=company)
    order_requests = order_requests.order_by("-created_at", "-id")[: max(limit * 2, 16)]
    order_request_ids = []
    for request_obj in order_requests:
        order_request_ids.append(request_obj.pk)
        _append_event(
            events,
            occurred_at=request_obj.created_at,
            badge="Solicitud",
            title=f"Solicitud de compra #{request_obj.pk}",
            summary=_clean_parts(
                request_obj.get_status_display(),
                getattr(request_obj.company, "name", ""),
                f"Total ${request_obj.requested_total or Decimal('0.00'):.2f}",
            ),
            amount=request_obj.requested_total or Decimal("0.00"),
            detail_url=reverse("admin_order_request_detail", args=[request_obj.pk]),
            detail_label="Abrir solicitud",
            scope_label=getattr(request_obj.company, "name", ""),
            sort_id=request_obj.pk,
        )

    if order_request_ids:
        request_events = (
            OrderRequestEvent.objects.select_related("order_request", "order_request__company", "actor")
            .exclude(event_type=OrderRequestEvent.EVENT_CREATED)
            .filter(order_request_id__in=order_request_ids)
            .order_by("-created_at", "-id")[: max(limit * 3, 18)]
        )
        for event_obj in request_events:
            _append_event(
                events,
                occurred_at=event_obj.created_at,
                badge="Solicitud",
                title=f"Solicitud #{event_obj.order_request_id}: {event_obj.get_event_type_display()}",
                summary=_clean_parts(
                    event_obj.message,
                    event_obj.to_status or "",
                    getattr(event_obj.order_request.company, "name", ""),
                ),
                detail_url=reverse("admin_order_request_detail", args=[event_obj.order_request_id]),
                detail_label="Ver seguimiento",
                actor_label=(
                    event_obj.actor.get_full_name().strip() or event_obj.actor.username
                    if event_obj.actor_id
                    else ""
                ),
                scope_label=getattr(event_obj.order_request.company, "name", ""),
                sort_id=event_obj.pk,
            )

    orders = (
        Order.objects.select_related("company")
        .filter(client_scope)
    )
    if company:
        orders = orders.filter(company=company)
    orders = orders.order_by("-created_at", "-id")[: max(limit * 2, 16)]
    order_ids = []
    for order_obj in orders:
        order_ids.append(order_obj.pk)
        _append_event(
            events,
            occurred_at=order_obj.created_at,
            badge="Pedido",
            title=f"Pedido operativo #{order_obj.pk}",
            summary=_clean_parts(
                order_obj.get_status_display(),
                getattr(order_obj.company, "name", ""),
                f"Total ${_format_order_amount(order_obj):.2f}",
            ),
            amount=_format_order_amount(order_obj),
            detail_url=reverse("admin_order_detail", args=[order_obj.pk]),
            detail_label="Abrir pedido",
            scope_label=getattr(order_obj.company, "name", ""),
            sort_id=order_obj.pk,
        )

    if order_ids:
        status_rows = (
            OrderStatusHistory.objects.select_related("order", "order__company", "changed_by")
            .filter(order_id__in=order_ids)
            .order_by("-created_at", "-id")[: max(limit * 3, 18)]
        )
        for history_obj in status_rows:
            _append_event(
                events,
                occurred_at=history_obj.created_at,
                badge="Estado",
                title=f"Pedido #{history_obj.order_id} -> {history_obj.order.get_status_display()}",
                summary=_clean_parts(
                    history_obj.note,
                    getattr(history_obj.order.company, "name", ""),
                ),
                detail_url=reverse("admin_order_detail", args=[history_obj.order_id]),
                detail_label="Ver pedido",
                actor_label=(
                    history_obj.changed_by.get_full_name().strip() or history_obj.changed_by.username
                    if history_obj.changed_by_id
                    else ""
                ),
                scope_label=getattr(history_obj.order.company, "name", ""),
                sort_id=history_obj.pk,
            )

    internal_documents = (
        InternalDocument.objects.select_related("company", "sales_document_type", "order", "payment")
        .filter(document_scope)
    )
    if company:
        internal_documents = internal_documents.filter(company=company)
    internal_documents = internal_documents.order_by("-issued_at", "-id")[: max(limit * 2, 16)]
    internal_transaction_ids = set()
    for document in internal_documents:
        if document.transaction_id:
            internal_transaction_ids.add(document.transaction_id)
        if document.order_id:
            detail_url = reverse("admin_order_detail", args=[document.order_id])
            detail_label = "Ver pedido"
        elif document.payment_id:
            query = urlencode({"client_id": client_profile.pk, "company_id": getattr(company, "pk", "all")})
            detail_url = f"{reverse('admin_payment_list')}?{query}"
            detail_label = "Ver cobro"
        else:
            detail_url = ""
            detail_label = "Abrir"
        _append_event(
            events,
            occurred_at=document.issued_at,
            badge="Interno",
            title=f"{document.commercial_type_label} {document.display_number}",
            summary=_clean_parts(
                getattr(document.company, "name", ""),
                f"Pedido #{document.order_id}" if document.order_id else "",
                "Anulado" if document.is_cancelled else "",
            ),
            detail_url=detail_url,
            detail_label=detail_label,
            scope_label=getattr(document.company, "name", ""),
            sort_id=document.pk,
        )

    fiscal_documents = (
        FiscalDocument.objects.select_related("company", "sales_document_type", "point_of_sale", "order")
        .filter(document_scope)
        .exclude(status=FISCAL_STATUS_VOIDED)
    )
    if company:
        fiscal_documents = fiscal_documents.filter(company=company)
    fiscal_documents = fiscal_documents.order_by("-created_at", "-id")[: max(limit * 2, 16)]
    for document in fiscal_documents:
        _append_event(
            events,
            occurred_at=document.issued_at or document.created_at,
            badge="Fiscal",
            title=f"{document.commercial_type_label} {document.display_number}",
            summary=_clean_parts(
                document.get_status_display(),
                getattr(document.company, "name", ""),
                f"Pedido #{document.order_id}" if document.order_id else "",
            ),
            amount=document.total or Decimal("0.00"),
            detail_url=reverse("admin_fiscal_document_detail", args=[document.pk]),
            detail_label="Ver comprobante",
            scope_label=getattr(document.company, "name", ""),
            sort_id=document.pk,
        )

    payments = (
        ClientPayment.objects.select_related("company", "order", "created_by")
        .filter(client_profile=client_profile)
    )
    if company:
        payments = payments.filter(company=company)
    payments = payments.order_by("-paid_at", "-id")[: max(limit * 2, 16)]
    payment_base_query = urlencode({"client_id": client_profile.pk, "company_id": getattr(company, "pk", "all")})
    for payment in payments:
        _append_event(
            events,
            occurred_at=payment.paid_at,
            badge="Cobro",
            title="Cobro registrado",
            summary=_clean_parts(
                payment.get_method_display(),
                payment.reference,
                getattr(payment.company, "name", ""),
                "Anulado" if payment.is_cancelled else "",
            ),
            amount=(payment.amount or Decimal("0.00")) * (Decimal("-1.00") if not payment.is_cancelled else Decimal("1.00")),
            detail_url=f"{reverse('admin_payment_list')}?{payment_base_query}",
            detail_label="Ver cobros",
            actor_label=(
                payment.created_by.get_full_name().strip() or payment.created_by.username
                if payment.created_by_id
                else ""
            ),
            scope_label=getattr(payment.company, "name", ""),
            sort_id=payment.pk,
        )

    adjustments = (
        ClientTransaction.objects.select_related("company", "created_by")
        .filter(
            client_profile=client_profile,
            transaction_type=ClientTransaction.TYPE_ADJUSTMENT,
        )
    )
    if company:
        adjustments = adjustments.filter(company=company)
    adjustments = adjustments.order_by("-occurred_at", "-id")[: max(limit, 8)]
    for movement in adjustments:
        if movement.pk in internal_transaction_ids:
            continue
        _append_event(
            events,
            occurred_at=movement.occurred_at,
            badge="Ajuste",
            title="Ajuste manual de cuenta corriente",
            summary=_clean_parts(
                movement.description,
                movement.get_movement_state_display(),
                getattr(movement.company, "name", ""),
            ),
            amount=movement.amount or Decimal("0.00"),
            actor_label=(
                movement.created_by.get_full_name().strip() or movement.created_by.username
                if movement.created_by_id
                else ""
            ),
            scope_label=getattr(movement.company, "name", ""),
            sort_id=movement.pk,
        )

    return _sort_and_trim(events, limit)


def build_company_activity_timeline(company=None, limit=10):
    events = []

    request_qs = OrderRequest.objects.select_related("company", "client_company_ref__client_profile").order_by("-created_at", "-id")
    if company:
        request_qs = request_qs.filter(company=company)
    for request_obj in request_qs[: max(limit, 8)]:
        client_name = (
            getattr(getattr(request_obj.client_company_ref, "client_profile", None), "company_name", "")
            or getattr(getattr(request_obj, "user", None), "username", "")
        )
        _append_event(
            events,
            occurred_at=request_obj.created_at,
            badge="Solicitud",
            title=f"Solicitud #{request_obj.pk}",
            summary=_clean_parts(
                client_name,
                request_obj.get_status_display(),
                f"Total ${request_obj.requested_total or Decimal('0.00'):.2f}",
            ),
            amount=request_obj.requested_total or Decimal("0.00"),
            detail_url=reverse("admin_order_request_detail", args=[request_obj.pk]),
            detail_label="Abrir solicitud",
            scope_label=getattr(request_obj.company, "name", ""),
            sort_id=request_obj.pk,
        )

    order_qs = Order.objects.select_related("company", "user").order_by("-created_at", "-id")
    if company:
        order_qs = order_qs.filter(company=company)
    for order_obj in order_qs[: max(limit, 8)]:
        _append_event(
            events,
            occurred_at=order_obj.created_at,
            badge="Pedido",
            title=f"Pedido #{order_obj.pk}",
            summary=_clean_parts(
                getattr(order_obj.user, "username", ""),
                order_obj.get_status_display(),
                f"Total ${_format_order_amount(order_obj):.2f}",
            ),
            amount=_format_order_amount(order_obj),
            detail_url=reverse("admin_order_detail", args=[order_obj.pk]),
            detail_label="Abrir pedido",
            scope_label=getattr(order_obj.company, "name", ""),
            sort_id=order_obj.pk,
        )

    status_qs = OrderStatusHistory.objects.select_related("order", "order__company", "changed_by").order_by("-created_at", "-id")
    if company:
        status_qs = status_qs.filter(order__company=company)
    for history_obj in status_qs[: max(limit, 8)]:
        _append_event(
            events,
            occurred_at=history_obj.created_at,
            badge="Estado",
            title=f"Pedido #{history_obj.order_id} -> {history_obj.order.get_status_display()}",
            summary=_clean_parts(
                getattr(history_obj.order.user, "username", ""),
                history_obj.note,
            ),
            detail_url=reverse("admin_order_detail", args=[history_obj.order_id]),
            detail_label="Ver pedido",
            actor_label=(
                history_obj.changed_by.get_full_name().strip() or history_obj.changed_by.username
                if history_obj.changed_by_id
                else ""
            ),
            scope_label=getattr(history_obj.order.company, "name", ""),
            sort_id=history_obj.pk,
        )

    fiscal_qs = FiscalDocument.objects.select_related("company", "client_profile", "client_company_ref__client_profile", "sales_document_type").exclude(status=FISCAL_STATUS_VOIDED).order_by("-created_at", "-id")
    if company:
        fiscal_qs = fiscal_qs.filter(company=company)
    for document in fiscal_qs[: max(limit, 8)]:
        client_name = (
            getattr(document.client_profile, "company_name", "")
            or getattr(getattr(document.client_company_ref, "client_profile", None), "company_name", "")
        )
        _append_event(
            events,
            occurred_at=document.issued_at or document.created_at,
            badge="Fiscal",
            title=f"{document.commercial_type_label} {document.display_number}",
            summary=_clean_parts(
                client_name,
                document.get_status_display(),
                f"Total ${document.total or Decimal('0.00'):.2f}",
            ),
            amount=document.total or Decimal("0.00"),
            detail_url=reverse("admin_fiscal_document_detail", args=[document.pk]),
            detail_label="Ver comprobante",
            scope_label=getattr(document.company, "name", ""),
            sort_id=document.pk,
        )

    payment_qs = ClientPayment.objects.select_related("company", "client_profile", "created_by").order_by("-paid_at", "-id")
    if company:
        payment_qs = payment_qs.filter(company=company)
    for payment in payment_qs[: max(limit, 8)]:
        query = urlencode({
            "client_id": payment.client_profile_id,
            "company_id": getattr(payment.company, "pk", "all"),
        })
        _append_event(
            events,
            occurred_at=payment.paid_at,
            badge="Cobro",
            title="Cobro registrado",
            summary=_clean_parts(
                payment.client_profile.company_name,
                payment.get_method_display(),
                payment.reference,
            ),
            amount=(payment.amount or Decimal("0.00")) * (Decimal("-1.00") if not payment.is_cancelled else Decimal("1.00")),
            detail_url=f"{reverse('admin_payment_list')}?{query}",
            detail_label="Ver cobros",
            actor_label=(
                payment.created_by.get_full_name().strip() or payment.created_by.username
                if payment.created_by_id
                else ""
            ),
            scope_label=getattr(payment.company, "name", ""),
            sort_id=payment.pk,
        )

    return _sort_and_trim(events, limit)


def build_operational_snapshot(company=None):
    requests_qs = OrderRequest.objects.all()
    orders_qs = Order.objects.all()
    payments_qs = ClientPayment.objects.filter(is_cancelled=False)
    movements_qs = ClientTransaction.objects.exclude(amount=Decimal("0.00"))
    fiscal_qs = FiscalDocument.objects.exclude(status=FISCAL_STATUS_VOIDED)

    if company:
        requests_qs = requests_qs.filter(company=company)
        orders_qs = orders_qs.filter(company=company)
        payments_qs = payments_qs.filter(company=company)
        movements_qs = movements_qs.filter(company=company)
        fiscal_qs = fiscal_qs.filter(company=company)

    invoice_order_ids = fiscal_qs.filter(doc_type__in=INVOICE_FISCAL_DOC_TYPES).values_list("order_id", flat=True)
    requests_inbox = requests_qs.filter(status__in=REQUEST_INBOX_STATUSES).count()
    orders_to_confirm = orders_qs.filter(status=Order.STATUS_DRAFT).count()
    orders_in_preparation = orders_qs.filter(status__in=[Order.STATUS_CONFIRMED, Order.STATUS_PREPARING]).count()
    orders_to_invoice = (
        orders_qs
        .filter(status__in=[Order.STATUS_CONFIRMED, Order.STATUS_PREPARING, Order.STATUS_SHIPPED, Order.STATUS_DELIVERED])
        .exclude(pk__in=invoice_order_ids)
        .filter(Q(saas_document_number="") | Q(saas_document_number__isnull=True))
        .count()
    )
    open_movements = movements_qs.filter(movement_state=ClientTransaction.STATE_OPEN).count()
    clients_with_debt = (
        movements_qs
        .values("client_profile_id")
        .annotate(balance=Sum("amount"))
        .filter(balance__gt=0)
        .count()
    )
    fiscal_pending = fiscal_qs.filter(status__in=INVOICE_PENDING_FISCAL_STATUSES).count()
    critical_stock = Product.objects.filter(is_active=True, stock__lte=5).count()
    recent_payments = payments_qs.count()

    return [
        {
            "key": "requests_inbox",
            "label": "Solicitudes por revisar",
            "count": requests_inbox,
            "help_text": "Pedidos del portal y propuestas que siguen en gestion comercial.",
            "url": f"{reverse('admin_order_request_list')}?{urlencode({'status': OrderRequest.STATUS_SUBMITTED})}",
        },
        {
            "key": "orders_to_confirm",
            "label": "Pedidos por confirmar",
            "count": orders_to_confirm,
            "help_text": "Borradores y pedidos manuales que todavia necesitan cierre comercial.",
            "url": f"{reverse('admin_order_list')}?{urlencode({'status': Order.STATUS_DRAFT})}",
        },
        {
            "key": "orders_in_preparation",
            "label": "Pedidos en curso",
            "count": orders_in_preparation,
            "help_text": "Pedidos ya aprobados que siguen pasando por deposito o preparacion.",
            "url": reverse("admin_order_list"),
        },
        {
            "key": "orders_to_invoice",
            "label": "Pedidos listos para facturar",
            "count": orders_to_invoice,
            "help_text": "Ventas sin factura local ni comprobante SaaS vinculado todavia.",
            "url": reverse("admin_order_list"),
        },
        {
            "key": "fiscal_pending",
            "label": "Comprobantes a resolver",
            "count": fiscal_pending,
            "help_text": "Fiscales listos, en envio o con reintento pendiente.",
            "url": reverse("admin_fiscal_document_list"),
        },
        {
            "key": "open_movements",
            "label": "Movimientos abiertos",
            "count": open_movements,
            "help_text": "Cuenta corriente con movimientos todavia no cerrados o por revisar.",
            "url": reverse("admin_payment_list"),
        },
        {
            "key": "clients_with_debt",
            "label": "Clientes con deuda",
            "count": clients_with_debt,
            "help_text": "Clientes con saldo positivo en cuenta corriente dentro del contexto actual.",
            "url": reverse("admin_client_report_debtors"),
        },
        {
            "key": "critical_stock",
            "label": "Stock critico",
            "count": critical_stock,
            "help_text": "Productos activos con stock igual o menor a 5 unidades.",
            "url": reverse("admin_product_list"),
        },
        {
            "key": "recent_payments",
            "label": "Cobros registrados",
            "count": recent_payments,
            "help_text": "Cobros activos cargados en el contexto actual, listos para seguimiento.",
            "url": reverse("admin_payment_list"),
        },
    ]


def resolve_sales_workspace_active_keys(stage):
    return SALES_WORKSPACE_STAGE_ACTIVE_KEYS.get(str(stage or "").strip().lower(), [])


def build_sales_workspace(company=None, hub_url_name=""):
    request_qs = OrderRequest.objects.all()
    orders_qs = Order.objects.all()
    fiscal_qs = FiscalDocument.objects.exclude(status=FISCAL_STATUS_VOIDED)
    remito_qs = InternalDocument.objects.filter(doc_type="REM")

    if company:
        request_qs = request_qs.filter(company=company)
        orders_qs = orders_qs.filter(company=company)
        fiscal_qs = fiscal_qs.filter(company=company)
        remito_qs = remito_qs.filter(company=company)

    invoice_order_ids = fiscal_qs.filter(doc_type__in=INVOICE_FISCAL_DOC_TYPES).values_list("order_id", flat=True)
    remito_order_ids = remito_qs.values_list("order_id", flat=True)

    pending_collection_qs = (
        orders_qs
        .exclude(status=Order.STATUS_CANCELLED)
        .annotate(
            total_paid=Coalesce(
                Sum("payments__amount", filter=Q(payments__is_cancelled=False)),
                Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            )
        )
        .filter(total__gt=F("total_paid"))
    )

    hub_base_url = reverse(hub_url_name) if hub_url_name else ""

    def workspace_url(*, route_name, stage):
        if hub_base_url:
            if stage:
                return f"{hub_base_url}?{urlencode({'stage': stage})}"
            return hub_base_url
        if stage:
            return f"{reverse(route_name)}?{urlencode({'stage': stage})}"
        return reverse(route_name)

    return [
        {
            "key": "requests_inbox",
            "label": "Solicitudes nuevas",
            "count": request_qs.filter(
                status__in=[OrderRequest.STATUS_SUBMITTED, OrderRequest.STATUS_IN_REVIEW]
            ).count(),
            "help_text": "Pedidos del portal que todavia estan en revision comercial.",
            "url": workspace_url(route_name="admin_order_request_list", stage="inbox"),
        },
        {
            "key": "requests_waiting",
            "label": "Esperando cliente",
            "count": request_qs.filter(
                status__in=[OrderRequest.STATUS_PROPOSAL_SENT, OrderRequest.STATUS_WAITING_CLIENT]
            ).count(),
            "help_text": "Solicitudes con propuesta enviada o a la espera de definicion del cliente.",
            "url": workspace_url(route_name="admin_order_request_list", stage="waiting"),
        },
        {
            "key": "order_drafts",
            "label": "Borradores operativos",
            "count": orders_qs.filter(status=Order.STATUS_DRAFT).count(),
            "help_text": "Pedidos o cotizaciones internas que todavia necesitan cierre comercial.",
            "url": workspace_url(route_name="admin_order_list", stage="drafts"),
        },
        {
            "key": "remito_queue",
            "label": "Listos para remito",
            "count": (
                orders_qs
                .filter(status__in=[Order.STATUS_CONFIRMED, Order.STATUS_PREPARING])
                .exclude(pk__in=remito_order_ids)
                .count()
            ),
            "help_text": "Ventas confirmadas o en preparacion que aun no tienen remito vinculado.",
            "url": workspace_url(route_name="admin_order_list", stage="remito"),
        },
        {
            "key": "invoice_queue",
            "label": "Listos para factura",
            "count": (
                orders_qs
                .filter(status__in=[Order.STATUS_CONFIRMED, Order.STATUS_PREPARING, Order.STATUS_SHIPPED, Order.STATUS_DELIVERED])
                .exclude(pk__in=invoice_order_ids)
                .filter(Q(saas_document_number="") | Q(saas_document_number__isnull=True))
                .count()
            ),
            "help_text": "Pedidos ya avanzados que siguen sin factura local ni comprobante externo.",
            "url": workspace_url(route_name="admin_order_list", stage="invoice"),
        },
        {
            "key": "collection_queue",
            "label": "Cobros pendientes",
            "count": pending_collection_qs.count(),
            "help_text": "Pedidos con saldo pendiente de cobro segun los pagos ya aplicados.",
            "url": workspace_url(route_name="admin_order_list", stage="collections"),
        },
    ]


def build_sales_pipeline_rows(*, company=None, stage="", client_query=""):
    stage = str(stage or "").strip().lower()
    client_query = str(client_query or "").strip()

    request_qs = (
        OrderRequest.objects.select_related(
            "company",
            "user",
            "client_company_ref__client_profile",
        )
        .filter(generated_orders__isnull=True)
    )
    orders_qs = (
        Order.objects.select_related(
            "company",
            "user",
            "client_company_ref__client_profile",
            "source_request",
        )
        .annotate(
            total_paid=Coalesce(
                Sum("payments__amount", filter=Q(payments__is_cancelled=False)),
                Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            )
        )
    )
    fiscal_qs = FiscalDocument.objects.exclude(status=FISCAL_STATUS_VOIDED).filter(
        doc_type__in=INVOICE_FISCAL_DOC_TYPES
    )
    remito_qs = InternalDocument.objects.filter(doc_type="REM")

    if company:
        request_qs = request_qs.filter(company=company)
        orders_qs = orders_qs.filter(company=company)
        fiscal_qs = fiscal_qs.filter(company=company)
        remito_qs = remito_qs.filter(company=company)

    if client_query:
        request_qs = request_qs.filter(
            Q(user__username__icontains=client_query)
            | Q(client_company_ref__client_profile__company_name__icontains=client_query)
        )
        orders_qs = orders_qs.filter(
            Q(user__username__icontains=client_query)
            | Q(client_company_ref__client_profile__company_name__icontains=client_query)
            | Q(client_company__icontains=client_query)
        )

    invoice_order_ids = set(fiscal_qs.values_list("order_id", flat=True))
    remito_order_ids = set(remito_qs.values_list("order_id", flat=True))

    if stage == "inbox":
        request_qs = request_qs.filter(
            status__in=[OrderRequest.STATUS_SUBMITTED, OrderRequest.STATUS_IN_REVIEW]
        )
        orders_qs = orders_qs.none()
    elif stage == "waiting":
        request_qs = request_qs.filter(
            status__in=[OrderRequest.STATUS_PROPOSAL_SENT, OrderRequest.STATUS_WAITING_CLIENT]
        )
        orders_qs = orders_qs.none()
    else:
        request_qs = request_qs.filter(
            status__in=[
                OrderRequest.STATUS_SUBMITTED,
                OrderRequest.STATUS_IN_REVIEW,
                OrderRequest.STATUS_PROPOSAL_SENT,
                OrderRequest.STATUS_WAITING_CLIENT,
            ]
        )
        if stage == "drafts":
            orders_qs = orders_qs.filter(status=Order.STATUS_DRAFT)
        elif stage == "remito":
            orders_qs = orders_qs.filter(
                status__in=[Order.STATUS_CONFIRMED, Order.STATUS_PREPARING]
            ).exclude(pk__in=remito_order_ids)
        elif stage == "invoice":
            orders_qs = (
                orders_qs
                .filter(
                    status__in=[
                        Order.STATUS_CONFIRMED,
                        Order.STATUS_PREPARING,
                        Order.STATUS_SHIPPED,
                        Order.STATUS_DELIVERED,
                    ]
                )
                .exclude(pk__in=invoice_order_ids)
                .filter(Q(saas_document_number="") | Q(saas_document_number__isnull=True))
            )
        elif stage == "collections":
            orders_qs = orders_qs.exclude(status=Order.STATUS_CANCELLED).filter(total__gt=F("total_paid"))

    order_list = list(orders_qs.order_by("-updated_at", "-created_at"))
    order_ids = [order.pk for order in order_list]
    remito_by_order_id = {}
    invoice_by_order_id = {}
    if order_ids:
        for document in (
            InternalDocument.objects.filter(order_id__in=order_ids, doc_type="REM")
            .select_related("sales_document_type")
            .order_by("order_id", "-issued_at", "-id")
        ):
            remito_by_order_id.setdefault(document.order_id, document)
        for document in (
            FiscalDocument.objects.filter(order_id__in=order_ids)
            .exclude(status=FISCAL_STATUS_VOIDED)
            .filter(doc_type__in=INVOICE_FISCAL_DOC_TYPES)
            .select_related("sales_document_type", "point_of_sale")
            .order_by("order_id", "-created_at", "-id")
        ):
            invoice_by_order_id.setdefault(document.order_id, document)

    rows = []
    for order_request in request_qs.order_by("-updated_at", "-created_at"):
        client_profile = getattr(getattr(order_request, "client_company_ref", None), "client_profile", None)
        client_name = (
            getattr(client_profile, "company_name", "")
            or getattr(getattr(order_request, "user", None), "username", "")
            or "-"
        )
        username = getattr(getattr(order_request, "user", None), "username", "")
        rows.append(
            {
                "sort_at": order_request.updated_at or order_request.created_at,
                "kind": "request",
                "request_id": order_request.pk,
                "order_id": None,
                "company_id": order_request.company_id,
                "client_profile_id": getattr(client_profile, "pk", None),
                "kind_label": "Solicitud comercial",
                "kind_badge": "badge-warning",
                "record_label": f"Solicitud #{order_request.pk}",
                "company_name": getattr(getattr(order_request, "company", None), "name", "-"),
                "client_name": client_name,
                "client_meta": username if username and username != client_name else "",
                "stage_label": (
                    "Revision comercial"
                    if order_request.status in [OrderRequest.STATUS_SUBMITTED, OrderRequest.STATUS_IN_REVIEW]
                    else "Esperando cliente"
                ),
                "next_step_label": (
                    "Revisar propuesta y definir conversion a pedido."
                    if order_request.status in [OrderRequest.STATUS_SUBMITTED, OrderRequest.STATUS_IN_REVIEW]
                    else "Esperar aceptacion o ajustar la propuesta."
                ),
                "status_label": order_request.get_status_display(),
                "status_badge": "badge-info",
                "amount": order_request.requested_total,
                "summary": "Llega desde catalogo o portal y todavia no paso a pedido operativo.",
                "detail_url": reverse("admin_order_request_detail", args=[order_request.pk]),
                "detail_label": "Abrir flujo",
                "secondary_url": reverse("admin_client_order_history", args=[client_profile.pk]) if client_profile else "",
                "secondary_label": "Cliente" if client_profile else "",
                "created_at": order_request.created_at,
            }
        )

    for order in order_list:
        remito = remito_by_order_id.get(order.pk)
        invoice = invoice_by_order_id.get(order.pk)
        amount = _format_order_amount(order) or Decimal("0.00")
        paid_amount = getattr(order, "total_paid", Decimal("0.00")) or Decimal("0.00")
        pending_amount = max(amount - paid_amount, Decimal("0.00"))
        client_profile = getattr(getattr(order, "client_company_ref", None), "client_profile", None)
        client_name = (
            getattr(client_profile, "company_name", "")
            or getattr(order, "client_company", "")
            or getattr(getattr(order, "user", None), "username", "")
            or "-"
        )
        username = getattr(getattr(order, "user", None), "username", "")
        if order.status == Order.STATUS_DRAFT:
            stage_label = "Borrador operativo"
            next_step_label = "Completar items, validar precios y confirmar la venta."
        elif not remito and order.status in [Order.STATUS_CONFIRMED, Order.STATUS_PREPARING]:
            stage_label = "Pendiente de remito"
            next_step_label = "La venta ya puede pasar por remito."
        elif not invoice and order.status in [Order.STATUS_CONFIRMED, Order.STATUS_PREPARING, Order.STATUS_SHIPPED, Order.STATUS_DELIVERED]:
            stage_label = "Pendiente de factura"
            next_step_label = "Emitir o registrar comprobante fiscal."
        elif pending_amount > Decimal("0.00") and order.status != Order.STATUS_CANCELLED:
            stage_label = "Pendiente de cobro"
            next_step_label = f"Saldo pendiente ${pending_amount:.2f}."
        elif order.status == Order.STATUS_CANCELLED:
            stage_label = "Operacion cancelada"
            next_step_label = "Quedo fuera de la cola operativa."
        else:
            stage_label = "Flujo cerrado"
            next_step_label = "Venta documentada y sin saldo pendiente."

        refs = []
        if order.source_request_id:
            refs.append(f"Solicitud #{order.source_request_id}")
        if remito:
            refs.append(f"Remito {remito.display_number}")
        if invoice:
            refs.append(f"Factura {invoice.display_number}")
        if paid_amount > Decimal("0.00"):
            refs.append(f"Cobrado ${paid_amount:.2f}")

        rows.append(
            {
                "sort_at": order.updated_at or order.created_at,
                "kind": "order",
                "request_id": order.source_request_id,
                "order_id": order.pk,
                "company_id": order.company_id,
                "client_profile_id": getattr(client_profile, "pk", None),
                "kind_label": "Pedido operativo",
                "kind_badge": "badge-primary",
                "record_label": f"Pedido #{order.pk}",
                "company_name": getattr(getattr(order, "company", None), "name", "-"),
                "client_name": client_name,
                "client_meta": username if username and username != client_name else "",
                "stage_label": stage_label,
                "next_step_label": next_step_label,
                "status_label": order.get_status_display(),
                "status_badge": (
                    "badge-danger" if order.status == Order.STATUS_CANCELLED else
                    "badge-success" if pending_amount <= Decimal("0.00") and order.status == Order.STATUS_DELIVERED else
                    "badge-warning" if order.status in [Order.STATUS_DRAFT, Order.STATUS_CONFIRMED, Order.STATUS_PREPARING] else
                    "badge-info"
                ),
                "amount": amount,
                "summary": " | ".join(refs) if refs else "Sin documentos ni cobros vinculados todavia.",
                "detail_url": reverse("admin_order_detail", args=[order.pk]),
                "detail_label": "Abrir venta",
                "secondary_url": reverse("admin_client_order_history", args=[client_profile.pk]) if client_profile else "",
                "secondary_label": "Cliente" if client_profile else "",
                "created_at": order.created_at,
            }
        )

    rows.sort(key=lambda item: (item["sort_at"], item["created_at"]), reverse=True)
    return rows


def build_order_flow_steps(
    order,
    *,
    order_documents=None,
    order_invoice_document=None,
    order_has_external_invoice=False,
    order_paid_amount=None,
    order_pending_amount=None,
    client_profile_id=None,
):
    order_documents = list(order_documents or [])
    order_paid_amount = order_paid_amount if order_paid_amount is not None else Decimal("0.00")
    order_pending_amount = order_pending_amount if order_pending_amount is not None else Decimal("0.00")
    latest_remito = next((doc for doc in reversed(order_documents) if getattr(doc, "doc_type", "") == "REM"), None)

    request_url = reverse("admin_order_request_detail", args=[order.source_request_id]) if order.source_request_id else ""
    request_summary = (
        f"Solicitud #{order.source_request_id} vinculada al pedido."
        if order.source_request_id
        else "Pedido creado sin solicitud previa."
    )
    request_state = "done" if order.source_request_id else "muted"

    order_state = "current" if order.normalized_status() == Order.STATUS_DRAFT else "done"
    if order.status == Order.STATUS_CANCELLED:
        order_state = "danger"

    if latest_remito:
        remito_state = "done"
        remito_summary = f"Remito {latest_remito.display_number} ya generado."
        remito_url = f"{reverse('admin_internal_document_print', args=[latest_remito.pk])}?copy=original"
        remito_label = "Abrir remito"
    elif order.status in [Order.STATUS_CONFIRMED, Order.STATUS_PREPARING]:
        remito_state = "current"
        remito_summary = "La venta ya puede pasar por remito."
        remito_url = reverse("admin_order_detail", args=[order.pk])
        remito_label = "Preparar remito"
    elif order.status in [Order.STATUS_SHIPPED, Order.STATUS_DELIVERED]:
        remito_state = "warning"
        remito_summary = "La venta avanzo de estado pero no tiene remito interno vinculado."
        remito_url = reverse("admin_order_detail", args=[order.pk])
        remito_label = "Revisar"
    else:
        remito_state = "muted"
        remito_summary = "Todavia no corresponde generar remito."
        remito_url = reverse("admin_order_detail", args=[order.pk])
        remito_label = "Ver pedido"

    if order_invoice_document:
        invoice_state = "done"
        invoice_summary = f"{order_invoice_document.commercial_type_label} {order_invoice_document.display_number}."
        invoice_url = reverse("admin_fiscal_document_detail", args=[order_invoice_document.pk])
        invoice_label = "Abrir factura"
    elif order_has_external_invoice:
        invoice_state = "done"
        invoice_summary = "La factura ya fue registrada en un sistema externo."
        invoice_url = reverse("admin_order_detail", args=[order.pk])
        invoice_label = "Ver venta"
    elif order.status in [Order.STATUS_CONFIRMED, Order.STATUS_PREPARING, Order.STATUS_SHIPPED, Order.STATUS_DELIVERED]:
        invoice_state = "current"
        invoice_summary = "La venta ya esta en etapa de facturacion."
        invoice_url = reverse("admin_order_detail", args=[order.pk])
        invoice_label = "Facturar"
    else:
        invoice_state = "muted"
        invoice_summary = "La factura queda pendiente hasta confirmar la venta."
        invoice_url = reverse("admin_order_detail", args=[order.pk])
        invoice_label = "Ver pedido"

    payment_query = {
        "order_id": order.pk,
        "company_id": order.company_id,
    }
    if client_profile_id:
        payment_query["client_id"] = client_profile_id
    payment_url = f"{reverse('admin_payment_list')}?{urlencode(payment_query)}"
    if order_pending_amount <= Decimal("0.00"):
        collection_state = "done"
        collection_summary = f"Cobrado completo. Total aplicado ${order_paid_amount:.2f}."
        collection_label = "Ver cobros"
    elif order_paid_amount > Decimal("0.00"):
        collection_state = "current"
        collection_summary = (
            f"Cobro parcial de ${order_paid_amount:.2f}. Queda pendiente ${order_pending_amount:.2f}."
        )
        collection_label = "Registrar cobro"
    elif order.status in [Order.STATUS_CONFIRMED, Order.STATUS_PREPARING, Order.STATUS_SHIPPED, Order.STATUS_DELIVERED]:
        collection_state = "warning"
        collection_summary = f"Sin cobros aplicados. Pendiente ${order_pending_amount:.2f}."
        collection_label = "Registrar cobro"
    else:
        collection_state = "muted"
        collection_summary = "El cobro queda pendiente hasta avanzar la venta."
        collection_label = "Ver cobros"

    return [
        {
            "key": "request",
            "label": "Solicitud",
            "state": request_state,
            "summary": request_summary,
            "action_url": request_url,
            "action_label": "Abrir solicitud" if request_url else "",
            "action_target_blank": False,
        },
        {
            "key": "order",
            "label": "Pedido",
            "state": order_state,
            "summary": f"{order.get_status_display()} en {getattr(order.company, 'name', '-')}.",
            "action_url": reverse("admin_order_detail", args=[order.pk]),
            "action_label": "Pedido actual",
            "action_target_blank": False,
        },
        {
            "key": "remito",
            "label": "Remito",
            "state": remito_state,
            "summary": remito_summary,
            "action_url": remito_url,
            "action_label": remito_label,
            "action_target_blank": bool(latest_remito),
        },
        {
            "key": "invoice",
            "label": "Factura",
            "state": invoice_state,
            "summary": invoice_summary,
            "action_url": invoice_url,
            "action_label": invoice_label,
            "action_target_blank": False,
        },
        {
            "key": "collection",
            "label": "Cobro",
            "state": collection_state,
            "summary": collection_summary,
            "action_url": payment_url,
            "action_label": collection_label,
            "action_target_blank": False,
        },
    ]
