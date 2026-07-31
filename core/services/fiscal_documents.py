"""Fiscal document local flow helpers (without ARCA emission)."""

from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from accounts.services.account_movement_service import (
    sync_fiscal_document_account_movement,
)
from core.models import (
    FISCAL_ACTIVE_OPERATION_STATUSES,
    FISCAL_ISSUE_MODE_ARCA_WSFE,
    FISCAL_ISSUE_MODE_EXTERNAL_SAAS,
    FISCAL_ISSUE_MODE_MANUAL,
    FISCAL_DOC_TYPE_FA,
    FISCAL_DOC_TYPE_FB,
    FISCAL_DOC_TYPE_NCA,
    FISCAL_DOC_TYPE_NCB,
    FISCAL_STATUS_AUTHORIZED,
    FISCAL_STATUS_DRAFT,
    FISCAL_STATUS_EXTERNAL_RECORDED,
    FISCAL_STATUS_PENDING_RETRY,
    FISCAL_STATUS_READY_TO_ISSUE,
    FISCAL_STATUS_REJECTED,
    FISCAL_STATUS_SUBMITTING,
    FISCAL_STATUS_VOIDED,
    FiscalDocument,
    FiscalDocumentItem,
    FiscalPointOfSale,
)
from core.services.fiscal import (
    is_invoice_ready,
    resolve_payment_due_date,
    validate_credit_note_relationship,
)
from core.services.arca_parameters import (
    get_vat_rate_parameter,
    validate_client_receiver_iva,
)
from core.services.fiscal_integrity import (
    FiscalPayloadConflict,
    fiscal_payload_hash,
    quantize_money,
    quantize_quantity,
    quantize_rate,
    totals_from_lines,
    validate_line_and_totals,
)
from core.services.sales_documents import (
    apply_sales_document_type_to_fiscal_document,
    resolve_sales_document_type_for_fiscal_doc,
)


ALLOWED_DOC_TYPES_FOR_PHASE3 = {code for code, _label in FiscalDocument.DOC_TYPE_CHOICES}
ALLOWED_ARCA_DOC_TYPES = {
    FISCAL_DOC_TYPE_FA,
    FISCAL_DOC_TYPE_FB,
    FISCAL_DOC_TYPE_NCA,
    FISCAL_DOC_TYPE_NCB,
}
LOCAL_ISSUE_MODES_FOR_PHASE3 = {
    FISCAL_ISSUE_MODE_ARCA_WSFE,
    FISCAL_ISSUE_MODE_MANUAL,
}


def build_local_source_key(*, order_id, company_id, point_of_sale_id, doc_type):
    return f"local:order:{order_id}:company:{company_id}:pos:{point_of_sale_id}:doc:{doc_type}"


def build_external_source_key(
    *,
    order_id,
    company_id,
    point_of_sale_id,
    doc_type,
    external_system,
    external_id,
    external_number,
):
    external_ref = str(external_id or external_number or "").strip()
    return (
        "external:"
        f"order:{order_id}:company:{company_id}:pos:{point_of_sale_id}:doc:{doc_type}:"
        f"system:{external_system}:ref:{external_ref}"
    )


def _validate_order_and_point(*, order, company, point_of_sale, doc_type):
    if not order:
        raise ValidationError("Pedido invalido.")
    if not company:
        raise ValidationError("Empresa invalida.")
    if order.company_id != company.id:
        raise ValidationError("El pedido no pertenece a la empresa activa.")
    if not point_of_sale:
        raise ValidationError("Debes seleccionar un punto de venta.")
    if point_of_sale.company_id != company.id:
        raise ValidationError("El punto de venta no pertenece a la empresa activa.")
    if not point_of_sale.is_active:
        raise ValidationError("El punto de venta seleccionado esta inactivo.")
    if doc_type not in ALLOWED_DOC_TYPES_FOR_PHASE3:
        raise ValidationError("Tipo de comprobante no permitido en esta fase.")


def _get_discount_percentage(item, order):
    item_discount = getattr(item, "discount_percentage_used", None)
    if item_discount is None:
        return Decimal(order.discount_percentage or 0)
    return Decimal(item_discount or 0)


def _to_decimal(value, default="0.00"):
    try:
        return Decimal(str(value if value is not None else default))
    except Exception:
        return Decimal(default)


