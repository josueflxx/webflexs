import csv
from pathlib import Path

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from admin_panel.forms.supplier_import_forms import (
    SupplierPriceListApplyForm,
    SupplierPriceListMappingForm,
    SupplierPriceListUploadForm,
)
from catalog.models import (
    SupplierImportProfile,
    SupplierPriceListBatch,
    SupplierPriceListRow,
)
from catalog.services.supplier_price_lists import (
    MAPPING_FIELDS,
    apply_supplier_price_list,
    generate_supplier_price_list_preview,
    hash_uploaded_file,
    inspect_source_file,
    report_rows,
    update_row_decisions,
)
from core.services.audit import log_admin_action
from core.services.authorization import (
    CAP_MANAGE_PRODUCTS,
    CAP_RUN_IMPORTS,
    capability_required,
)
from core.services.company_context import get_active_company, user_has_company_access


def _price_list_permissions(view):
    view = capability_required(CAP_MANAGE_PRODUCTS)(view)
    view = capability_required(CAP_RUN_IMPORTS)(view)
    return staff_member_required(view)


def _batch_for_user(request, batch_id):
    batch = get_object_or_404(
        SupplierPriceListBatch.objects.select_related(
            "supplier", "company", "profile", "created_by", "applied_by", "import_execution"
        ),
        pk=batch_id,
    )
    if not user_has_company_access(request.user, batch.company):
        raise Http404
    return batch


def _mapping_from_data(data):
    return {
        field_name: str(data.get(field_name) or "").strip()
        for field_name, _label in MAPPING_FIELDS
        if str(data.get(field_name) or "").strip()
    }


def _mapping_context(batch, form, inspection):
    return {
        "batch": batch,
        "form": form,
        "inspection": inspection,
        "mapping_fields": MAPPING_FIELDS,
    }


@_price_list_permissions
def supplier_price_list_batches(request):
    active_company = get_active_company(request)
    if not active_company:
        messages.error(request, "Selecciona una empresa activa para ver listas de proveedor.")
        return redirect("select_company")
    batches = (
        SupplierPriceListBatch.objects.filter(company=active_company)
        .select_related("supplier", "created_by", "applied_by")
        .order_by("-created_at")
    )
    page_obj = Paginator(batches, 30).get_page(request.GET.get("page"))
    return render(
        request,
        "admin_panel/supplier_price_lists/batches.html",
        {"page_obj": page_obj, "active_company": active_company},
    )


@_price_list_permissions
def supplier_price_list_upload(request):
    active_company = get_active_company(request)
    if not active_company:
        messages.error(request, "Selecciona una empresa activa antes de cargar una lista.")
        return redirect("select_company")

    form = SupplierPriceListUploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        uploaded = form.cleaned_data["source_file"]
        supplier = form.cleaned_data["supplier"]
        profile = form.cleaned_data.get("profile")
        digest = hash_uploaded_file(uploaded)
        if SupplierPriceListBatch.objects.filter(
            supplier=supplier,
            file_sha256=digest,
            status=SupplierPriceListBatch.STATUS_APPLIED,
        ).exists():
            form.add_error("source_file", "Este mismo archivo ya fue aplicado para el proveedor.")
        else:
            batch = SupplierPriceListBatch.objects.create(
                supplier=supplier,
                company=active_company,
                profile=profile,
                source_file=uploaded,
                original_filename=Path(uploaded.name).name[:255],
                file_sha256=digest,
                file_size=uploaded.size or 0,
                sheet_name=profile.sheet_name if profile else "",
                header_row=profile.header_row if profile else 1,
                column_mapping=profile.column_mapping if profile else {},
                default_currency=(
                    profile.default_currency
                    if profile
                    else SupplierPriceListBatch._meta.get_field("default_currency").default
                ),
                created_by=request.user,
            )
            log_admin_action(
                request,
                action="supplier_price_list_upload",
                target_type="supplier_price_list_batch",
                target_id=batch.pk,
                details={
                    "supplier_id": supplier.pk,
                    "file_name": batch.original_filename,
                    "file_sha256": digest,
                    "profile_id": profile.pk if profile else None,
                },
            )
            messages.success(request, "Archivo conservado. Ahora revisa el mapeo de columnas.")
            return redirect("admin_supplier_price_list_mapping", batch_id=batch.pk)

    return render(
        request,
        "admin_panel/supplier_price_lists/upload.html",
        {"form": form, "active_company": active_company},
    )


