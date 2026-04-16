from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone

from accounts.models import ClientCompany, ClientPayment, ClientProfile, ClientTransaction
from accounts.services.account_movement_service import (
    sync_order_charge_transaction,
    sync_payment_transaction,
)
from accounts.services.movement_lifecycle import apply_transaction_state_transition
from catalog.models import Product
from core.models import (
    SALES_BEHAVIOR_FACTURA,
    SALES_BILLING_MODE_MANUAL_FISCAL,
    Company,
    DocumentSeries,
    FiscalDocument,
    FiscalPointOfSale,
    InternalDocument,
    SalesDocumentType,
)
from core.services.documents import ensure_document_for_payment
from core.services.fiscal_documents import close_fiscal_document
from core.services.sales_documents import (
    create_fiscal_document_from_sales_type,
    reserve_sales_document_number,
)
from orders.models import (
    Order,
    OrderItem,
    OrderRequest,
    OrderRequestEvent,
    OrderRequestItem,
)


DEMO_MARKER = "DEMO_FLOW_E2E"
DEMO_USERNAME = "demo_flujo_ventas"
DEMO_EMAIL = "demo.flujo.ventas@example.com"
DEMO_CLIENT_NAME = "Cliente Demo Flujo Completo"
DEMO_PRODUCT_SKU = "DEMO-FLOW-E2E"
DEMO_ORDER_EXTERNAL_SYSTEM = "demo_flow_e2e"
DEMO_ORDER_EXTERNAL_ID = "main"
DEMO_PAYMENT_REFERENCE = "DEMO-FLOW-E2E-COBRO"
DEMO_MANUAL_INVOICE_CODE = "demo-factura-manual"


@dataclass
class DemoSalesFlowResult:
    company: Company
    actor: User | None
    client_profile: ClientProfile
    client_company: ClientCompany
    product: Product
    order_request: OrderRequest
    order: Order
    remito: InternalDocument | None
    invoice: FiscalDocument | None
    payment: ClientPayment | None
    receipt: InternalDocument | None
    order_charge_transaction: ClientTransaction | None
    payment_transaction: ClientTransaction | None


def ensure_demo_sales_flow(*, company: Company, actor: User | None = None) -> DemoSalesFlowResult:
    if not company:
        raise ValueError("Empresa obligatoria para sembrar el demo de ventas.")

    with transaction.atomic():
        actor = _resolve_actor(company=company, actor=actor)
        client_profile, client_company = _ensure_demo_client(company=company)
        product = _ensure_demo_product()
        order_request = _ensure_demo_order_request(
            company=company,
            client_profile=client_profile,
            client_company=client_company,
            product=product,
            actor=actor,
        )
        order = _ensure_demo_order(
            company=company,
            client_profile=client_profile,
            client_company=client_company,
            product=product,
            order_request=order_request,
            actor=actor,
        )
        remito = _ensure_demo_remito(order=order)
        invoice = _ensure_demo_invoice(order=order, company=company, actor=actor)
        order_charge_tx = sync_order_charge_transaction(order=order, actor=actor)
        if order_charge_tx:
            _close_transaction_if_needed(order_charge_tx, actor=actor)
            order_charge_tx.refresh_from_db()

        payment = _ensure_demo_payment(
            order=order,
            client_profile=client_profile,
            company=company,
            actor=actor,
        )
        receipt = ensure_document_for_payment(payment) if payment else None
        payment_tx = sync_payment_transaction(payment=payment, actor=actor) if payment else None
        if payment_tx:
            _close_transaction_if_needed(payment_tx, actor=actor)
            payment_tx.refresh_from_db()

        return DemoSalesFlowResult(
            company=company,
            actor=actor,
            client_profile=client_profile,
            client_company=client_company,
            product=product,
            order_request=order_request,
            order=order,
            remito=remito,
            invoice=invoice,
            payment=payment,
            receipt=receipt,
            order_charge_transaction=order_charge_tx,
            payment_transaction=payment_tx,
        )


def _resolve_actor(*, company: Company, actor: User | None) -> User | None:
    if actor and actor.is_staff:
        return actor
    return User.objects.filter(is_staff=True, is_active=True).order_by("id").first()


