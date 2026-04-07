"""
Orders app views - Cart, orders, and client portal.
"""
import json
import logging
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Prefetch
from django.http import JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import ClientCategory, ClientPayment
from catalog.models import Product
from core.models import FiscalDocument, InternalDocument, SiteSettings
from core.services.company_context import get_active_company
from core.services.pricing import (
    calculate_cart_pricing,
    calculate_final_price,
    get_base_price_for_product,
    resolve_effective_discount_percentage,
    resolve_pricing_context,
)

from .models import (
    Cart,
    CartItem,
    ClientFavoriteProduct,
    Order,
    OrderItem,
    OrderProposal,
    OrderProposalItem,
    OrderRequest,
    OrderRequestEvent,
    OrderRequestItem,
    OrderStatusHistory,
)
from .services.request_workflow import (
    accept_order_proposal,
    build_order_request_from_cart,
    reject_order_proposal,
)

logger = logging.getLogger(__name__)

FISCAL_PRINT_DOC_META = {
    "FA": {"letter": "A", "code": "001"},
    "FB": {"letter": "B", "code": "006"},
    "NCA": {"letter": "A", "code": "003"},
    "NCB": {"letter": "B", "code": "008"},
}
FISCAL_PRINT_COPY_LABELS = {
    "original": "ORIGINAL",
    "duplicado": "DUPLICADO",
    "triplicado": "TRIPLICADO",
}


def _get_client_profile(user):
    return getattr(user, "client_profile", None)


def _get_discount_decimal(user, company=None):
    client_profile, client_company, client_category = resolve_pricing_context(user, company)
    discount_percentage = resolve_effective_discount_percentage(
        client_profile=client_profile,
        company=company,
        client_company=client_company,
        client_category=client_category,
    )
    if not discount_percentage:
        return Decimal("0")
    return Decimal(discount_percentage) / Decimal("100")


def _get_cart_for_company(user, company, create=False):
    if not company:
        return None, False
    if create:
        return Cart.objects.get_or_create(user=user, company=company)
    return Cart.objects.filter(user=user, company=company).first(), False


def _get_client_internal_documents(order):
    if not order:
        return []
    return list(
        InternalDocument.objects.select_related("sales_document_type")
        .filter(order=order)
        .order_by("issued_at", "id")
    )


def _get_client_fiscal_documents(order):
    if not order:
        return []
    return list(
        FiscalDocument.objects.select_related("sales_document_type", "point_of_sale")
        .filter(order=order)
        .order_by("-created_at", "-id")
    )


def _get_client_internal_document_queryset(user, company=None):
    queryset = InternalDocument.objects.select_related(
        "company",
        "client_company_ref__client_profile",
        "client_profile",
        "order",
        "payment",
        "sales_document_type",
    ).filter(order__user=user)
    if company:
        queryset = queryset.filter(company=company)
    return queryset


def _get_client_fiscal_document_queryset(user, company=None):
    queryset = FiscalDocument.objects.select_related(
        "company",
        "point_of_sale",
        "client_company_ref__client_profile",
        "client_profile",
        "order",
        "sales_document_type",
    ).prefetch_related("items__product").filter(order__user=user)
    if company:
        queryset = queryset.filter(company=company)
    return queryset


