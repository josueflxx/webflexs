"""Preview-first administration of brand rubros and subrubros."""
from pathlib import PurePosixPath

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.http import Http404
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from admin_panel.forms.brand_structure_forms import BrandStructureForm
from catalog.models import Brand, BrandRubro, BrandSubrubro, BrandStructureBatch
from catalog.services.brand_structure import PREVIEW_LIFETIME, apply_batch, prepare_batch, undo_batch
from core.decorators import PRIMARY_SUPERADMIN_USERNAME, superuser_required_for_modifications
from core.services.audit import log_admin_action


def _can_manage(user):
    return user.is_superuser and user.username.strip().lower() == str(PRIMARY_SUPERADMIN_USERNAME).strip().lower()


def _draft(request):
    value = request.POST.get("draft") if request.method == "POST" else request.GET.get("draft")
    if not value:
        return None
    if not str(value).isdigit() or len(str(value)) > 18:
        raise Http404
    return get_object_or_404(BrandStructureBatch, pk=value, status="preview", created_by=request.user)


@staff_member_required
@superuser_required_for_modifications
@require_http_methods(["GET", "POST"])
def brand_structure_manage(request):
    previous = _draft(request)
    initial = {**previous.spec["initial"], "draft": previous.pk} if previous else None
    form = BrandStructureForm(request.POST or None, request.FILES or None, initial=initial, previous=previous)
    if request.method == "POST" and form.is_valid():
        try:
            batch = prepare_batch(form.cleaned_data, user=request.user, previous=previous)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            return redirect("admin_brand_structure_batch", pk=batch.pk)
    history = BrandStructureBatch.objects.filter(
        Q(status__in=["applied", "undone"]) | Q(created_by=request.user)
    ).select_related("created_by", "undone_by")
    context = {
        "form": form, "previous": previous, "can_manage_structure": _can_manage(request.user),
        "brands": Brand.objects.annotate(rubro_count=Count("rubros")).order_by("order", "name"),
        "selected_brand_ids": {str(pk) for pk in (form["brand_ids"].value() or [])},
        "rubro_names": BrandRubro.objects.order_by("name").values_list("name", flat=True).distinct(),
        "subrubro_names": BrandSubrubro.objects.order_by("name").values_list("name", flat=True).distinct(),
        "history": Paginator(history, 20).get_page(request.GET.get("page")),
    }
    return render(request, "admin_panel/brands/structure_manage.html", context)


def _display_value(field, value):
    if field == "is_active":
        return "Activo" if value else "Inactivo"
    if field == "image":
        return PurePosixPath(value).name if value else "Sin imagen"
    return str(value)


def _present_rows(entries):
    labels = {"name": "Nombre", "image": "Imagen", "order": "Orden", "is_active": "Estado"}
    status_labels = {"create": "Crear", "update": "Editar", "skip": "Omitir", "conflict": "Conflicto"}
    for entry in entries:
        row = dict(entry)
        before, after = row.get("before") or {}, row.get("after") or {}
        row["status_label"] = status_labels[row["status"]]
        row["fields"] = [
            {"label": label, "before": _display_value(field, before[field]) if field in before else "—", "after": _display_value(field, after[field])}
            for field, label in labels.items() if field in after and before.get(field) != after[field]
        ]
        yield row


@staff_member_required
@require_GET
def brand_structure_batch(request, pk):
    batch = get_object_or_404(BrandStructureBatch.objects.select_related("created_by", "undone_by"), pk=pk)
    if batch.status == "preview" and batch.created_by_id != request.user.pk:
        raise Http404
    expired = timezone.now() - batch.created_at > PREVIEW_LIFETIME
    return render(request, "admin_panel/brands/structure_batch.html", {
        "batch": batch, "rows": list(_present_rows(batch.plan["entries"])),
        "can_manage_structure": _can_manage(request.user), "expired": expired,
        "can_apply": batch.status == "preview" and batch.plan["can_apply"] and not expired and _can_manage(request.user),
    })


@staff_member_required
@superuser_required_for_modifications
@require_POST
def brand_structure_apply(request, pk):
    batch = get_object_or_404(BrandStructureBatch, pk=pk, created_by=request.user)
    if request.POST.get("confirm_scope") != "on":
        messages.error(request, "Confirmá las marcas y el alcance compartido antes de aplicar.")
        return redirect("admin_brand_structure_batch", pk=pk)
    was_applied = batch.status == "applied"
    try:
        batch = apply_batch(pk, user=request.user)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    except IntegrityError:
        messages.error(request, "Hubo un cambio simultáneo. No se aplicó el lote; generá una nueva vista previa.")
    else:
        if not was_applied:
            log_admin_action(request, "brand_structure_apply", target_type="BrandStructureBatch", target_id=pk, details={"operation": batch.operation, "rows": len(batch.changes), "brands": batch.spec["brand_ids"]})
        messages.success(request, "Lote aplicado. Los productos y sus asociaciones se conservaron.")
    return redirect("admin_brand_structure_batch", pk=pk)


@staff_member_required
@superuser_required_for_modifications
@require_POST
def brand_structure_undo(request, pk):
    get_object_or_404(BrandStructureBatch, pk=pk)
    if request.POST.get("confirm_undo") != "on":
        messages.error(request, "Confirmá que querés deshacer este lote.")
        return redirect("admin_brand_structure_batch", pk=pk)
    try:
        batch = undo_batch(pk, user=request.user)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    except IntegrityError:
        messages.error(request, "La estructura tiene nuevos vínculos. No se deshizo ningún registro.")
    else:
        log_admin_action(request, "brand_structure_undo", target_type="BrandStructureBatch", target_id=pk, details={"operation": batch.operation, "rows": len(batch.changes)})
        messages.success(request, "Lote deshecho. Las estructuras creadas sin uso se eliminaron y las editadas recuperaron sus valores anteriores. El historial se conserva.")
    return redirect("admin_brand_structure_batch", pk=pk)