def _ensure_demo_client(*, company: Company) -> tuple[ClientProfile, ClientCompany]:
    user, created = User.objects.get_or_create(
        username=DEMO_USERNAME,
        defaults={
            "email": DEMO_EMAIL,
            "first_name": "Cliente",
            "last_name": "Demo",
            "is_active": True,
        },
    )
    if created or not user.email:
        user.email = DEMO_EMAIL
        user.first_name = user.first_name or "Cliente"
        user.last_name = user.last_name or "Demo"
        user.is_active = True
        user.save(update_fields=["email", "first_name", "last_name", "is_active"])

    profile, _ = ClientProfile.objects.get_or_create(
        user=user,
        defaults={
            "company_name": DEMO_CLIENT_NAME,
            "document_type": "cuit",
            "document_number": "30711222334",
            "cuit_dni": "30-71122233-4",
            "phone": "11 5555-0101",
            "address": "Av. Demo 1234",
            "fiscal_address": "Av. Demo 1234",
            "fiscal_city": "San Martin",
            "fiscal_province": "Buenos Aires",
            "postal_code": "1650",
            "iva_condition": "responsable_inscripto",
            "notes": "Cliente demo sembrado para validar el flujo comercial end-to-end.",
            "is_approved": True,
        },
    )
    profile.company_name = DEMO_CLIENT_NAME
    profile.document_type = profile.document_type or "cuit"
    profile.document_number = profile.document_number or "30711222334"
    profile.cuit_dni = profile.cuit_dni or "30-71122233-4"
    profile.phone = profile.phone or "11 5555-0101"
    profile.address = profile.address or "Av. Demo 1234"
    profile.fiscal_address = profile.fiscal_address or "Av. Demo 1234"
    profile.fiscal_city = profile.fiscal_city or "San Martin"
    profile.fiscal_province = profile.fiscal_province or "Buenos Aires"
    profile.postal_code = profile.postal_code or "1650"
    profile.iva_condition = profile.iva_condition or "responsable_inscripto"
    profile.is_approved = True
    profile.notes = (profile.notes or "").strip() or "Cliente demo sembrado para validar el flujo comercial end-to-end."
    profile.save()

    company_link, _ = ClientCompany.objects.get_or_create(
        client_profile=profile,
        company=company,
        defaults={"is_active": True},
    )
    if not company_link.is_active:
        company_link.is_active = True
        company_link.save(update_fields=["is_active", "updated_at"])
    return profile, company_link


def _ensure_demo_product() -> Product:
    product, _ = Product.objects.get_or_create(
        sku=DEMO_PRODUCT_SKU,
        defaults={
            "name": "Producto demo flujo end-to-end",
            "description": "Producto semilla para validar pedido, remito, factura y cobro.",
            "cost": Decimal("8500.00"),
            "price": Decimal("12500.00"),
            "stock": 100,
            "is_active": True,
        },
    )
    product.name = product.name or "Producto demo flujo end-to-end"
    product.description = product.description or "Producto semilla para validar pedido, remito, factura y cobro."
    product.cost = product.cost or Decimal("8500.00")
    product.price = product.price or Decimal("12500.00")
    product.stock = max(int(product.stock or 0), 100)
    product.is_active = True
    product.save()
    return product