@_price_list_permissions
def supplier_price_list_mapping(request, batch_id):
    batch = _batch_for_user(request, batch_id)
    if batch.status == SupplierPriceListBatch.STATUS_APPLIED:
        messages.info(request, "Esta lista ya fue aplicada.")
        return redirect("admin_supplier_price_list_preview", batch_id=batch.pk)

    profile = batch.profile
    initial_mapping = dict(batch.column_mapping or (profile.column_mapping if profile else {}) or {})
    requested_sheet = str(
        (request.POST.get("sheet_name") if request.method == "POST" else "")
        or batch.sheet_name
        or (profile.sheet_name if profile else "")
    ).strip()
    try:
        requested_header = int(
            (request.POST.get("header_row") if request.method == "POST" else None)
            or batch.header_row
            or (profile.header_row if profile else 1)
        )
    except (TypeError, ValueError):
        requested_header = 1

    if request.method == "POST":
        initial_mapping = _mapping_from_data(request.POST)

    try:
        inspection = inspect_source_file(
            batch.source_file.path,
            sheet_name=requested_sheet,
            header_row=requested_header,
        )
    except (ValidationError, OSError) as exc:
        messages.error(request, "; ".join(getattr(exc, "messages", [str(exc)])))
        return redirect("admin_supplier_price_list_batches")

    initial = {
        "sheet_name": inspection["sheet_name"],
        "header_row": requested_header,
        "default_currency": batch.default_currency,
        **initial_mapping,
    }
    action = request.POST.get("action") if request.method == "POST" else ""
    if action == "inspect":
        form = SupplierPriceListMappingForm(
            headers=inspection["headers"],
            sheets=inspection["sheets"],
            initial_mapping=initial_mapping,
            initial=initial,
        )
        messages.info(request, "Columnas detectadas de nuevo. Revisa el mapeo antes de continuar.")
        return render(
            request,
            "admin_panel/supplier_price_lists/mapping.html",
            _mapping_context(batch, form, inspection),
        )

    form = SupplierPriceListMappingForm(
        request.POST or None,
        headers=inspection["headers"],
        sheets=inspection["sheets"],
        initial_mapping=initial_mapping,
        initial=initial,
    )
    if request.method == "POST" and form.is_valid():
        mapping = form.cleaned_data["column_mapping"]
        batch.default_currency = form.cleaned_data["default_currency"]
        batch.save(update_fields=["default_currency", "updated_at"])
        if form.cleaned_data.get("save_profile"):
            profile, profile_created = SupplierImportProfile.objects.get_or_create(
                supplier=batch.supplier,
                name=str(form.cleaned_data["profile_name"]).strip(),
                defaults={
                    "sheet_name": form.cleaned_data["sheet_name"],
                    "header_row": form.cleaned_data["header_row"],
                    "column_mapping": mapping,
                    "default_currency": form.cleaned_data["default_currency"],
                    "is_active": True,
                    "updated_by": request.user,
                    "created_by": request.user,
                },
            )
            if not profile_created:
                profile.sheet_name = form.cleaned_data["sheet_name"]
                profile.header_row = form.cleaned_data["header_row"]
                profile.column_mapping = mapping
                profile.default_currency = form.cleaned_data["default_currency"]
                profile.is_active = True
                profile.updated_by = request.user
                profile.save(
                    update_fields=[
                        "sheet_name", "header_row", "column_mapping", "default_currency",
                        "is_active", "updated_by", "updated_at",
                    ]
                )
            batch.profile = profile
            batch.save(update_fields=["profile", "updated_at"])
        try:
            generate_supplier_price_list_preview(
                batch,
                mapping=mapping,
                sheet_name=form.cleaned_data["sheet_name"],
                header_row=form.cleaned_data["header_row"],
            )
        except ValidationError as exc:
            form.add_error(None, "; ".join(exc.messages))
        else:
            log_admin_action(
                request,
                action="supplier_price_list_preview",
                target_type="supplier_price_list_batch",
                target_id=batch.pk,
                details=batch.summary,
            )
            messages.success(request, "Previsualizacion generada sin modificar costos.")
            return redirect("admin_supplier_price_list_preview", batch_id=batch.pk)

    return render(
        request,
        "admin_panel/supplier_price_lists/mapping.html",
        _mapping_context(batch, form, inspection),
    )