def _build_client_fiscal_print_context(fiscal_document):
    site_settings = SiteSettings.get_settings()
    client_profile = fiscal_document.client_profile
    if not client_profile and getattr(fiscal_document, "client_company_ref", None):
        client_profile = fiscal_document.client_company_ref.client_profile

    company = fiscal_document.company
    order = fiscal_document.order
    company_meta = FISCAL_PRINT_DOC_META.get(fiscal_document.doc_type, {"letter": "-", "code": "---"})
    sale_condition_label = "-"
    operator_label = "-"
    observations = []

    if order:
        if order.notes:
            observations.append(order.notes)
        if order.admin_notes:
            observations.append(order.admin_notes)
        if getattr(order, "assigned_to", None):
            operator_label = order.assigned_to.get_full_name() or order.assigned_to.username

    client_company_link = getattr(fiscal_document, "client_company_ref", None)
    effective_category = None
    if client_company_link and getattr(client_company_link, "client_category_id", None):
        effective_category = client_company_link.client_category
    elif client_profile:
        try:
            effective_category = client_profile.get_effective_client_category(company=company)
        except Exception:
            effective_category = None
    if effective_category and getattr(effective_category, "default_sale_condition", None):
        sale_condition_label = dict(ClientCategory.SALE_CONDITION_CHOICES).get(
            effective_category.default_sale_condition,
            effective_category.default_sale_condition,
        )
    elif getattr(order, "billing_mode", "") == "official":
        sale_condition_label = "Cuenta corriente"

    company_address_bits = [
        company.fiscal_address,
        company.fiscal_city,
        company.fiscal_province,
        company.postal_code,
    ]
    client_address_bits = []
    if client_profile:
        client_address_bits = [
            client_profile.fiscal_address or client_profile.address,
            client_profile.fiscal_city or client_profile.province,
            client_profile.fiscal_province,
            client_profile.postal_code,
        ]

    subtotal_before_discount = Decimal(fiscal_document.subtotal_net or 0)
    discount_total = Decimal(fiscal_document.discount_total or 0)
    taxable_net = subtotal_before_discount - discount_total
    if taxable_net < 0:
        taxable_net = Decimal("0.00")

    order_total_discount_percentage = Decimal("0.00")
    if order and getattr(order, "discount_percentage", None):
        order_total_discount_percentage = Decimal(order.discount_percentage or 0)

    qr_base64 = ""
    qr_url = ""
    try:
        from core.services.pdf_generator import generate_afip_qr_data, generate_qr_image_base64

        qr_url = generate_afip_qr_data(fiscal_document)
        qr_base64 = generate_qr_image_base64(qr_url)
    except Exception:
        qr_base64 = ""
        qr_url = ""

    return {
        "fiscal_document": fiscal_document,
        "items": fiscal_document.items.all().order_by("line_number"),
        "document_letter": company_meta["letter"],
        "document_code": company_meta["code"],
        "document_number_display": (
            f"{str(fiscal_document.point_of_sale.number or '').zfill(5)}-"
            f"{str(fiscal_document.number or 0).zfill(8)}"
        ),
        "company_address_line": " / ".join(bit for bit in company_address_bits if bit),
        "company_contact_line": " / ".join(
            bit
            for bit in [
                site_settings.company_phone,
                site_settings.company_phone_2,
                company.email or site_settings.company_email,
            ]
            if bit
        ),
        "company_contact_site": site_settings.company_address,
        "client_profile": client_profile,
        "client_address_line": " / ".join(bit for bit in client_address_bits if bit),
        "client_document_label": (
            client_profile.get_document_type_display() if client_profile and client_profile.document_type else "CUIT/DNI"
        )
        if client_profile
        else "CUIT/DNI",
        "client_document_value": (
            client_profile.document_number or client_profile.cuit_dni
        )
        if client_profile
        else "",
        "sale_condition_label": sale_condition_label,
        "operator_label": operator_label,
        "observations_text": "\n".join(bit for bit in observations if bit).strip(),
        "subtotal_before_discount": subtotal_before_discount,
        "taxable_net": taxable_net,
        "order_total_discount_percentage": order_total_discount_percentage,
        "qr_base64": qr_base64,
        "qr_url": qr_url,
    }


def _build_order_totals_from_cart(cart, user, company):
    pricing = calculate_cart_pricing(cart, user=user, company=company)
    return pricing["subtotal"], pricing["discount_amount"], pricing["total"], pricing


