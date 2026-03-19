"""Workflow helpers for pre-order requests and commercial proposals."""

from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from core.services.pricing import (
    calculate_cart_pricing,
    calculate_final_price,
    get_base_price_for_product,
)
from orders.models import (
    Cart,
    Order,
    OrderItem,
    OrderProposal,
    OrderProposalItem,
    OrderRequest,
    OrderRequestEvent,
    OrderRequestItem,
    OrderStatusHistory,
)


MONEY_QUANT = Decimal("0.01")


def _quantize_money(value):
    return Decimal(value or 0).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def record_order_request_event(
    *,
    order_request,
    event_type,
    actor=None,
    from_status="",
    to_status="",
    message="",
    metadata=None,
):
    """Append one audit row to the request timeline."""
    if not order_request:
        return None
    return OrderRequestEvent.objects.create(
        order_request=order_request,
        event_type=event_type,
        from_status=str(from_status or "").strip(),
        to_status=str(to_status or "").strip(),
        message=str(message or "").strip(),
        metadata=metadata or {},
        actor=actor if getattr(actor, "is_authenticated", False) else None,
    )


def _expire_pending_proposals(order_request):
    order_request.proposals.filter(status=OrderProposal.STATUS_PENDING).update(
        status=OrderProposal.STATUS_EXPIRED,
        is_current=False,
        updated_at=timezone.now(),
    )


def _build_snapshot_payloads_from_request_items(order_request):
    return [
        {
            "product": item.product,
            "clamp_request": item.clamp_request,
            "product_sku": item.product_sku,
            "product_name": item.product_name,
            "quantity": item.quantity,
            "unit_price_base": item.unit_price_base,
            "discount_percentage_used": item.discount_percentage_used,
            "price_list": item.price_list,
            "price_at_snapshot": item.price_at_snapshot,
        }
        for item in order_request.items.select_related("product", "clamp_request", "price_list").order_by("line_number")
    ]


def _build_snapshot_payloads_from_proposal_items(order_proposal):
    return [
        {
            "product": item.product,
            "clamp_request": item.clamp_request,
            "product_sku": item.product_sku,
            "product_name": item.product_name,
            "quantity": item.quantity,
            "unit_price_base": item.unit_price_base,
            "discount_percentage_used": item.discount_percentage_used,
            "price_list": item.price_list,
            "price_at_snapshot": item.price_at_snapshot,
        }
        for item in order_proposal.items.select_related("product", "clamp_request", "price_list").order_by("line_number")
    ]


def _normalize_snapshot_payloads(item_payloads):
    normalized = []
    for row in item_payloads:
        quantity = int(row.get("quantity") or 0)
        if quantity <= 0:
            raise ValidationError("La cantidad de cada item debe ser mayor a cero.")

        unit_price_base = _quantize_money(row.get("unit_price_base"))
        price_at_snapshot = _quantize_money(row.get("price_at_snapshot"))
        discount_percentage = Decimal(row.get("discount_percentage_used") or 0).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
        normalized.append(
            {
                "product": row.get("product"),
                "clamp_request": row.get("clamp_request"),
                "product_sku": str(row.get("product_sku") or "").strip(),
                "product_name": str(row.get("product_name") or "").strip(),
                "quantity": quantity,
                "unit_price_base": unit_price_base,
                "discount_percentage_used": discount_percentage,
                "price_list": row.get("price_list"),
                "price_at_snapshot": price_at_snapshot,
                "subtotal": _quantize_money(price_at_snapshot * quantity),
            }
        )

    if not normalized:
        raise ValidationError("Debe existir al menos un item comercial.")
    return normalized


def _summarize_snapshot_payloads(item_payloads):
    subtotal = Decimal("0.00")
    total = Decimal("0.00")
    discount_percentage = Decimal("0.00")
    for row in item_payloads:
        subtotal += _quantize_money(row["unit_price_base"] * row["quantity"])
        total += row["subtotal"]
        discount_percentage = row["discount_percentage_used"]
    subtotal = _quantize_money(subtotal)
    total = _quantize_money(total)
    discount_amount = _quantize_money(subtotal - total)
    if discount_amount < 0:
        discount_amount = Decimal("0.00")
    return {
        "subtotal": subtotal,
        "discount_percentage": discount_percentage,
        "discount_amount": discount_amount,
        "total": total,
    }