def _ensure_demo_order_request(
    *,
    company: Company,
    client_profile: ClientProfile,
    client_company: ClientCompany,
    product: Product,
    actor: User | None,
) -> OrderRequest:
    order_request, _ = OrderRequest.objects.get_or_create(
        company=company,
        user=client_profile.user,
        client_company_ref=client_company,
        client_note=DEMO_MARKER,
        defaults={
            "status": OrderRequest.STATUS_CONVERTED,
            "origin_channel": Order.ORIGIN_CATALOG,
            "admin_note": "Solicitud demo convertida para validar el flujo comercial completo.",
            "requested_subtotal": product.price,
            "requested_total": product.price,
            "submitted_at": timezone.now(),
            "reviewed_at": timezone.now(),
            "decided_at": timezone.now(),
            "converted_at": timezone.now(),
        },
    )
    changed_fields = []
    if order_request.status != OrderRequest.STATUS_CONVERTED:
        order_request.status = OrderRequest.STATUS_CONVERTED
        changed_fields.append("status")
    if order_request.origin_channel != Order.ORIGIN_CATALOG:
        order_request.origin_channel = Order.ORIGIN_CATALOG
        changed_fields.append("origin_channel")
    if order_request.requested_subtotal != product.price:
        order_request.requested_subtotal = product.price
        changed_fields.append("requested_subtotal")
    if order_request.requested_total != product.price:
        order_request.requested_total = product.price
        changed_fields.append("requested_total")
    if not order_request.submitted_at:
        order_request.submitted_at = timezone.now()
        changed_fields.append("submitted_at")
    if not order_request.reviewed_at:
        order_request.reviewed_at = timezone.now()
        changed_fields.append("reviewed_at")
    if not order_request.decided_at:
        order_request.decided_at = timezone.now()
        changed_fields.append("decided_at")
    if not order_request.converted_at:
        order_request.converted_at = timezone.now()
        changed_fields.append("converted_at")
    if not order_request.admin_note:
        order_request.admin_note = "Solicitud demo convertida para validar el flujo comercial completo."
        changed_fields.append("admin_note")
    if changed_fields:
        changed_fields.append("updated_at")
        order_request.save(update_fields=changed_fields)

    OrderRequestItem.objects.get_or_create(
        order_request=order_request,
        line_number=1,
        defaults={
            "product": product,
            "product_sku": product.sku,
            "product_name": product.name,
            "quantity": 1,
            "unit_price_base": product.price,
            "price_at_snapshot": product.price,
        },
    )

    _ensure_request_event(
        order_request=order_request,
        event_type=OrderRequestEvent.EVENT_CREATED,
        actor=actor,
        to_status=OrderRequest.STATUS_SUBMITTED,
        message="Solicitud demo creada desde catalogo.",
    )
    _ensure_request_event(
        order_request=order_request,
        event_type=OrderRequestEvent.EVENT_CONFIRMED,
        actor=actor,
        from_status=OrderRequest.STATUS_SUBMITTED,
        to_status=OrderRequest.STATUS_CONFIRMED,
        message="Solicitud demo validada por ventas.",
    )
    _ensure_request_event(
        order_request=order_request,
        event_type=OrderRequestEvent.EVENT_CONVERTED,
        actor=actor,
        from_status=OrderRequest.STATUS_CONFIRMED,
        to_status=OrderRequest.STATUS_CONVERTED,
        message="Solicitud demo convertida a pedido operativo.",
    )
    return order_request


def _ensure_request_event(
    *,
    order_request: OrderRequest,
    event_type: str,
    actor: User | None,
    from_status: str = "",
    to_status: str = "",
    message: str = "",
) -> None:
    if order_request.events.filter(event_type=event_type, message=message).exists():
        return
    OrderRequestEvent.objects.create(
        order_request=order_request,
        event_type=event_type,
        from_status=from_status,
        to_status=to_status,
        message=message,
        actor=actor if getattr(actor, "is_authenticated", False) else None,
    )