def _build_order_from_cart(cart, user, notes="", status=Order.STATUS_CONFIRMED, company=None):
    client_profile = _get_client_profile(user)
    if not company:
        raise ValueError("Empresa activa requerida para confirmar el pedido.")
    if not client_profile or not client_profile.can_operate_in_company(company):
        raise ValueError("Cliente no habilitado para operar en esta empresa.")
    client_company_ref = None
    if client_profile and company:
        client_company_ref = client_profile.get_company_link(company)
    if not client_company_ref or client_company_ref.company_id != company.id:
        raise ValueError("No se pudo validar la relacion cliente-empresa.")
    subtotal, discount_amount, total, pricing = _build_order_totals_from_cart(cart, user, company)
    discount_percentage = pricing["discount_percentage"]
    price_list = pricing["price_list"]
    item_map = pricing["item_map"]
    order = Order.objects.create(
        user=user,
        company=company,
        origin_channel=Order.ORIGIN_CATALOG,
        status=status,
        priority=Order.PRIORITY_NORMAL,
        notes=(notes or "").strip(),
        subtotal=subtotal,
        discount_percentage=discount_percentage,
        discount_amount=discount_amount,
        total=total,
        client_company=client_profile.company_name if client_profile else "",
        client_cuit=client_profile.cuit_dni if client_profile else "",
        client_address=client_profile.address if client_profile else "",
        client_phone=client_profile.phone if client_profile else "",
        client_company_ref=client_company_ref,
        saas_document_type="",
        saas_document_number="",
        saas_document_cae="",
        follow_up_note="",
    )
    OrderStatusHistory.objects.create(
        order=order,
        from_status="",
        to_status=order.status,
        changed_by=user if user.is_authenticated else None,
        note="Pedido creado por cliente",
    )
    clamp_summary_lines = []
    clamp_request_ids = set()

    for cart_item in cart.items.select_related("product", "clamp_request"):
        base_price = get_base_price_for_product(
            cart_item.product,
            price_list=price_list,
            item_map=item_map,
        )
        final_price = calculate_final_price(base_price, discount_percentage)
        OrderItem.objects.create(
            order=order,
            product=cart_item.product,
            clamp_request=cart_item.clamp_request,
            product_sku=cart_item.product.sku,
            product_name=cart_item.product.name,
            quantity=cart_item.quantity,
            price_at_purchase=final_price,
            unit_price_base=base_price,
            discount_percentage_used=discount_percentage,
            price_list=price_list,
        )

        if cart_item.clamp_request_id:
            clamp_request_ids.add(cart_item.clamp_request_id)
            clamp_summary_lines.append(
                (
                    f"- Solicitud #{cart_item.clamp_request_id}: "
                    f"{cart_item.product.name} | "
                    f"cant. {cart_item.quantity} | "
                    f"${final_price:.2f} c/u"
                )
            )

    # Keep client current-account ledger synchronized from order creation.
    try:
        from accounts.services.ledger import sync_order_charge_transaction

        sync_order_charge_transaction(
            order=order,
            actor=user if getattr(user, "is_authenticated", False) else None,
        )
    except Exception:
        logger.exception("Could not sync ledger for order %s", order.pk)

    if clamp_summary_lines:
        clamp_summary = "Abrazaderas a medida agregadas por cliente:\n" + "\n".join(clamp_summary_lines)
        order.admin_notes = (order.admin_notes or "").strip()
        if order.admin_notes:
            order.admin_notes = f"{order.admin_notes}\n\n{clamp_summary}"
        else:
            order.admin_notes = clamp_summary
        order.save(update_fields=["admin_notes", "updated_at"])

    if clamp_request_ids:
        ClampMeasureRequest = apps.get_model("catalog", "ClampMeasureRequest")
        ClampMeasureRequest.objects.filter(id__in=clamp_request_ids).update(
            ordered_at=timezone.now(),
            updated_at=timezone.now(),
        )

    return order