def build_order_request_from_cart(*, cart, user, company, client_note="", origin_channel=Order.ORIGIN_CATALOG):
    """Create a request snapshot from the current cart without impacting ledger or documents."""
    if not cart or not isinstance(cart, Cart):
        raise ValidationError("Carrito invalido.")
    if not user or not getattr(user, "is_authenticated", False):
        raise ValidationError("Usuario autenticado requerido.")
    if not company:
        raise ValidationError("Empresa activa requerida.")
    if cart.company_id != company.id:
        raise ValidationError("El carrito no pertenece a la empresa activa.")

    client_profile = getattr(user, "client_profile", None)
    if not client_profile or not client_profile.can_operate_in_company(company):
        raise ValidationError("Cliente no habilitado para operar en esta empresa.")

    client_company_ref = client_profile.get_company_link(company)
    if not client_company_ref or client_company_ref.company_id != company.id:
        raise ValidationError("No se pudo validar la relacion cliente-empresa.")

    pricing = calculate_cart_pricing(cart, user=user, company=company)
    item_map = pricing["item_map"]
    price_list = pricing["price_list"]
    cart_items = list(cart.items.select_related("product", "clamp_request"))
    if not cart_items:
        raise ValidationError("El carrito esta vacio.")

    with transaction.atomic():
        previous_status = OrderRequest.STATUS_DRAFT
        order_request = OrderRequest.objects.create(
            user=user,
            company=company,
            client_company_ref=client_company_ref,
            status=OrderRequest.STATUS_SUBMITTED,
            origin_channel=origin_channel,
            client_note=(client_note or "").strip(),
            requested_subtotal=_quantize_money(pricing["subtotal"]),
            requested_discount_percentage=Decimal(pricing["discount_percentage"] or 0).quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP,
            ),
            requested_discount_amount=_quantize_money(pricing["discount_amount"]),
            requested_total=_quantize_money(pricing["total"]),
            submitted_at=timezone.now(),
        )

        discount_percentage = Decimal(pricing["discount_percentage"] or 0).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
        clamp_request_ids = []
        for line_number, cart_item in enumerate(cart_items, start=1):
            base_price = get_base_price_for_product(
                cart_item.product,
                price_list=price_list,
                item_map=item_map,
            )
            final_unit_price = _quantize_money(
                calculate_final_price(base_price, discount_percentage)
            )
            OrderRequestItem.objects.create(
                order_request=order_request,
                line_number=line_number,
                product=cart_item.product,
                clamp_request=cart_item.clamp_request,
                product_sku=cart_item.product.sku,
                product_name=cart_item.product.name,
                quantity=cart_item.quantity,
                unit_price_base=_quantize_money(base_price),
                discount_percentage_used=discount_percentage,
                price_list=price_list,
                price_at_snapshot=final_unit_price,
                subtotal=_quantize_money(final_unit_price * cart_item.quantity),
            )
            if cart_item.clamp_request_id:
                clamp_request_ids.append(cart_item.clamp_request_id)

        if clamp_request_ids:
            ClampMeasureRequest = cart_item._meta.get_field("clamp_request").related_model
            ClampMeasureRequest.objects.filter(id__in=clamp_request_ids).update(
                ordered_at=timezone.now(),
                updated_at=timezone.now(),
            )
        record_order_request_event(
            order_request=order_request,
            event_type=OrderRequestEvent.EVENT_CREATED,
            actor=user,
            from_status=previous_status,
            to_status=order_request.status,
            message="Solicitud enviada desde el catalogo.",
            metadata={
                "origin_channel": order_request.origin_channel,
                "requested_total": str(order_request.requested_total),
                "items_count": len(cart_items),
            },
        )

    return order_request


