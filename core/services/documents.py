"""Internal document helpers (numbering + auto-generation)."""

from django.db import transaction
from django.utils import timezone

from core.models import DocumentSeries, InternalDocument
from core.services.sales_documents import (
    apply_sales_document_type_to_internal_document,
    resolve_sales_document_type_for_internal_doc,
)


def _allocate_number(*, company, doc_type):
    series, _ = DocumentSeries.objects.select_for_update().get_or_create(
        company=company,
        doc_type=doc_type,
        defaults={"next_number": 1},
    )
    number = series.next_number
    series.next_number = number + 1
    series.save(update_fields=["next_number", "updated_at"])
    return number


def _ensure_document(
    *,
    source_key,
    doc_type,
    company,
    client_company_ref=None,
    client_profile=None,
    order=None,
    payment=None,
    transaction_obj=None,
    sales_document_type=None,
    actor=None,
):
    if not company:
        return None
    existing = InternalDocument.objects.filter(source_key=source_key).first()
    if existing:
        return apply_sales_document_type_to_internal_document(
            document=existing,
            sales_document_type=sales_document_type,
            actor=actor,
        )
    with transaction.atomic():
        existing = InternalDocument.objects.select_for_update().filter(source_key=source_key).first()
        if existing:
            return apply_sales_document_type_to_internal_document(
                document=existing,
                sales_document_type=sales_document_type,
                actor=actor,
            )
        number = _allocate_number(company=company, doc_type=doc_type)
        document = InternalDocument.objects.create(
            source_key=source_key,
            doc_type=doc_type,
            number=number,
            company=company,
            client_company_ref=client_company_ref,
            client_profile=client_profile,
            order=order,
            payment=payment,
            transaction=transaction_obj,
            sales_document_type=sales_document_type,
            issued_at=timezone.now(),
        )
    return apply_sales_document_type_to_internal_document(
        document=document,
        sales_document_type=sales_document_type,
        actor=actor,
    )


def ensure_document_for_order(order, *, doc_type, sales_document_type=None, actor=None):
    if not order or not getattr(order, "company_id", None):
        return None
    client_company_ref = getattr(order, "client_company_ref", None)
    if not client_company_ref:
        return None
    if sales_document_type is None:
        sales_document_type = resolve_sales_document_type_for_internal_doc(
            company=order.company,
            doc_type=doc_type,
        )
    source_key = f"order:{order.pk}:{doc_type}"
    return _ensure_document(
        source_key=source_key,
        doc_type=doc_type,
        company=order.company,
        client_company_ref=client_company_ref,
        client_profile=client_company_ref.client_profile if client_company_ref else None,
        order=order,
        sales_document_type=sales_document_type,
        actor=actor,
    )


def ensure_document_for_payment(payment, sales_document_type=None):
    if not payment or not getattr(payment, "company_id", None):
        return None
    client_profile = getattr(payment, "client_profile", None)
    client_company_ref = None
    if client_profile and payment.company_id:
        try:
            client_company_ref = client_profile.get_company_link(payment.company)
        except Exception:
            client_company_ref = None
    if not client_company_ref:
        return None
    if sales_document_type is None:
        sales_document_type = resolve_sales_document_type_for_internal_doc(
            company=payment.company,
            doc_type=DocumentSeries.DOC_REC,
        )
    source_key = f"payment:{payment.pk}:REC"
    return _ensure_document(
        source_key=source_key,
        doc_type=DocumentSeries.DOC_REC,
        company=payment.company,
        client_company_ref=client_company_ref,
        client_profile=client_profile,
        order=getattr(payment, "order", None),
        payment=payment,
        sales_document_type=sales_document_type,
    )


def ensure_document_for_adjustment(transaction_obj, sales_document_type=None):
    if not transaction_obj or transaction_obj.transaction_type != transaction_obj.TYPE_ADJUSTMENT:
        return None
    if not getattr(transaction_obj, "company_id", None):
        return None
    client_profile = getattr(transaction_obj, "client_profile", None)
    client_company_ref = None
    if client_profile and transaction_obj.company_id:
        try:
            client_company_ref = client_profile.get_company_link(transaction_obj.company)
        except Exception:
            client_company_ref = None
    if sales_document_type is None:
        sales_document_type = resolve_sales_document_type_for_internal_doc(
            company=transaction_obj.company,
            doc_type=DocumentSeries.DOC_AJU,
        )
    source_key = f"adjustment:{transaction_obj.pk}:AJU"
    return _ensure_document(
        source_key=source_key,
        doc_type=DocumentSeries.DOC_AJU,
        company=transaction_obj.company,
        client_company_ref=client_company_ref,
        client_profile=client_profile,
        transaction_obj=transaction_obj,
        sales_document_type=sales_document_type,
    )