@login_required
def cart_view(request):
    """View shopping cart."""
    company = get_active_company(request)
    if not company:
        messages.error(request, "No se pudo resolver la empresa activa.")
        return redirect("catalog")
    cart, _ = _get_cart_for_company(request.user, company, create=True)
    pricing = calculate_cart_pricing(cart, user=request.user, company=company)
    discount = pricing["discount_percentage"] / Decimal("100") if pricing["discount_percentage"] else Decimal("0")
    subtotal = pricing["subtotal"]
    discount_amount = pricing["discount_amount"]
    total = pricing["total"]
    context = {
        "cart": cart,
        "cart_items": pricing["items"],
        "discount": discount,
        "discount_display": discount * 100,
        "subtotal": subtotal,
        "discount_amount": discount_amount,
        "total": total,
    }
    return render(request, "orders/cart.html", context)


@login_required
@require_POST
def add_to_cart(request):
    """Add product to cart (AJAX)."""
    try:
        data = json.loads(request.body)
        product_id = data.get("product_id")
        quantity = int(data.get("quantity", 1))
        if quantity < 1:
            quantity = 1

        product = get_object_or_404(
            Product.catalog_visible(Product.objects.select_related("category").prefetch_related("categories")),
            id=product_id,
        )
        company = get_active_company(request)
        if not company:
            return JsonResponse({"success": False, "error": "Empresa activa no disponible."}, status=400)
        cart, _ = _get_cart_for_company(request.user, company, create=True)
        cart_item, created = CartItem.objects.get_or_create(
            cart=cart,
            product=product,
            defaults={"quantity": quantity},
        )

        if not created:
            cart_item.quantity += quantity
            cart_item.save(update_fields=["quantity"])

        return JsonResponse(
            {
                "success": True,
                "message": f"{product.name} agregado al carrito",
                "cart_count": cart.get_item_count(),
            }
        )
    except Exception:
        logger.exception("Error adding product to cart")
        return JsonResponse({"success": False, "error": "No se pudo agregar el producto."}, status=400)


@login_required
@require_POST
def update_cart_item(request):
    """Update cart item quantity (AJAX)."""
    try:
        data = json.loads(request.body)
        item_id = data.get("item_id")
        quantity = int(data.get("quantity", 1))

        company = get_active_company(request)
        if not company:
            return JsonResponse({"success": False, "error": "Empresa activa no disponible."}, status=400)
        cart_item = get_object_or_404(
            CartItem,
            id=item_id,
            cart__user=request.user,
            cart__company=company,
        )
        if quantity <= 0:
            cart_item.delete()
            message = "Producto eliminado del carrito"
        else:
            cart_item.quantity = quantity
            cart_item.save(update_fields=["quantity"])
            message = "Cantidad actualizada"

        cart = cart_item.cart
        pricing = calculate_cart_pricing(cart, user=request.user, company=company)
        return JsonResponse(
            {
                "success": True,
                "message": message,
                "cart_total": float(pricing["subtotal"]),
                "cart_count": cart.get_item_count(),
            }
        )
    except Exception:
        logger.exception("Error updating cart item")
        return JsonResponse({"success": False, "error": "No se pudo actualizar el carrito."}, status=400)


@login_required
@require_POST
def remove_from_cart(request):
    """Remove item from cart (AJAX)."""
    try:
        data = json.loads(request.body)
        item_id = data.get("item_id")
        company = get_active_company(request)
        if not company:
            return JsonResponse({"success": False, "error": "Empresa activa no disponible."}, status=400)
        cart_item = get_object_or_404(
            CartItem,
            id=item_id,
            cart__user=request.user,
            cart__company=company,
        )
        cart = cart_item.cart
        cart_item.delete()
        pricing = calculate_cart_pricing(cart, user=request.user, company=company)
        return JsonResponse(
            {
                "success": True,
                "message": "Producto eliminado",
                "cart_total": float(pricing["subtotal"]),
                "cart_count": cart.get_item_count(),
            }
        )
    except Exception:
        logger.exception("Error removing cart item")
        return JsonResponse({"success": False, "error": "No se pudo eliminar el producto."}, status=400)