def create_order_proposal(
    *,
    order_request,
    created_by,
    item_payloads=None,
    message_to_client="",
    internal_note="",
    expires_at=None,
):
    """Create one commercial proposal version for a request."""
    if not order_request:
        raise ValidationError("Solicitud invalida.")
    if order_request.status in {
        OrderRequest.STATUS_REJECTED,
        OrderRequest.STATUS_CANCELLED,
        OrderRequest.STATUS_CONVERTED,
    }:
        raise ValidationError("La solicitud ya no admite propuestas.")

    if item_payloads is None:
        item_payloads = _build_snapshot_payloads_from_request_items(order_request)
    normalized_items = _normalize_snapshot_payloads(item_payloads)
    totals = _summarize_snapshot_payloads(normalized_items)

    with transaction.atomic():
        previous_status = order_request.status
        current_version = (
            order_request.proposals.select_for_update().aggregate(max_version=Max("version_number"))["max_version"]
            or 0
        )
        _expire_pending_proposals(order_request)
        order_request.proposals.filter(is_current=True).update(is_current=False)
        proposal = OrderProposal.objects.create(
            order_request=order_request,
            version_number=current_version + 1,
            status=OrderProposal.STATUS_PENDING,
            is_current=True,
            message_to_client=(message_to_client or "").strip(),
            internal_note=(internal_note or "").strip(),
            proposed_subtotal=totals["subtotal"],
            proposed_discount_percentage=totals["discount_percentage"],
            proposed_discount_amount=totals["discount_amount"],
            proposed_total=totals["total"],
            created_by=created_by if getattr(created_by, "is_authenticated", False) else None,
            expires_at=expires_at,
        )
        OrderProposalItem.objects.bulk_create(
            [
                OrderProposalItem(
                    order_proposal=proposal,
                    line_number=index,
                    product=row["product"],
                    clamp_request=row["clamp_request"],
                    product_sku=row["product_sku"],
                    product_name=row["product_name"],
                    quantity=row["quantity"],
                    unit_price_base=row["unit_price_base"],
                    discount_percentage_used=row["discount_percentage_used"],
                    price_list=row["price_list"],
                    price_at_snapshot=row["price_at_snapshot"],
                    subtotal=row["subtotal"],
                )
                for index, row in enumerate(normalized_items, start=1)
            ]
        )
        order_request.status = OrderRequest.STATUS_WAITING_CLIENT
        order_request.reviewed_at = timezone.now()
        order_request.save(update_fields=["status", "reviewed_at", "updated_at"])
        if previous_status == OrderRequest.STATUS_SUBMITTED:
            record_order_request_event(
                order_request=order_request,
                event_type=OrderRequestEvent.EVENT_REVIEW_STARTED,
                actor=created_by,
                from_status=previous_status,
                to_status=OrderRequest.STATUS_IN_REVIEW,
                message="El equipo comercial tomo la solicitud en revision.",
            )
        record_order_request_event(
            order_request=order_request,
            event_type=OrderRequestEvent.EVENT_PROPOSAL_SENT,
            actor=created_by,
            from_status=previous_status,
            to_status=order_request.status,
            message=(message_to_client or "").strip() or "Se envio una propuesta comercial al cliente.",
            metadata={
                "proposal_id": proposal.pk,
                "version_number": proposal.version_number,
                "proposed_total": str(proposal.proposed_total),
            },
        )
    return proposal


def confirm_order_request(*, order_request, actor=None):
    """Confirm a request without a counterproposal."""
    if not order_request:
        raise ValidationError("Solicitud invalida.")
    if order_request.status in {OrderRequest.STATUS_REJECTED, OrderRequest.STATUS_CANCELLED}:
        raise ValidationError("La solicitud no puede confirmarse en su estado actual.")

    with transaction.atomic():
        previous_status = order_request.status
        _expire_pending_proposals(order_request)
        order_request.status = OrderRequest.STATUS_CONFIRMED
        order_request.reviewed_at = order_request.reviewed_at or timezone.now()
        order_request.decided_at = timezone.now()
        order_request.save(update_fields=["status", "reviewed_at", "decided_at", "updated_at"])
        if previous_status == OrderRequest.STATUS_SUBMITTED:
            record_order_request_event(
                order_request=order_request,
                event_type=OrderRequestEvent.EVENT_REVIEW_STARTED,
                actor=actor,
                from_status=previous_status,
                to_status=OrderRequest.STATUS_IN_REVIEW,
                message="El equipo comercial tomo la solicitud en revision.",
            )
        record_order_request_event(
            order_request=order_request,
            event_type=OrderRequestEvent.EVENT_CONFIRMED,
            actor=actor,
            from_status=previous_status,
            to_status=order_request.status,
            message="Solicitud confirmada sin cambios.",
        )
    return order_request