def _ensure_demo_order(
    *,
    company: Company,
    client_profile: ClientProfile,
    client_company: ClientCompany,
    product: Product,
    order_request: OrderRequest,
    actor: User | None,
) -> Order:
    order, created = Order.objects.get_or_create(
        company=company,
        user=client_profile.user,
        external_system=DEMO_ORDER_EXTERNAL_SYSTEM,
        external_id=DEMO_ORDER_EXTERNAL_ID,
        defaults={
            "client_company_ref": client_company,
            "source_request": order_request,
            "origin_channel": Order.ORIGIN_CATALOG,
            "status": Order.STATUS_DRAFT,
            "priority": Order.PRIORITY_NORMAL,
            "client_company": client_profile.company_name,
            "client_cuit": client_profile.cuit_dni or client_profile.document_number,
            "client_address": client_profile.fiscal_address or client_profile.address,
            "client_phone": client_profile.phone,
            "subtotal": product.price,
            "total": product.price,
            "admin_notes": f"{DEMO_MARKER} :: pedido demo sembrado para validacion manual.",
            "notes": "Pedido demo generado para revisar el flujo comercial completo.",
        },
    )

    changed_fields = []
    if order.client_company_ref_id != client_company.pk:
        order.client_company_ref = client_company
        changed_fields.append("client_company_ref")
    if order.source_request_id != order_request.pk:
        order.source_request = order_request
        changed_fields.append("source_request")
    if order.origin_channel != Order.ORIGIN_CATALOG:
        order.origin_channel = Order.ORIGIN_CATALOG
        changed_fields.append("origin_channel")
    if order.client_company != client_profile.company_name:
        order.client_company = client_profile.company_name
        changed_fields.append("client_company")
    desired_cuit = client_profile.cuit_dni or client_profile.document_number
    if order.client_cuit != desired_cuit:
        order.client_cuit = desired_cuit
        changed_fields.append("client_cuit")
    desired_address = client_profile.fiscal_address or client_profile.address
    if order.client_address != desired_address:
        order.client_address = desired_address
        changed_fields.append("client_address")
    if order.client_phone != client_profile.phone:
        order.client_phone = client_profile.phone
        changed_fields.append("client_phone")
    desired_admin_notes = f"{DEMO_MARKER} :: pedido demo sembrado para validacion manual."
    if order.admin_notes != desired_admin_notes:
        order.admin_notes = desired_admin_notes
        changed_fields.append("admin_notes")
    desired_notes = "Pedido demo generado para revisar el flujo comercial completo."
    if order.notes != desired_notes:
        order.notes = desired_notes
        changed_fields.append("notes")
    if changed_fields:
        changed_fields.append("updated_at")
        order.save(update_fields=changed_fields)

    item = order.items.filter(product_sku=product.sku).first()
    if not item:
        OrderItem.objects.create(
            order=order,
            product=product,
            product_sku=product.sku,
            product_name=product.name,
            quantity=1,
            unit_price_base=product.price,
            price_at_purchase=product.price,
        )
    order_line_total = sum((row.subtotal for row in order.items.all()), Decimal("0.00"))
    order.subtotal = order_line_total or product.price
    order.total = order_line_total or product.price
    order.discount_amount = Decimal("0.00")
    order.discount_percentage = Decimal("0.00")
    order.save(update_fields=["subtotal", "total", "discount_amount", "discount_percentage", "updated_at"])

    for target_status in [
        Order.STATUS_CONFIRMED,
        Order.STATUS_PREPARING,
        Order.STATUS_SHIPPED,
        Order.STATUS_DELIVERED,
    ]:
        if order.normalized_status() == target_status:
            continue
        if order.can_transition_to(target_status):
            order.change_status(
                target_status,
                changed_by=actor,
                note=f"Transicion automatica del seed demo hacia {target_status}.",
            )
            order.refresh_from_db()
    if order_request.status != OrderRequest.STATUS_CONVERTED:
        order_request.status = OrderRequest.STATUS_CONVERTED
        if not order_request.converted_at:
            order_request.converted_at = timezone.now()
        order_request.save(update_fields=["status", "converted_at", "updated_at"])
    return order


def _ensure_demo_remito(*, order: Order) -> InternalDocument | None:
    return (
        InternalDocument.objects.filter(order=order, doc_type=DocumentSeries.DOC_REM)
        .exclude(is_cancelled=True)
        .order_by("-issued_at", "-id")
        .first()
    )


def _ensure_demo_invoice(*, order: Order, company: Company, actor: User | None) -> FiscalDocument:
    point_of_sale = _ensure_point_of_sale(company=company)
    sales_document_type = _ensure_manual_invoice_type(company=company, point_of_sale=point_of_sale)
    fiscal_document, _ = create_fiscal_document_from_sales_type(
        order=order,
        sales_document_type=sales_document_type,
        actor=actor,
        require_invoice_ready=True,
    )
    if fiscal_document.number is None:
        fiscal_document.number = reserve_sales_document_number(sales_document_type=sales_document_type)
        fiscal_document.save(update_fields=["number", "updated_at"])
    if fiscal_document.status != "external_recorded":
        fiscal_document, _ = close_fiscal_document(fiscal_document=fiscal_document, actor=actor)
    return fiscal_document