@login_required
def checkout(request):
    """Checkout view - submit a commercial request for review."""
    company = get_active_company(request)
    if not company:
        messages.error(request, "No se pudo resolver la empresa activa.")
        return redirect("cart")
    cart, _ = _get_cart_for_company(request.user, company, create=False)
    if not cart:
        messages.warning(request, "Tu carrito esta vacio.")
        return redirect("catalog")
    if cart.items.count() == 0:
        messages.warning(request, "Tu carrito esta vacio.")
        return redirect("catalog")

    client_profile = _get_client_profile(request.user)
    if not client_profile or not client_profile.can_operate_in_company(company):
        messages.error(
            request,
            "No tienes habilitada la operacion comercial para esta empresa.",
        )
        return redirect("cart")
    client_company_ref = client_profile.get_company_link(company)
    if not client_company_ref or client_company_ref.company_id != company.id:
        messages.error(
            request,
            "No se pudo validar la relacion comercial con la empresa seleccionada.",
        )
        return redirect("cart")
    pricing = calculate_cart_pricing(cart, user=request.user, company=company)
    discount = pricing["discount_percentage"] / Decimal("100") if pricing["discount_percentage"] else Decimal("0")
    if request.method == "POST":
        notes = request.POST.get("notes", "").strip()
        with transaction.atomic():
            cart = Cart.objects.select_for_update().get(pk=cart.pk)
            try:
                order_request = build_order_request_from_cart(
                    cart=cart,
                    user=request.user,
                    company=company,
                    client_note=notes,
                    origin_channel=Order.ORIGIN_CATALOG,
                )
            except ValidationError as exc:
                messages.error(request, str(exc))
                return redirect("cart")
            except ValueError as exc:
                messages.error(request, str(exc))
                return redirect("cart")
            cart.clear()
        messages.success(
            request,
            f"Solicitud #{order_request.pk} enviada. La revisaremos antes de confirmar la operacion.",
        )
        return redirect("order_request_detail", request_id=order_request.pk)

    context = {
        "cart": cart,
        "cart_items": pricing["items"],
        "discount": discount,
        "discount_display": discount * 100,
        "subtotal": pricing["subtotal"],
        "discount_amount": pricing["discount_amount"],
        "total": pricing["total"],
    }
    return render(request, "orders/checkout.html", context)


@login_required
def order_request_list(request):
    """List of the client's submitted commercial requests."""
    status = request.GET.get("status", "").strip()
    company = get_active_company(request)
    order_requests = (
        OrderRequest.objects.select_related("company")
        .prefetch_related("items", "proposals", "generated_orders")
        .filter(user=request.user)
    )
    if company:
        order_requests = order_requests.filter(company=company)
    if status:
        order_requests = order_requests.filter(status=status)
    order_requests = order_requests.order_by("-created_at")
    return render(
        request,
        "orders/order_request_list.html",
        {
            "order_requests": order_requests,
            "status": status,
            "status_choices": OrderRequest.STATUS_CHOICES,
        },
    )


@login_required
def order_request_detail(request, request_id):
    """Detailed view of one commercial request and its latest proposal."""
    company = get_active_company(request)
    order_request_qs = OrderRequest.objects.select_related(
        "company",
        "client_company_ref",
        "client_company_ref__client_profile",
    ).prefetch_related(
        Prefetch(
            "items",
            queryset=OrderRequestItem.objects.select_related("product", "clamp_request", "price_list"),
        ),
        Prefetch(
            "proposals",
            queryset=OrderProposal.objects.select_related("created_by", "responded_by")
            .prefetch_related(
                Prefetch(
                    "items",
                    queryset=OrderProposalItem.objects.select_related("product", "clamp_request", "price_list"),
                )
            )
            .order_by("-version_number", "-id"),
        ),
        Prefetch(
            "events",
            queryset=OrderRequestEvent.objects.select_related("actor").order_by("-created_at", "-id"),
        ),
        "generated_orders",
    )
    if company:
        order_request_qs = order_request_qs.filter(company=company)
    order_request = get_object_or_404(
        order_request_qs,
        pk=request_id,
        user=request.user,
    )
    proposals = list(order_request.proposals.all())
    current_proposal = next((proposal for proposal in proposals if proposal.is_current), None)
    request_order = order_request.converted_order
    return render(
        request,
        "orders/order_request_detail.html",
        {
            "order_request": order_request,
            "request_order": request_order,
            "request_order_documents": _get_client_internal_documents(request_order),
            "request_order_fiscal_documents": _get_client_fiscal_documents(request_order),
            "current_proposal": current_proposal,
            "request_events": list(order_request.events.all()[:20]),
            "can_accept_proposal": bool(
                current_proposal
                and current_proposal.status == OrderProposal.STATUS_PENDING
                and order_request.status == OrderRequest.STATUS_WAITING_CLIENT
            ),
            "proposals": proposals,
        },
    )