def _should_apply_item_tax(issue_mode):
    if not bool(getattr(settings, "FISCAL_AUTO_ITEM_TAX_ENABLED", True)):
        return False
    if issue_mode == FISCAL_ISSUE_MODE_ARCA_WSFE:
        return True
    return bool(getattr(settings, "FISCAL_APPLY_TAX_TO_MANUAL_DOCS", False))


def _apply_item_tax_breakdown(*, base_amount, iva_rate):
    iva_rate = quantize_rate(iva_rate)
    amount = quantize_money(base_amount)
    if iva_rate <= 0:
        return amount, Decimal("0.00"), amount

    mode = str(
        getattr(settings, "FISCAL_ITEM_TAX_CALCULATION_MODE", "net") or "net"
    ).strip().lower()
    if mode == "net":
        net_amount = amount
        iva_amount = (net_amount * iva_rate / Decimal("100")).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
        total_amount = quantize_money(net_amount + iva_amount)
        return net_amount, iva_amount, total_amount

    # Legacy compatibility mode: amount already includes IVA and gets split out.
    divisor = Decimal("1.00") + (iva_rate / Decimal("100"))
    if divisor <= 0:
        return amount, Decimal("0.00"), amount
    net_amount = (amount / divisor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    iva_amount = quantize_money(amount - net_amount)
    total_amount = amount
    return net_amount, iva_amount, total_amount


def _build_order_items_payload(order, *, doc_type, issue_mode):
    payload = []
    line_number = 1
    apply_tax = _should_apply_item_tax(issue_mode)
    for item in order.items.select_related("product").all():
        quantity = quantize_quantity(item.quantity or 0)
        unit_price_base = Decimal(getattr(item, "unit_price_base", None) or item.price_at_purchase or 0)
        line_gross = quantize_money(unit_price_base * quantity)
        line_amount = quantize_money(getattr(item, "subtotal", None) or 0)
        discount_amount = quantize_money(line_gross - line_amount)
        if discount_amount < 0:
            discount_amount = Decimal("0.00")

        iva_rate = Decimal("0.00")
        if apply_tax:
            snapshot_rate = getattr(item, "iva_rate_snapshot", None)
            product_rate = getattr(getattr(item, "product", None), "iva_rate", None)
            raw_rate = snapshot_rate if snapshot_rate is not None else product_rate
            if raw_rate is None:
                raise ValidationError(
                    f'La linea "{item.product_sku or item.product_name}" no tiene alicuota de IVA congelada.'
                )
            iva_rate = quantize_rate(raw_rate)
            if iva_rate <= 0:
                raise ValidationError(
                    f'La linea "{item.product_sku or item.product_name}" requiere un tratamiento exento/no gravado explicito; no se admite IVA cero implicito.'
                )
            vat_parameter = get_vat_rate_parameter(iva_rate)
            arca_iva_id = vat_parameter.arca_id
        else:
            arca_iva_id = None

        net_amount, iva_amount, total_amount = _apply_item_tax_breakdown(
            base_amount=line_amount,
            iva_rate=iva_rate,
        )
        if not apply_tax:
            net_amount = line_amount
            iva_amount = Decimal("0.00")
            total_amount = line_amount

        payload.append(
            {
                "line_number": line_number,
                "product_id": item.product_id,
                "sku": item.product_sku or "",
                "description": item.product_name or "",
                "quantity": quantity,
                "unit_price_net": quantize_money(unit_price_base),
                "discount_percentage": quantize_rate(_get_discount_percentage(item, order)),
                "discount_amount": discount_amount,
                "net_amount": net_amount,
                "iva_rate": iva_rate,
                "arca_iva_id": arca_iva_id,
                "tax_treatment": "taxed" if apply_tax else "non_taxed",
                "iva_amount": iva_amount,
                "total_amount": total_amount,
            }
        )
        line_number += 1
    return payload


def _compute_totals_from_payload(payload):
    totals = totals_from_lines(payload)
    validate_line_and_totals(payload, totals)
    return totals


def _build_fiscal_snapshot_payload(
    *,
    order,
    company,
    point_of_sale,
    doc_type,
    issue_mode,
    sales_document_type=None,
    actor=None,
    client_company_ref=None,
    receiver_iva_condition=None,
    items_payload=None,
    totals=None,
    external_system="",
    external_id="",
    external_number="",
):
    client_profile = None
    if client_company_ref is not None:
        client_profile = getattr(client_company_ref, "client_profile", None)
    if not client_profile and getattr(order, "user_id", None):
        client_profile = getattr(order.user, "client_profile", None)

    company_tax_label = ""
    if company and getattr(company, "tax_condition", ""):
        try:
            company_tax_label = company.get_tax_condition_display()
        except Exception:
            company_tax_label = str(getattr(company, "tax_condition", "") or "")

    client_doc_label = ""
    client_tax_label = ""
    if client_profile and getattr(client_profile, "document_type", ""):
        try:
            client_doc_label = client_profile.get_document_type_display()
        except Exception:
            client_doc_label = str(getattr(client_profile, "document_type", "") or "")
    if client_profile and getattr(client_profile, "iva_condition", ""):
        try:
            client_tax_label = client_profile.get_iva_condition_display()
        except Exception:
            client_tax_label = str(getattr(client_profile, "iva_condition", "") or "")

    snapshot_items = []
    for row in items_payload or []:
        snapshot_items.append(
            {
                key: (format(value, "f") if isinstance(value, Decimal) else value)
                for key, value in row.items()
            }
        )
    snapshot_totals = {
        key: (format(value, "f") if isinstance(value, Decimal) else value)
        for key, value in (totals or {}).items()
    }

    return {
        "version": 2,
        "emitter": {
            "company_id": getattr(company, "id", None),
            "name": str(getattr(company, "name", "") or ""),
            "legal_name": str(getattr(company, "legal_name", "") or ""),
            "cuit": str(getattr(company, "cuit", "") or ""),
            "tax_condition": str(getattr(company, "tax_condition", "") or ""),
            "tax_condition_label": str(company_tax_label or ""),
            "fiscal_address": str(getattr(company, "fiscal_address", "") or ""),
            "fiscal_city": str(getattr(company, "fiscal_city", "") or ""),
            "fiscal_province": str(getattr(company, "fiscal_province", "") or ""),
            "postal_code": str(getattr(company, "postal_code", "") or ""),
            "email": str(getattr(company, "email", "") or ""),
            "activity_start_date": (
                company.activity_start_date.isoformat()
                if getattr(company, "activity_start_date", None)
                else ""
            ),
            "point_of_sale": str(getattr(point_of_sale, "number", "") or ""),
            "environment": str(getattr(point_of_sale, "environment", "") or ""),
        },
        "client": {
            "client_company_ref_id": getattr(client_company_ref, "id", None),
            "client_profile_id": getattr(client_profile, "id", None),
            "name": str(
                (getattr(client_profile, "company_name", "") or "")
                or (getattr(order, "client_company", "") or "")
            ),
            "document_type": str(getattr(client_profile, "document_type", "") or ""),
            "document_type_label": str(client_doc_label or ""),
            "document_number": str(
                (getattr(client_profile, "document_number", "") or "")
                or (getattr(client_profile, "cuit_dni", "") or "")
                or (getattr(order, "client_cuit", "") or "")
            ),
            "tax_condition": str(getattr(client_profile, "iva_condition", "") or ""),
            "tax_condition_label": str(client_tax_label or ""),
            "iva_condition_arca_id": getattr(
                receiver_iva_condition,
                "arca_id",
                None,
            ),
            "iva_condition_arca_description": str(
                getattr(receiver_iva_condition, "description", "") or ""
            ),
            "iva_condition_source": str(
                getattr(client_profile, "iva_condition_source", "") or ""
            ),
            "iva_condition_validated_at": (
                client_profile.iva_condition_validated_at.isoformat()
                if client_profile
                and getattr(client_profile, "iva_condition_validated_at", None)
                else ""
            ),
            "fiscal_address": str(
                (getattr(client_profile, "fiscal_address", "") or "")
                or (getattr(client_profile, "address", "") or "")
                or (getattr(order, "client_address", "") or "")
            ),
            "fiscal_city": str(
                (getattr(client_profile, "fiscal_city", "") or "")
                or (getattr(client_profile, "province", "") or "")
            ),
            "fiscal_province": str(getattr(client_profile, "fiscal_province", "") or ""),
            "postal_code": str(getattr(client_profile, "postal_code", "") or ""),
            "phone": str(
                (getattr(client_profile, "phone", "") or "")
                or (getattr(order, "client_phone", "") or "")
            ),
        },
        "operation": {
            "order_id": getattr(order, "id", None),
            "order_status": str(getattr(order, "status", "") or ""),
            "origin_channel": str(getattr(order, "origin_channel", "") or ""),
            "billing_mode": str(getattr(order, "billing_mode", "") or ""),
            "source_request_id": getattr(order, "source_request_id", None),
            "source_proposal_id": getattr(order, "source_proposal_id", None),
            "operator_id": getattr(order, "assigned_to_id", None),
            "operator_name": str(
                (
                    order.assigned_to.get_full_name()
                    or order.assigned_to.username
                )
                if getattr(order, "assigned_to", None)
                else ""
            ),
            "notes": str(getattr(order, "notes", "") or ""),
            "admin_notes": str(getattr(order, "admin_notes", "") or ""),
            "subtotal": str(quantize_money(getattr(order, "subtotal", 0) or 0)),
            "discount_amount": str(
                quantize_money(getattr(order, "discount_amount", 0) or 0)
            ),
            "discount_percentage": str(
                quantize_rate(getattr(order, "discount_percentage", 0) or 0)
            ),
            "total": str(quantize_money(getattr(order, "total", 0) or 0)),
        },
        "items": snapshot_items,
        "totals": snapshot_totals,
        "generation": {
            "doc_type": str(doc_type or ""),
            "issue_mode": str(issue_mode or ""),
            "sales_document_type_id": getattr(sales_document_type, "id", None),
            "sales_document_type_name": str(getattr(sales_document_type, "name", "") or ""),
            "external_system": str(external_system or ""),
            "external_id": str(external_id or ""),
            "external_number": str(external_number or ""),
        },
    }


def _ensure_document_snapshot(*, document, snapshot_payload):
    if not document:
        return False
    current_payload = document.request_payload if isinstance(document.request_payload, dict) else {}
    if isinstance(current_payload.get("snapshot"), dict):
        return False
    current_payload["snapshot"] = snapshot_payload
    document.request_payload = current_payload
    document.save(update_fields=["request_payload", "updated_at"])
    return True


def _create_document_items(document, payload):
    items = []
    for row in payload:
        items.append(
            FiscalDocumentItem(
                fiscal_document=document,
                line_number=row["line_number"],
                product_id=row["product_id"],
                sku=row["sku"],
                description=row["description"],
                quantity=row["quantity"],
                unit_price_net=row["unit_price_net"],
                discount_percentage=row["discount_percentage"],
                discount_amount=row["discount_amount"],
                net_amount=row["net_amount"],
                iva_rate=row["iva_rate"],
                arca_iva_id=row.get("arca_iva_id"),
                tax_treatment=row.get("tax_treatment") or "taxed",
                iva_amount=row["iva_amount"],
                total_amount=row["total_amount"],
            )
        )
    if items:
        FiscalDocumentItem.objects.bulk_create(items)


def create_local_fiscal_document_from_order(
    *,
    order,
    company,
    doc_type,
    point_of_sale,
    issue_mode,
    sales_document_type=None,
    actor=None,
    require_invoice_ready=True,
):
    """Prepare one immutable, idempotent fiscal operation without contacting ARCA."""
    _validate_order_and_point(
        order=order,
        company=company,
        point_of_sale=point_of_sale,
        doc_type=doc_type,
    )
    if issue_mode not in LOCAL_ISSUE_MODES_FOR_PHASE3:
        raise ValidationError("Modo de comprobante invalido para creacion local.")
    if issue_mode == FISCAL_ISSUE_MODE_ARCA_WSFE and doc_type not in ALLOWED_ARCA_DOC_TYPES:
        raise ValidationError(
            "La emision electronica admite solamente Factura A/B y Nota de Credito A/B."
        )
    if (
        issue_mode == FISCAL_ISSUE_MODE_ARCA_WSFE
        and point_of_sale.environment != FiscalPointOfSale.ENV_HOMOLOGATION
    ):
        raise ValidationError(
            "Durante esta etapa las operaciones ARCA sólo pueden prepararse para homologacion."
        )

    invoice_ready, invoice_errors = is_invoice_ready(order)
    if require_invoice_ready and not invoice_ready:
        raise ValidationError("No se puede crear comprobante fiscal: " + " | ".join(invoice_errors))

    source_key = build_local_source_key(
        order_id=order.id,
        company_id=company.id,
        point_of_sale_id=point_of_sale.id,
        doc_type=doc_type,
    )
    payload = _build_order_items_payload(
        order,
        doc_type=doc_type,
        issue_mode=issue_mode,
    )
    totals = _compute_totals_from_payload(payload)
    client_company_ref = getattr(order, "client_company_ref", None)
    if not client_company_ref:
        raise ValidationError("El pedido no tiene cliente empresa asignado.")
    client_profile = client_company_ref.client_profile
    receiver_iva_condition = None
    if issue_mode == FISCAL_ISSUE_MODE_ARCA_WSFE:
        receiver_iva_condition = validate_client_receiver_iva(
            client_profile=client_profile,
            doc_type=doc_type,
        )
    resolved_type = sales_document_type or resolve_sales_document_type_for_fiscal_doc(
        company=company,
        doc_type=doc_type,
        origin_channel=getattr(order, "origin_channel", ""),
    )
    snapshot_payload = _build_fiscal_snapshot_payload(
        order=order,
        company=company,
        point_of_sale=point_of_sale,
        doc_type=doc_type,
        issue_mode=issue_mode,
        sales_document_type=resolved_type,
        actor=actor,
        client_company_ref=client_company_ref,
        receiver_iva_condition=receiver_iva_condition,
        items_payload=payload,
        totals=totals,
    )
    snapshot_hash = fiscal_payload_hash(snapshot_payload)

    def existing_or_conflict():
        existing_document = (
            FiscalDocument.objects.filter(source_key=source_key).first()
            or FiscalDocument.objects.filter(
                company=company,
                order=order,
                issue_mode=FISCAL_ISSUE_MODE_ARCA_WSFE,
                status__in=FISCAL_ACTIVE_OPERATION_STATUSES,
            )
            .first()
        )
        if not existing_document:
            return None
        if existing_document.snapshot_hash == snapshot_hash:
            return existing_document
        raise FiscalPayloadConflict(
            "La operacion fiscal idempotente ya existe con un payload diferente."
        )

    existing = existing_or_conflict()
    if existing:
        return existing, False

    try:
        with transaction.atomic():
            duplicate = (
                FiscalDocument.objects.select_for_update()
                .filter(
                    company=company,
                    order=order,
                    issue_mode=FISCAL_ISSUE_MODE_ARCA_WSFE,
                    status__in=FISCAL_ACTIVE_OPERATION_STATUSES,
                )
                .first()
            )
            if duplicate:
                if duplicate.snapshot_hash == snapshot_hash:
                    return duplicate, False
                raise FiscalPayloadConflict(
                    "El pedido ya tiene otra operacion fiscal activa con distinto payload."
                )

            prepared_at = timezone.now()
            document = FiscalDocument.objects.create(
                source_key=source_key,
                company=company,
                client_company_ref=client_company_ref,
                client_profile=client_profile,
                order=order,
                point_of_sale=point_of_sale,
                doc_type=doc_type,
                issue_mode=issue_mode,
                status=FISCAL_STATUS_DRAFT,
                payment_due_date=resolve_payment_due_date(order=order),
                sales_document_type=resolved_type,
                subtotal_net=totals["subtotal_net"],
                discount_total=totals["discount_total"],
                tax_total=totals["tax_total"],
                total=totals["total"],
                currency="ARS",
                exchange_rate=Decimal("1.000000"),
                fiscal_snapshot=snapshot_payload,
                snapshot_schema_version=2,
                snapshot_hash=snapshot_hash,
                prepared_at=prepared_at,
                issuer_cuit_snapshot="".join(
                    character
                    for character in str(getattr(company, "cuit", "") or "")
                    if character.isdigit()
                ),
                environment_snapshot=point_of_sale.environment,
                point_of_sale_number_snapshot=point_of_sale.number,
                receiver_iva_condition_id_snapshot=getattr(
                    receiver_iva_condition,
                    "arca_id",
                    None,
                ),
                receiver_iva_condition_label_snapshot=str(
                    getattr(receiver_iva_condition, "description", "") or ""
                ),
                receiver_iva_condition_source_snapshot=str(
                    getattr(client_profile, "iva_condition_source", "") or ""
                ),
                receiver_iva_condition_validated_at_snapshot=getattr(
                    client_profile,
                    "iva_condition_validated_at",
                    None,
                ),
                request_payload={},
            )
            _create_document_items(document, payload)
            document.transition_to(FISCAL_STATUS_READY_TO_ISSUE)
            return document, True
    except IntegrityError:
        winner = existing_or_conflict()
        if winner:
            return winner, False
        raise


def register_external_fiscal_document_for_order(
    *,
    order,
    company,
    doc_type,
    point_of_sale,
    external_system,
    external_id,
    external_number,
    sales_document_type=None,
    actor=None,
):
    """Register external/SaaS fiscal document without local emission."""
    _validate_order_and_point(
        order=order,
        company=company,
        point_of_sale=point_of_sale,
        doc_type=doc_type,
    )
    external_system = str(external_system or "").strip()
    external_id = str(external_id or "").strip()
    external_number = str(external_number or "").strip()
    if not external_system:
        raise ValidationError("El sistema externo es obligatorio para registro externo.")
    if not (external_id or external_number):
        raise ValidationError("Debes informar ID externo o numero externo.")

    source_key = build_external_source_key(
        order_id=order.id,
        company_id=company.id,
        point_of_sale_id=point_of_sale.id,
        doc_type=doc_type,
        external_system=external_system,
        external_id=external_id,
        external_number=external_number,
    )
    payload = _build_order_items_payload(
        order,
        doc_type=doc_type,
        issue_mode=FISCAL_ISSUE_MODE_EXTERNAL_SAAS,
    )
    totals = _compute_totals_from_payload(payload)
    client_company_ref = getattr(order, "client_company_ref", None)
    if not client_company_ref:
        raise ValidationError("El pedido no tiene cliente empresa asignado.")
    snapshot_payload = _build_fiscal_snapshot_payload(
        order=order,
        company=company,
        point_of_sale=point_of_sale,
        doc_type=doc_type,
        issue_mode=FISCAL_ISSUE_MODE_EXTERNAL_SAAS,
        sales_document_type=sales_document_type,
        actor=actor,
        client_company_ref=client_company_ref,
        items_payload=payload,
        totals=totals,
        external_system=external_system,
        external_id=external_id,
        external_number=external_number,
    )
    snapshot_hash = fiscal_payload_hash(snapshot_payload)
    resolved_type = sales_document_type or resolve_sales_document_type_for_fiscal_doc(
        company=company,
        doc_type=doc_type,
        origin_channel=getattr(order, "origin_channel", ""),
    )

    with transaction.atomic():
        existing = FiscalDocument.objects.select_for_update().filter(source_key=source_key).first()
        if existing:
            if existing.snapshot_hash != snapshot_hash:
                raise FiscalPayloadConflict(
                    "El registro externo idempotente ya existe con distinto payload."
                )
            return existing, False

        duplicate = (
            FiscalDocument.objects.select_for_update()
            .filter(
                company=company,
                order=order,
                point_of_sale=point_of_sale,
                doc_type=doc_type,
            )
            .exclude(status="voided")
            .first()
        )
        if duplicate:
            raise ValidationError(
                "Ya existe un comprobante fiscal para este pedido, tipo y punto de venta."
            )

        duplicate_external_filter = FiscalDocument.objects.select_for_update().filter(
            company=company,
            external_system=external_system,
        )
        if external_id and external_number:
            duplicate_external_filter = duplicate_external_filter.filter(
                Q(external_id=external_id) | Q(external_number=external_number)
            )
        elif external_id:
            duplicate_external_filter = duplicate_external_filter.filter(external_id=external_id)
        else:
            duplicate_external_filter = duplicate_external_filter.filter(external_number=external_number)
        duplicate_external = duplicate_external_filter.first()
        if duplicate_external:
            return duplicate_external, False

        issued_now = timezone.now()
        document = FiscalDocument.objects.create(
            source_key=source_key,
            company=company,
            client_company_ref=client_company_ref,
            client_profile=client_company_ref.client_profile if client_company_ref else None,
            order=order,
            point_of_sale=point_of_sale,
            doc_type=doc_type,
            issue_mode=FISCAL_ISSUE_MODE_EXTERNAL_SAAS,
            status=FISCAL_STATUS_DRAFT,
            payment_due_date=resolve_payment_due_date(order=order, issued_at=issued_now),
            sales_document_type=resolved_type,
            subtotal_net=totals["subtotal_net"],
            discount_total=totals["discount_total"],
            tax_total=totals["tax_total"],
            total=totals["total"],
            currency="ARS",
            exchange_rate=Decimal("1.000000"),
            external_system=external_system,
            external_id=external_id,
            external_number=external_number,
            fiscal_snapshot=snapshot_payload,
            snapshot_schema_version=2,
            snapshot_hash=snapshot_hash,
            prepared_at=issued_now,
            issuer_cuit_snapshot="".join(
                character
                for character in str(getattr(company, "cuit", "") or "")
                if character.isdigit()
            ),
            environment_snapshot=point_of_sale.environment,
            point_of_sale_number_snapshot=point_of_sale.number,
            receiver_iva_condition_id_snapshot=getattr(
                client_company_ref.client_profile,
                "iva_condition_arca_id",
                None,
            ),
            receiver_iva_condition_label_snapshot=str(
                getattr(client_company_ref.client_profile, "iva_condition", "") or ""
            ),
            receiver_iva_condition_source_snapshot=str(
                getattr(client_company_ref.client_profile, "iva_condition_source", "") or ""
            ),
            receiver_iva_condition_validated_at_snapshot=getattr(
                client_company_ref.client_profile,
                "iva_condition_validated_at",
                None,
            ),
            request_payload={},
        )
        _create_document_items(document, payload)
        document.transition_to(FISCAL_STATUS_READY_TO_ISSUE)
        document.transition_to(
            FISCAL_STATUS_EXTERNAL_RECORDED,
            issued_at=issued_now,
            resolved_at=issued_now,
        )
        return document, True


def close_fiscal_document(*, fiscal_document, actor=None):
    """Close a manual fiscal document without ARCA emission."""
    if not fiscal_document:
        raise ValidationError("Comprobante fiscal invalido.")
    if fiscal_document.status == FISCAL_STATUS_VOIDED:
        raise ValidationError("El comprobante esta anulado.")
    if fiscal_document.issue_mode == FISCAL_ISSUE_MODE_ARCA_WSFE:
        raise ValidationError("Los comprobantes ARCA se cierran emitiendolos.")
    if fiscal_document.issue_mode == FISCAL_ISSUE_MODE_EXTERNAL_SAAS:
        raise ValidationError("El comprobante externo ya esta cerrado por definicion.")
    if fiscal_document.status == FISCAL_STATUS_EXTERNAL_RECORDED:
        return fiscal_document, False
    if fiscal_document.status != FISCAL_STATUS_READY_TO_ISSUE:
        raise ValidationError("El comprobante no puede cerrarse en su estado actual.")
    relation_ok, relation_errors = validate_credit_note_relationship(fiscal_document)
    if not relation_ok:
        raise ValidationError("No se puede cerrar el comprobante: " + " | ".join(relation_errors))
    if fiscal_document.order_id:
        invoice_ready, invoice_errors = is_invoice_ready(fiscal_document.order)
        if not invoice_ready:
            raise ValidationError(
                "No se puede cerrar el comprobante: " + " | ".join(invoice_errors)
            )

    issued_at = fiscal_document.issued_at or timezone.now()
    payment_due_date = fiscal_document.payment_due_date or resolve_payment_due_date(
            order=fiscal_document.order,
            issued_at=issued_at,
        )
    fiscal_document.transition_to(
        FISCAL_STATUS_EXTERNAL_RECORDED,
        issued_at=issued_at,
        payment_due_date=payment_due_date,
        resolved_at=timezone.now(),
    )
    try:
        sync_fiscal_document_account_movement(
            fiscal_document=fiscal_document,
            actor=actor,
        )
    except Exception:
        pass
    return fiscal_document, True


def reopen_fiscal_document(*, fiscal_document, actor=None):
    """Closed fiscal snapshots are immutable; corrections use a related document."""
    if not fiscal_document:
        raise ValidationError("Comprobante fiscal invalido.")
    raise ValidationError(
        "Un comprobante fiscal cerrado es inmutable. Emite un documento relacionado para corregirlo."
    )


def void_fiscal_document(*, fiscal_document, actor=None):
    """Void a fiscal document before it is legally authorized in ARCA."""
    if not fiscal_document:
        raise ValidationError("Comprobante fiscal invalido.")
    if fiscal_document.status == FISCAL_STATUS_VOIDED:
        return fiscal_document, False
    if fiscal_document.status not in {
        FISCAL_STATUS_DRAFT,
        FISCAL_STATUS_READY_TO_ISSUE,
        FISCAL_STATUS_REJECTED,
    }:
        raise ValidationError(
            "La operacion fiscal ya conserva evidencia y no puede anularse fisicamente. Usa un documento relacionado."
        )

    fiscal_document.transition_to(FISCAL_STATUS_VOIDED)
    try:
        sync_fiscal_document_account_movement(
            fiscal_document=fiscal_document,
            actor=actor,
        )
    except Exception:
        pass
    return fiscal_document, True