def _ensure_point_of_sale(*, company: Company) -> FiscalPointOfSale:
    point = (
        FiscalPointOfSale.objects.filter(company=company, is_default=True).order_by("id").first()
        or FiscalPointOfSale.objects.filter(company=company, is_active=True).order_by("id").first()
    )
    if point:
        if not point.is_active or not point.is_default:
            point.is_active = True
            point.is_default = True
            point.save(update_fields=["is_active", "is_default", "updated_at"])
        if company.point_of_sale_default != point.number:
            company.point_of_sale_default = point.number
            company.save(update_fields=["point_of_sale_default", "updated_at"])
        return point

    point = FiscalPointOfSale.objects.create(
        company=company,
        number=company.point_of_sale_default or "1",
        name="Punto demo",
        is_active=True,
        is_default=True,
    )
    if company.point_of_sale_default != point.number:
        company.point_of_sale_default = point.number
        company.save(update_fields=["point_of_sale_default", "updated_at"])
    return point


def _ensure_manual_invoice_type(*, company: Company, point_of_sale: FiscalPointOfSale) -> SalesDocumentType:
    sales_type, _ = SalesDocumentType.objects.update_or_create(
        company=company,
        code=DEMO_MANUAL_INVOICE_CODE,
        defaults={
            "name": "Factura demo manual",
            "letter": "B",
            "point_of_sale": point_of_sale,
            "enabled": True,
            "document_behavior": SALES_BEHAVIOR_FACTURA,
            "generate_stock_movement": False,
            "generate_account_movement": True,
            "billing_mode": SALES_BILLING_MODE_MANUAL_FISCAL,
            "fiscal_doc_type": "FB",
            "internal_doc_type": "",
            "is_default": False,
            "default_origin_channel": "",
            "display_order": 999,
            "notes": "Tipo de documento sembrado para demo local del flujo comercial.",
        },
    )
    return sales_type


def _ensure_demo_payment(
    *,
    order: Order,
    client_profile: ClientProfile,
    company: Company,
    actor: User | None,
) -> ClientPayment:
    payment, _ = ClientPayment.objects.get_or_create(
        client_profile=client_profile,
        company=company,
        order=order,
        reference=DEMO_PAYMENT_REFERENCE,
        defaults={
            "amount": order.total,
            "method": ClientPayment.METHOD_TRANSFER,
            "notes": "Cobro demo sembrado para validar el flujo comercial completo.",
            "created_by": actor if getattr(actor, "is_authenticated", False) else None,
            "paid_at": timezone.now(),
        },
    )
    changed_fields = []
    if payment.amount != order.total:
        payment.amount = order.total
        changed_fields.append("amount")
    if payment.method != ClientPayment.METHOD_TRANSFER:
        payment.method = ClientPayment.METHOD_TRANSFER
        changed_fields.append("method")
    if payment.is_cancelled:
        payment.is_cancelled = False
        payment.cancelled_at = None
        payment.cancel_reason = ""
        changed_fields.extend(["is_cancelled", "cancelled_at", "cancel_reason"])
    expected_notes = "Cobro demo sembrado para validar el flujo comercial completo."
    if payment.notes != expected_notes:
        payment.notes = expected_notes
        changed_fields.append("notes")
    if payment.created_by_id != getattr(actor, "pk", None):
        payment.created_by = actor if getattr(actor, "is_authenticated", False) else None
        changed_fields.append("created_by")
    if changed_fields:
        changed_fields.append("updated_at")
        payment.save(update_fields=changed_fields)
    return payment


def _close_transaction_if_needed(transaction_obj: ClientTransaction | None, *, actor: User | None) -> None:
    if not transaction_obj:
        return
    if transaction_obj.movement_state == ClientTransaction.STATE_CLOSED:
        return
    apply_transaction_state_transition(
        transaction_obj=transaction_obj,
        target_state=ClientTransaction.STATE_CLOSED,
        actor=actor,
    )