def reject_order_request(*, order_request, reason="", actor=None):
    """Reject a request with an optional visible reason."""
    if not order_request:
        raise ValidationError("Solicitud invalida.")
    if order_request.status == OrderRequest.STATUS_CONVERTED:
        raise ValidationError("La solicitud ya fue convertida y no puede rechazarse.")

    with transaction.atomic():
        previous_status = order_request.status
        _expire_pending_proposals(order_request)
        order_request.status = OrderRequest.STATUS_REJECTED
        order_request.rejection_reason = (reason or "").strip()
        order_request.reviewed_at = order_request.reviewed_at or timezone.now()
        order_request.decided_at = timezone.now()
        order_request.save(
            update_fields=["status", "rejection_reason", "reviewed_at", "decided_at", "updated_at"]
        )
        if previous_status == OrderRequest.STATUS_SUBMITTED:
            record_order_request_event(
                order_request=order_request,
                event_type=OrderRequestEvent.EVENT_REVIEW_STARTED,
                actor=actor,
                from_status=previous_status,
                to_status=OrderRequest.STATUS_IN_REVIEW,
                message="El equipo comercial tomo la solicitud en revision.",
            )
        record_order_request_event(
            order_request=order_request,
            event_type=OrderRequestEvent.EVENT_REJECTED,
            actor=actor,
            from_status=previous_status,
            to_status=order_request.status,
            message=(reason or "").strip() or "La solicitud fue rechazada por el equipo comercial.",
        )
    return order_request


def accept_order_proposal(*, order_proposal, actor=None):
    """Accept one proposal and mark the parent request as commercially confirmed."""
    if not order_proposal:
        raise ValidationError("Propuesta invalida.")
    if order_proposal.status != OrderProposal.STATUS_PENDING:
        raise ValidationError("Solo se pueden aceptar propuestas pendientes.")

    with transaction.atomic():
        order_proposal = OrderProposal.objects.select_for_update().get(pk=order_proposal.pk)
        if order_proposal.status != OrderProposal.STATUS_PENDING:
            raise ValidationError("Solo se pueden aceptar propuestas pendientes.")
        order_proposal.status = OrderProposal.STATUS_ACCEPTED
        order_proposal.responded_at = timezone.now()
        order_proposal.responded_by = actor if getattr(actor, "is_authenticated", False) else None
        order_proposal.is_current = True
        order_proposal.save(
            update_fields=["status", "responded_at", "responded_by", "is_current", "updated_at"]
        )

        order_request = order_proposal.order_request
        previous_status = order_request.status
        order_request.status = OrderRequest.STATUS_CONFIRMED
        order_request.decided_at = timezone.now()
        order_request.reviewed_at = order_request.reviewed_at or timezone.now()
        order_request.save(update_fields=["status", "decided_at", "reviewed_at", "updated_at"])
        record_order_request_event(
            order_request=order_request,
            event_type=OrderRequestEvent.EVENT_PROPOSAL_ACCEPTED,
            actor=actor,
            from_status=previous_status,
            to_status=order_request.status,
            message="El cliente acepto la propuesta comercial.",
            metadata={
                "proposal_id": order_proposal.pk,
                "version_number": order_proposal.version_number,
            },
        )
    return order_proposal


def reject_order_proposal(*, order_proposal, actor=None):
    """Reject one pending proposal and keep the request pending resolution."""
    if not order_proposal:
        raise ValidationError("Propuesta invalida.")
    if order_proposal.status != OrderProposal.STATUS_PENDING:
        raise ValidationError("Solo se pueden rechazar propuestas pendientes.")

    with transaction.atomic():
        order_proposal = OrderProposal.objects.select_for_update().get(pk=order_proposal.pk)
        if order_proposal.status != OrderProposal.STATUS_PENDING:
            raise ValidationError("Solo se pueden rechazar propuestas pendientes.")
        order_proposal.status = OrderProposal.STATUS_REJECTED
        order_proposal.responded_at = timezone.now()
        order_proposal.responded_by = actor if getattr(actor, "is_authenticated", False) else None
        order_proposal.save(update_fields=["status", "responded_at", "responded_by", "updated_at"])

        order_request = order_proposal.order_request
        previous_status = order_request.status
        order_request.status = OrderRequest.STATUS_IN_REVIEW
        order_request.reviewed_at = timezone.now()
        order_request.save(update_fields=["status", "reviewed_at", "updated_at"])
        record_order_request_event(
            order_request=order_request,
            event_type=OrderRequestEvent.EVENT_PROPOSAL_REJECTED,
            actor=actor,
            from_status=previous_status,
            to_status=order_request.status,
            message="El cliente rechazo la propuesta comercial.",
            metadata={
                "proposal_id": order_proposal.pk,
                "version_number": order_proposal.version_number,
            },
        )
    return order_proposal