@login_required
@require_POST
def order_request_accept_proposal(request, request_id, proposal_id):
    """Allow the client to accept the current commercial proposal."""
    company = get_active_company(request)
    order_request_qs = OrderRequest.objects.filter(pk=request_id, user=request.user)
    if company:
        order_request_qs = order_request_qs.filter(company=company)
    order_request = get_object_or_404(order_request_qs)
    proposal = get_object_or_404(
        OrderProposal.objects.filter(order_request=order_request),
        pk=proposal_id,
    )
    try:
        accept_order_proposal(order_proposal=proposal, actor=request.user)
    except ValidationError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            "Aceptaste la propuesta comercial. La solicitud ya quedo confirmada para continuar el circuito.",
        )
    return redirect("order_request_detail", request_id=order_request.pk)


@login_required
@require_POST
def order_request_reject_proposal(request, request_id, proposal_id):
    """Allow the client to reject the current commercial proposal."""
    company = get_active_company(request)
    order_request_qs = OrderRequest.objects.filter(pk=request_id, user=request.user)
    if company:
        order_request_qs = order_request_qs.filter(company=company)
    order_request = get_object_or_404(order_request_qs)
    proposal = get_object_or_404(
        OrderProposal.objects.filter(order_request=order_request),
        pk=proposal_id,
    )
    try:
        reject_order_proposal(order_proposal=proposal, actor=request.user)
    except ValidationError as exc:
        messages.error(request, str(exc))
    else:
        messages.info(
            request,
            "Rechazaste la propuesta. La solicitud vuelve a revision comercial para un nuevo ajuste.",
        )
    return redirect("order_request_detail", request_id=order_request.pk)


@login_required
def order_list(request):
    """List of user's operational orders."""
    status = request.GET.get("status", "").strip()
    company = get_active_company(request)
    orders = Order.objects.filter(user=request.user).prefetch_related("items")
    if company:
        orders = orders.filter(company=company)
    if status:
        orders = orders.filter(status=status)
    orders = orders.order_by("-created_at")
    return render(
        request,
        "orders/order_list.html",
        {
            "orders": orders,
            "status": status,
            "status_choices": Order.STATUS_CHOICES,
        },
    )


@login_required
def order_detail(request, order_id):
    """Order detail view."""
    company = get_active_company(request)
    order_qs = Order.objects.prefetch_related(
        Prefetch("items", queryset=OrderItem.objects.select_related("product", "clamp_request")),
        Prefetch("status_history", queryset=OrderStatusHistory.objects.select_related("changed_by")),
    )
    if company:
        order_qs = order_qs.filter(company=company)
    order = get_object_or_404(
        order_qs,
        pk=order_id,
        user=request.user,
    )
    payments_qs = (
        ClientPayment.objects.filter(order_id=order.pk, is_cancelled=False)
        .select_related("created_by")
        .order_by("-paid_at")
    )
    if company:
        payments_qs = payments_qs.filter(company=company)
    payments = list(payments_qs)
    return render(
        request,
        "orders/order_detail.html",
        {
            "order": order,
            "order_paid_amount": order.get_paid_amount(),
            "order_pending_amount": order.get_pending_amount(),
            "payments": payments,
            "order_documents": _get_client_internal_documents(order),
            "order_fiscal_documents": _get_client_fiscal_documents(order),
        },
    )