@_price_list_permissions
def supplier_price_list_preview(request, batch_id):
    batch = _batch_for_user(request, batch_id)
    if batch.status == SupplierPriceListBatch.STATUS_UPLOADED:
        return redirect("admin_supplier_price_list_mapping", batch_id=batch.pk)

    if request.method == "POST":
        decisions = {}
        for key, value in request.POST.items():
            if not key.startswith("decision_"):
                continue
            try:
                decisions[int(key.removeprefix("decision_"))] = str(value)
            except ValueError:
                continue
        try:
            changed = update_row_decisions(batch, decisions)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.success(request, f"Decisiones guardadas: {changed} cambios.")
        query = request.GET.urlencode()
        url = reverse("admin_supplier_price_list_preview", kwargs={"batch_id": batch.pk})
        return redirect(f"{url}?{query}" if query else url)

    rows = batch.rows.select_related("matched_product", "product_supplier")
    change_filter = str(request.GET.get("change") or "").strip()
    decision_filter = str(request.GET.get("decision") or "").strip()
    if change_filter in dict(SupplierPriceListRow.CHANGE_CHOICES):
        rows = rows.filter(change_type=change_filter)
    if decision_filter in dict(SupplierPriceListRow.DECISION_CHOICES):
        rows = rows.filter(decision=decision_filter)
    page_obj = Paginator(rows.order_by("row_number"), 100).get_page(request.GET.get("page"))
    apply_form = SupplierPriceListApplyForm(batch=batch)
    return render(
        request,
        "admin_panel/supplier_price_lists/preview.html",
        {
            "batch": batch,
            "page_obj": page_obj,
            "apply_form": apply_form,
            "change_choices": SupplierPriceListRow.CHANGE_CHOICES,
            "decision_choices": SupplierPriceListRow.DECISION_CHOICES,
            "change_filter": change_filter,
            "decision_filter": decision_filter,
        },
    )


@_price_list_permissions
@require_POST
def supplier_price_list_apply(request, batch_id):
    batch = _batch_for_user(request, batch_id)
    form = SupplierPriceListApplyForm(request.POST, batch=batch)
    if not form.is_valid():
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
        return redirect("admin_supplier_price_list_preview", batch_id=batch.pk)
    try:
        apply_supplier_price_list(batch, user=request.user)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    except Exception:
        messages.error(request, "No se aplico ninguna fila. Revisa el error del lote.")
    else:
        log_admin_action(
            request,
            action="supplier_price_list_apply",
            target_type="supplier_price_list_batch",
            target_id=batch.pk,
            details=batch.summary,
        )
        messages.success(request, "Lista aplicada en una unica transaccion y con historial de costos.")
    return redirect("admin_supplier_price_list_preview", batch_id=batch.pk)


@_price_list_permissions
def supplier_price_list_report(request, batch_id):
    batch = _batch_for_user(request, batch_id)
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response.write("\ufeff")
    response["Content-Disposition"] = (
        f'attachment; filename="lista_proveedor_{batch.pk}_reporte.csv"'
    )
    writer = csv.writer(response, delimiter=";")
    headers = [
        "fila", "tipo", "codigo_proveedor", "descripcion", "sku_identificado",
        "producto_identificado", "metodo", "confianza", "cambio", "costo_anterior",
        "costo_propuesto", "diferencia", "diferencia_porcentaje", "moneda", "decision",
        "aplicado", "advertencias",
    ]
    writer.writerow(headers)
    for item in report_rows(batch):
        values = []
        for header in headers:
            value = item.get(header, "")
            if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                value = "'" + value
            values.append(value)
        writer.writerow(values)
    return response