def convert_request_to_order(*, order_request, actor=None, source_proposal=None, status=Order.STATUS_DRAFT):
    """Create one operational order from the final approved request snapshot."""
    if not order_request:
        raise ValidationError("Solicitud invalida.")
    if order_request.status != OrderRequest.STATUS_CONFIRMED:
        raise ValidationError("Solo pueden convertirse solicitudes confirmadas.")
    if order_request.converted_order:
        return order_request.converted_order, False

    if source_proposal is None:
        source_proposal = (
            order_request.proposals.filter(status=OrderProposal.STATUS_ACCEPTED)
            .order_by("-version_number", "-id")
            .first()
        )
    if source_proposal and source_proposal.order_request_id != order_request.id:
        raise ValidationError("La propuesta no pertenece a la solicitud indicada.")

    if source_proposal:
        item_payloads = _build_snapshot_payloads_from_proposal_items(source_proposal)
        totals = {
            "subtotal": source_proposal.proposed_subtotal,
            "discount_percentage": source_proposal.proposed_discount_percentage,
            "discount_amount": source_proposal.proposed_discount_amount,
            "total": source_proposal.proposed_total,
        }
    else:
        item_payloads = _build_snapshot_payloads_from_request_items(order_request)
        totals = {
            "subtotal": order_request.requested_subtotal,
            "discount_percentage": order_request.requested_discount_percentage,
            "discount_amount": order_request.requested_discount_amount,
            "total": order_request.requested_total,
        }

    normalized_items = _normalize_snapshot_payloads(item_payloads)

    with transaction.atomic():
        order = Order.objects.create(
            user=order_request.user,
            company=order_request.company,
            origin_channel=order_request.origin_channel,
            source_request=order_request,
            source_proposal=source_proposal,
            status=status,
            priority=Order.PRIORITY_NORMAL,
            notes=order_request.client_note or "",
            admin_notes=order_request.admin_note or "",
            subtotal=_quantize_money(totals["subtotal"]),
            discount_percentage=Decimal(totals["discount_percentage"] or 0).quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP,
            ),
            discount_amount=_quantize_money(totals["discount_amount"]),
            total=_quantize_money(totals["total"]),
            client_company=order_request.client_company_ref.client_profile.company_name
            if order_request.client_company_ref_id
            else "",
            client_cuit=order_request.client_company_ref.client_profile.cuit_dni
            if order_request.client_company_ref_id
            else "",
            client_address=order_request.client_company_ref.client_profile.address
            if order_request.client_company_ref_id
            else "",
            client_phone=order_request.client_company_ref.client_profile.phone
            if order_request.client_company_ref_id
            else "",
            client_company_ref=order_request.client_company_ref,
            saas_document_type="",
            saas_document_number="",
            saas_document_cae="",
            follow_up_note="",
        )
        OrderItem.objects.bulk_create(
            [
                OrderItem(
                    order=order,
                    product=row["product"],
                    clamp_request=row["clamp_request"],
                    product_sku=row["product_sku"],
                    product_name=row["product_name"],
                    quantity=row["quantity"],
                    unit_price_base=row["unit_price_base"],
                    discount_percentage_used=row["discount_percentage_used"],
                    price_list=row["price_list"],
                    price_at_purchase=row["price_at_snapshot"],
                    subtotal=row["subtotal"],
                )
                for row in normalized_items
            ]
        )
        OrderStatusHistory.objects.create(
            order=order,
            from_status="",
            to_status=order.status,
            changed_by=actor if getattr(actor, "is_authenticated", False) else None,
            note=f"Pedido generado desde solicitud #{order_request.pk}",
        )
        order_request.status = OrderRequest.STATUS_CONVERTED
        order_request.converted_at = timezone.now()
        order_request.save(update_fields=["status", "converted_at", "updated_at"])
        record_order_request_event(
            order_request=order_request,
            event_type=OrderRequestEvent.EVENT_CONVERTED,
            actor=actor,
            from_status=OrderRequest.STATUS_CONFIRMED,
            to_status=order_request.status,
            message=f"Se genero el pedido operativo #{order.pk}.",
            metadata={
                "order_id": order.pk,
                "order_status": order.status,
                "source_proposal_id": source_proposal.pk if source_proposal else None,
            },
        )

    return order, True
