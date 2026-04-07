"""Fiscal module views extracted from admin_panel.views.

This module centralizes fiscal reporting/dashboard screens so we can
progressively decouple the monolithic admin views file without breaking URLs.
"""

from __future__ import annotations

import csv
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from typing import Callable

from django.conf import settings
from django.contrib import messages
from django.db.models import Count, Max, Q, Sum
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date

from core.models import (
    FISCAL_ISSUE_MODE_ARCA_WSFE,
    FISCAL_ISSUE_MODE_MANUAL,
    FISCAL_STATUS_AUTHORIZED,
    FISCAL_STATUS_EXTERNAL_RECORDED,
    FISCAL_STATUS_PENDING_RETRY,
    FISCAL_STATUS_READY_TO_ISSUE,
    FISCAL_STATUS_REJECTED,
    FISCAL_STATUS_SUBMITTING,
    FiscalDocument,
    FiscalPointOfSale,
)
from core.services.fiscal import is_company_fiscal_ready
from core.services.advanced_search import sanitize_search_token


def _resolve_range_params(request):
    date_from_raw = str(request.GET.get("date_from", "")).strip()
    date_to_raw = str(request.GET.get("date_to", "")).strip()

    date_from = parse_date(date_from_raw) if date_from_raw else None
    date_to = parse_date(date_to_raw) if date_to_raw else None
    if not date_to:
        date_to = timezone.localdate()
    if not date_from:
        date_from = date_to - timedelta(days=30)
    if date_from > date_to:
        date_from, date_to = date_to, date_from
    return date_from, date_to, date_from_raw, date_to_raw


def _stale_submitting_q(now_dt):
    timeout_minutes = int(getattr(settings, "FISCAL_SUBMITTING_TIMEOUT_MINUTES", 20) or 20)
    cutoff = now_dt - timedelta(minutes=max(timeout_minutes, 5))
    return Q(status=FISCAL_STATUS_SUBMITTING) & (
        Q(last_attempt_at__isnull=False, last_attempt_at__lte=cutoff)
        | Q(last_attempt_at__isnull=True, updated_at__lte=cutoff)
    )