@login_required
def order_internal_document_print(request, doc_id):
    """Client-safe printable view of one internal document linked to their own order."""
    company = get_active_company(request)
    document = get_object_or_404(
        _get_client_internal_document_queryset(request.user, company=company),
        pk=doc_id,
    )
    copy_key = str(request.GET.get("copy", "original")).strip().lower()
    copy_labels = {
        "original": "ORIGINAL",
        "duplicado": "DUPLICADO",
        "triplicado": "TRIPLICADO",
    }
    if copy_key not in copy_labels:
        copy_key = "original"
    copy_label = copy_labels.get(copy_key, "ORIGINAL")
    order_items = []
    if document.order_id:
        order_items = list(document.order.items.select_related("product").all())

    context = {
        "document": document,
        "copy_key": copy_key,
        "copy_label": copy_label,
        "order_items": order_items,
    }

    if request.GET.get("format") == "pdf":
        try:
            from core.services.pdf_generator import generate_document_pdf

            html_string = render_to_string("admin_panel/documents/print.html", context, request=request)
            pdf_bytes = generate_document_pdf(html_string, base_url=request.build_absolute_uri("/"))
            response = HttpResponse(pdf_bytes, content_type="application/pdf")
            response["Content-Disposition"] = (
                f'inline; filename="{document.doc_type}_{document.number:07d}_{copy_key}.pdf"'
            )
            return response
        except ImportError:
            messages.error(request, "El generador de PDF no esta instalado correctamente.")
        except Exception as exc:
            messages.error(request, f"Error al generar PDF: {exc}")

    response = render(
        request,
        "admin_panel/documents/print.html",
        context,
    )
    if request.GET.get("download") == "1":
        filename = f"{document.doc_type}_{document.number:07d}.html"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
def order_fiscal_document_print(request, doc_id):
    """Client-safe printable view of one fiscal document linked to their own order."""
    company = get_active_company(request)
    fiscal_document = get_object_or_404(
        _get_client_fiscal_document_queryset(request.user, company=company),
        pk=doc_id,
    )
    copy_type = str(request.GET.get("copy", "original")).strip().lower()
    if copy_type not in {"original", "duplicado", "triplicado"}:
        copy_type = "original"

    context = _build_client_fiscal_print_context(fiscal_document)
    context.update(
        {
            "copy_type": copy_type,
            "copy_label": FISCAL_PRINT_COPY_LABELS.get(copy_type, "ORIGINAL"),
        }
    )
    if request.GET.get("format") == "pdf":
        try:
            from core.services.pdf_generator import generate_document_pdf

            html_string = render_to_string("admin_panel/fiscal/print.html", context, request=request)
            pdf_bytes = generate_document_pdf(html_string, base_url=request.build_absolute_uri("/"))
            response = HttpResponse(pdf_bytes, content_type="application/pdf")
            response["Content-Disposition"] = (
                f'inline; filename="{fiscal_document.commercial_type_label}_{fiscal_document.display_number}_{copy_type}.pdf"'
            )
            return response
        except ImportError:
            messages.error(request, "El generador de PDF no esta instalado correctamente.")
        except Exception as exc:
            messages.error(request, f"Error al generar PDF: {exc}")

    return render(request, "admin_panel/fiscal/print.html", context)


