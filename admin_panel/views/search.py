"""Company-safe global search for the internal panel."""

from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Q
from django.shortcuts import redirect, render
from django.urls import reverse

from accounts.models import ClientProfile
from catalog.models import Product
from core.models import FiscalDocument
from core.services.advanced_search import sanitize_search_token
from core.services.authorization import CAP_GLOBAL_SEARCH, capability_required
from core.services.company_context import get_active_company
from orders.models import Order, OrderRequest


@staff_member_required
@capability_required(CAP_GLOBAL_SEARCH)
def global_search(request):
    company = get_active_company(request)
    if not company:
        return redirect("select_company")

    query = sanitize_search_token(request.GET.get("q", ""))[:120]
    selected_type = str(request.GET.get("type", "all") or "all").strip().lower()
    valid_types = {"all", "products", "clients", "orders", "requests", "documents"}
    if selected_type not in valid_types:
        selected_type = "all"

    sections = []
    if query:
        if selected_type in {"all", "products"}:
            rows = Product.objects.filter(
                Q(sku__icontains=query)
                | Q(name__icontains=query)
                | Q(description__icontains=query)
                | Q(supplier__icontains=query)
                | Q(supplier_ref__name__icontains=query)
                | Q(supplier_offers__supplier__name__icontains=query)
                | Q(supplier_offers__supplier_code__icontains=query)
                | Q(supplier_offers__supplier_description__icontains=query)
            ).select_related("supplier_ref").distinct().order_by("name")[:25]
            sections.append({
                "key": "products",
                "label": "Productos",
                "rows": [
                    {
                        "title": f"{row.sku} - {row.name}",
                        "summary": f"Stock {row.stock} · ${row.price:.2f}",
                        "url": reverse("admin_product_edit", args=[row.pk]),
                    }
                    for row in rows
                ],
            })

        if selected_type in {"all", "clients"}:
            rows = (
                ClientProfile.objects.filter(
                    company_links__company=company,
                    company_links__is_active=True,
                )
                .filter(
                    Q(company_name__icontains=query)
                    | Q(cuit_dni__icontains=query)
                    | Q(document_number__icontains=query)
                    | Q(user__username__icontains=query)
                    | Q(user__email__icontains=query)
                )
                .select_related("user")
                .distinct()
                .order_by("company_name")[:25]
            )
            sections.append({
                "key": "clients",
                "label": "Clientes",
                "rows": [
                    {
                        "title": row.company_name or row.user.username,
                        "summary": f"{row.cuit_dni or '-'} · {row.user.email or '-'}",
                        "url": reverse("admin_client_order_history", args=[row.pk]),
                    }
                    for row in rows
                ],
            })

        numeric_query = int(query.lstrip("#")) if query.lstrip("#").isdigit() else None
        if selected_type in {"all", "orders"}:
            order_filter = (
                Q(client_company__icontains=query)
                | Q(client_cuit__icontains=query)
                | Q(external_number__icontains=query)
                | Q(user__username__icontains=query)
            )
            if numeric_query is not None:
                order_filter |= Q(pk=numeric_query)
            rows = Order.objects.filter(company=company).filter(order_filter).select_related("user").order_by("-created_at")[:25]
            sections.append({
                "key": "orders",
                "label": "Pedidos",
                "rows": [
                    {
                        "title": f"Pedido #{row.pk} · {row.client_company or getattr(row.user, 'username', '-')}",
                        "summary": f"{row.get_status_display()} · ${row.total:.2f}",
                        "url": reverse("admin_order_detail", args=[row.pk]),
                    }
                    for row in rows
                ],
            })

        if selected_type in {"all", "requests"}:
            request_filter = Q(client_note__icontains=query) | Q(user__username__icontains=query)
            if numeric_query is not None:
                request_filter |= Q(pk=numeric_query)
            rows = OrderRequest.objects.filter(company=company).filter(request_filter).select_related("user").order_by("-created_at")[:25]
            sections.append({
                "key": "requests",
                "label": "Solicitudes web",
                "rows": [
                    {
                        "title": f"Solicitud #{row.pk} · {getattr(row.user, 'username', '-')}",
                        "summary": f"{row.get_status_display()} · ${row.requested_total:.2f}",
                        "url": reverse("admin_order_request_detail", args=[row.pk]),
                    }
                    for row in rows
                ],
            })

        if selected_type in {"all", "documents"}:
            document_filter = (
                Q(client_profile__company_name__icontains=query)
                | Q(client_profile__cuit_dni__icontains=query)
                | Q(client_company_ref__client_profile__company_name__icontains=query)
                | Q(client_company_ref__client_profile__cuit_dni__icontains=query)
                | Q(cae__icontains=query)
                | Q(external_number__icontains=query)
            )
            if numeric_query is not None:
                document_filter |= Q(number=numeric_query) | Q(pk=numeric_query)
            rows = (
                FiscalDocument.objects.filter(company=company)
                .filter(document_filter)
                .select_related("point_of_sale", "sales_document_type", "client_profile", "client_company_ref__client_profile")
                .order_by("-created_at")[:25]
            )
            sections.append({
                "key": "documents",
                "label": "Comprobantes",
                "rows": [
                    {
                        "title": f"{row.get_doc_type_display()} {row.display_number}",
                        "summary": (
                            f"{getattr(row.client_profile, 'company_name', '') or getattr(getattr(row.client_company_ref, 'client_profile', None), 'company_name', '') or '-'}"
                            f" · ${row.total:.2f}"
                        ),
                        "url": reverse("admin_fiscal_document_detail", args=[row.pk]),
                    }
                    for row in rows
                ],
            })

    total_results = sum(len(section["rows"]) for section in sections)
    return render(
        request,
        "admin_panel/search/results.html",
        {
            "query": query,
            "selected_type": selected_type,
            "sections": sections,
            "total_results": total_results,
        },
    )


__all__ = ["global_search"]