def fiscal_report_view(
    request,
    *,
    get_active_company_fn: Callable,
    deny_if_needed_fn: Callable,
    build_collection_snapshot_fn: Callable,
    can_manage_fiscal_operations_fn: Callable,
):
    """Render fiscal report screen (extracted from monolithic admin_panel.views)."""
    active_company = get_active_company_fn(request)
    if not active_company:
        messages.error(request, "Selecciona una empresa activa para operar.")
        return redirect("select_company")

    denied_response = deny_if_needed_fn(
        request,
        redirect_url=reverse("admin_fiscal_document_list"),
        action_label="ver reportes fiscales",
    )
    if denied_response:
        return denied_response

    status = str(request.GET.get("status", "")).strip()
    doc_type = str(request.GET.get("doc_type", "")).strip()
    issue_mode = str(request.GET.get("issue_mode", "")).strip()
    export = str(request.GET.get("export", "")).strip().lower()
    date_from, date_to, date_from_raw, date_to_raw = _resolve_range_params(request)

    documents = (
        FiscalDocument.objects.select_related(
            "company",
            "point_of_sale",
            "order",
            "client_company_ref__client_profile",
        )
        .filter(company=active_company)
        .order_by("-created_at")
    )
    if status:
        documents = documents.filter(status=status)
    if doc_type:
        documents = documents.filter(doc_type=doc_type)
    if issue_mode:
        documents = documents.filter(issue_mode=issue_mode)
    if date_from:
        documents = documents.filter(created_at__date__gte=date_from)
    if date_to:
        documents = documents.filter(created_at__date__lte=date_to)

    today = timezone.localdate()
    overdue_qs = documents.filter(
        status__in=[FISCAL_STATUS_AUTHORIZED, FISCAL_STATUS_EXTERNAL_RECORDED],
        payment_due_date__lt=today,
    )
    summary = {
        "documents_count": documents.count(),
        "total_amount": documents.aggregate(total=Coalesce(Sum("total"), Decimal("0.00"))).get("total")
        or Decimal("0.00"),
        "authorized_count": documents.filter(status=FISCAL_STATUS_AUTHORIZED).count(),
        "pending_retry_count": documents.filter(status=FISCAL_STATUS_PENDING_RETRY).count(),
        "rejected_count": documents.filter(status=FISCAL_STATUS_REJECTED).count(),
        "overdue_count": overdue_qs.count(),
    }
    summary["overdue_amount"] = overdue_qs.aggregate(total=Coalesce(Sum("total"), Decimal("0.00"))).get("total") or Decimal("0.00")

    grouped = list(
        documents.values("doc_type").annotate(
            documents_count=Count("id"),
            total_amount=Coalesce(Sum("total"), Decimal("0.00")),
        ).order_by("doc_type")
    )
    doc_type_label_map = dict(FiscalDocument.DOC_TYPE_CHOICES)
    for row in grouped:
        row["doc_type_label"] = doc_type_label_map.get(row["doc_type"], row["doc_type"])

    rows = list(documents[:400])
    for row in rows:
        row.collection_snapshot = build_collection_snapshot_fn(row)

    if export == "csv":
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "fecha",
                "tipo_comercial",
                "tipo_fiscal",
                "numero",
                "cliente",
                "pedido",
                "estado_fiscal",
                "estado_cobranza",
                "total",
                "vencimiento",
                "proximo_reintento",
            ]
        )
        for doc in rows:
            snapshot = getattr(doc, "collection_snapshot", build_collection_snapshot_fn(doc))
            writer.writerow(
                [
                    doc.created_at.strftime("%Y-%m-%d %H:%M"),
                    doc.commercial_type_label,
                    doc.get_doc_type_display(),
                    doc.display_number,
                    getattr(getattr(doc, "client_company_ref", None), "client_profile", None).company_name
                    if getattr(doc, "client_company_ref", None)
                    and getattr(doc.client_company_ref, "client_profile", None)
                    else "-",
                    doc.order_id or "",
                    doc.get_status_display(),
                    snapshot.get("status_label"),
                    f"{Decimal(doc.total or 0):.2f}",
                    doc.payment_due_date.strftime("%Y-%m-%d") if doc.payment_due_date else "",
                    doc.next_retry_at.strftime("%Y-%m-%d %H:%M") if doc.next_retry_at else "",
                ]
            )
        response = HttpResponse(output.getvalue(), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = (
            f'attachment; filename="reporte_fiscal_{active_company.slug}_{timezone.now().strftime("%Y%m%d_%H%M%S")}.csv"'
        )
        return response

    return render(
        request,
        "admin_panel/fiscal/report.html",
        {
            "active_company": active_company,
            "documents": rows,
            "summary": summary,
            "grouped": grouped,
            "status": status,
            "doc_type": doc_type,
            "issue_mode": issue_mode,
            "date_from": date_from_raw,
            "date_to": date_to_raw,
            "status_choices": FiscalDocument.STATUS_CHOICES,
            "doc_type_choices": FiscalDocument.DOC_TYPE_CHOICES,
            "issue_mode_choices": FiscalDocument.ISSUE_MODE_CHOICES,
            "can_manage_fiscal_operations": can_manage_fiscal_operations_fn(request.user),
        },
    )


def fiscal_health_view(
    request,
    *,
    get_active_company_fn: Callable,
    deny_if_needed_fn: Callable,
):
    """Operational health dashboard for fiscal emission pipeline."""
    active_company = get_active_company_fn(request)
    if not active_company:
        messages.error(request, "Selecciona una empresa activa para operar.")
        return redirect("select_company")

    denied_response = deny_if_needed_fn(
        request,
        redirect_url=reverse("admin_fiscal_document_list"),
        action_label="ver salud fiscal",
    )
    if denied_response:
        return denied_response

    date_from, date_to, date_from_raw, date_to_raw = _resolve_range_params(request)
    search = sanitize_search_token(request.GET.get("q", ""))
    now_dt = timezone.now()

    base_qs = (
        FiscalDocument.objects.select_related(
            "company",
            "point_of_sale",
            "order",
            "client_company_ref__client_profile",
        )
        .filter(company=active_company)
        .order_by("-updated_at", "-id")
    )
    period_qs = base_qs.filter(created_at__date__gte=date_from, created_at__date__lte=date_to)
    if search:
        filter_q = (
            Q(source_key__icontains=search)
            | Q(error_code__icontains=search)
            | Q(error_message__icontains=search)
            | Q(external_number__icontains=search)
            | Q(client_company_ref__client_profile__company_name__icontains=search)
        )
        if search.isdigit():
            filter_q |= Q(order_id=int(search))
        period_qs = period_qs.filter(filter_q)

    stale_q = _stale_submitting_q(now_dt)
    stale_submitting_qs = base_qs.filter(stale_q)
    retry_due_qs = base_qs.filter(status=FISCAL_STATUS_PENDING_RETRY).filter(
        Q(next_retry_at__isnull=True) | Q(next_retry_at__lte=now_dt)
    )
    rejected_qs = base_qs.filter(status=FISCAL_STATUS_REJECTED)

    summary = {
        "period_documents": period_qs.count(),
        "period_total": period_qs.aggregate(total=Coalesce(Sum("total"), Decimal("0.00"))).get("total")
        or Decimal("0.00"),
        "authorized": period_qs.filter(status=FISCAL_STATUS_AUTHORIZED).count(),
        "ready_to_issue": base_qs.filter(status=FISCAL_STATUS_READY_TO_ISSUE).count(),
        "pending_retry": base_qs.filter(status=FISCAL_STATUS_PENDING_RETRY).count(),
        "submitting": base_qs.filter(status=FISCAL_STATUS_SUBMITTING).count(),
        "rejected": base_qs.filter(status=FISCAL_STATUS_REJECTED).count(),
        "external_recorded": base_qs.filter(status=FISCAL_STATUS_EXTERNAL_RECORDED).count(),
    }
    summary["retry_due"] = retry_due_qs.count()
    summary["stale_submitting"] = stale_submitting_qs.count()
    summary["manual_ready"] = base_qs.filter(
        status=FISCAL_STATUS_READY_TO_ISSUE,
        issue_mode=FISCAL_ISSUE_MODE_MANUAL,
    ).count()
    summary["arca_ready"] = base_qs.filter(
        status=FISCAL_STATUS_READY_TO_ISSUE,
        issue_mode=FISCAL_ISSUE_MODE_ARCA_WSFE,
    ).count()

    issue_weight = (
        summary["stale_submitting"] * 6
        + summary["retry_due"] * 4
        + summary["rejected"] * 2
        + summary["pending_retry"]
    )
    health_score = max(0, 100 - min(95, issue_weight))

    top_errors = list(
        base_qs.filter(status__in=[FISCAL_STATUS_PENDING_RETRY, FISCAL_STATUS_REJECTED])
        .exclude(error_code="")
        .values("error_code")
        .annotate(total=Count("id"), last_seen=Max("updated_at"))
        .order_by("-total", "-last_seen")[:10]
    )
    for row in top_errors:
        sample_doc = (
            base_qs.filter(error_code=row["error_code"])
            .exclude(error_message="")
            .order_by("-updated_at")
            .values_list("error_message", flat=True)
            .first()
        )
        row["sample_message"] = sample_doc or "-"

    stale_docs = list(stale_submitting_qs[:30])
    retry_due_docs = list(retry_due_qs[:30])
    rejected_recent = list(rejected_qs[:30])
    points = list(
        FiscalPointOfSale.objects.filter(company=active_company)
        .order_by("-is_default", "number")
    )
    is_ready, readiness_errors = is_company_fiscal_ready(active_company)

    return render(
        request,
        "admin_panel/fiscal/health.html",
        {
            "active_company": active_company,
            "date_from": date_from_raw or date_from.isoformat(),
            "date_to": date_to_raw or date_to.isoformat(),
            "search": search,
            "summary": summary,
            "health_score": health_score,
            "stale_docs": stale_docs,
            "retry_due_docs": retry_due_docs,
            "rejected_recent": rejected_recent,
            "top_errors": top_errors,
            "points": points,
            "is_ready": is_ready,
            "readiness_errors": readiness_errors,
        },
    )