@login_required
@require_POST
def reorder_to_cart(request, order_id):
    """Copy all order lines into active cart for quick re-order."""
    company = get_active_company(request)
    if not company:
        messages.error(request, "No se pudo resolver la empresa activa.")
        return redirect("order_list")
    order_qs = Order.objects.prefetch_related("items")
    if company:
        order_qs = order_qs.filter(company=company)
    order = get_object_or_404(order_qs, pk=order_id, user=request.user)
    cart, _ = _get_cart_for_company(request.user, company, create=True)
    added = 0
    for item in order.items.select_related("clamp_request"):
        if not item.product_id or not item.product:
            continue
        cart_item, created = CartItem.objects.get_or_create(
            cart=cart,
            product=item.product,
            defaults={
                "quantity": item.quantity,
                "clamp_request": item.clamp_request,
            },
        )
        if not created:
            cart_item.quantity += item.quantity
            if item.clamp_request_id and cart_item.clamp_request_id != item.clamp_request_id:
                cart_item.clamp_request = item.clamp_request
                cart_item.save(update_fields=["quantity", "clamp_request"])
            else:
                cart_item.save(update_fields=["quantity"])
        added += 1
    messages.success(request, f"Se agregaron {added} productos al carrito para recompra.")
    return redirect("cart")


@login_required
def client_portal(request):
    """B2B client portal dashboard."""
    company = get_active_company(request)
    orders_qs = Order.objects.filter(user=request.user)
    if company:
        orders_qs = orders_qs.filter(company=company)
    request_qs = OrderRequest.objects.filter(user=request.user)
    if company:
        request_qs = request_qs.filter(company=company)
    client_profile = _get_client_profile(request.user)
    favorites = (
        ClientFavoriteProduct.objects.filter(user=request.user)
        .select_related("product")
        .order_by("-created_at")[:10]
    )
    recent_payments = []
    if client_profile:
        payments_qs = ClientPayment.objects.filter(client_profile=client_profile, is_cancelled=False)
        if company:
            payments_qs = payments_qs.filter(company=company)
        recent_payments = list(payments_qs.select_related("order").order_by("-paid_at")[:8])

    context = {
        "open_request_count": request_qs.exclude(
            status__in=[
                OrderRequest.STATUS_REJECTED,
                OrderRequest.STATUS_CANCELLED,
                OrderRequest.STATUS_CONVERTED,
            ]
        ).count(),
        "total_request_count": request_qs.count(),
        "recent_requests": request_qs.prefetch_related("proposals", "generated_orders").order_by("-created_at")[:6],
        "active_orders_count": orders_qs.exclude(
            status__in=[Order.STATUS_DELIVERED, Order.STATUS_CANCELLED]
        ).count(),
        "total_orders_count": orders_qs.count(),
        "recent_orders": orders_qs.order_by("-created_at")[:8],
        "favorites": favorites,
        "client_profile": client_profile,
        "recent_payments": recent_payments,
        "current_balance": client_profile.get_current_balance(company=company) if client_profile else Decimal("0.00"),
    }
    return render(request, "orders/client_portal.html", context)


@login_required
@require_POST
def toggle_favorite(request):
    """Add/remove product favorites for quick reorder portal."""
    try:
        data = json.loads(request.body)
        product_id = int(data.get("product_id"))
        product = get_object_or_404(Product, pk=product_id)
        favorite, created = ClientFavoriteProduct.objects.get_or_create(
            user=request.user,
            product=product,
        )
        if not created:
            favorite.delete()
        count = ClientFavoriteProduct.objects.filter(user=request.user).count()
        return JsonResponse(
            {
                "success": True,
                "is_favorite": created,
                "favorites_count": count,
            }
        )
    except Exception:
        logger.exception("Error toggling favorite")
        return JsonResponse({"success": False, "error": "No se pudo actualizar favorito."}, status=400)


@login_required
def cart_count(request):
    """Get cart item count (AJAX)."""
    company = get_active_company(request)
    if not company:
        return JsonResponse({"count": 0, "favorites": 0})
    cart, _ = _get_cart_for_company(request.user, company, create=True)
    favorites_count = ClientFavoriteProduct.objects.filter(user=request.user).count()
    return JsonResponse({"count": cart.get_item_count(), "favorites": favorites_count})
