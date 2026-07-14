"""Operational pages for backups and integrations."""

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core.models import WebhookDelivery, WebhookEndpoint, generate_webhook_secret
from core.services.authorization import (
    CAP_MANAGE_BACKUPS,
    CAP_MANAGE_INTEGRATIONS,
    capability_required,
)
from core.services.backups import create_system_backup, list_backup_sets
from core.services.company_context import get_active_company
from core.tasks import create_automatic_backup_task


@staff_member_required
@capability_required(CAP_MANAGE_BACKUPS)
def backup_center(request):
    return render(
        request,
        "admin_panel/system/backups.html",
        {"backup_sets": list_backup_sets(limit=30)},
    )


@staff_member_required
@capability_required(CAP_MANAGE_BACKUPS)
@require_POST
def backup_run(request):
    try:
        create_automatic_backup_task.delay()
    except Exception:
        create_system_backup()
    messages.success(request, "Backup solicitado. Se guardara en el directorio configurado.")
    return redirect("admin_backup_center")


@staff_member_required
@capability_required(CAP_MANAGE_INTEGRATIONS)
def webhook_center(request):
    company = get_active_company(request)
    if not company:
        return redirect("select_company")

    if request.method == "POST":
        action = str(request.POST.get("action", "create") or "create").strip()
        if action == "create":
            endpoint = WebhookEndpoint(
                company=company,
                name=str(request.POST.get("name", "")).strip(),
                target_url=str(request.POST.get("target_url", "")).strip(),
                events=request.POST.getlist("events"),
                created_by=request.user,
            )
            try:
                endpoint.save()
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
            else:
                request.session["new_webhook_secret"] = {
                    "endpoint_id": endpoint.pk,
                    "name": endpoint.name,
                    "secret": endpoint.secret,
                }
                messages.success(request, "Webhook creado correctamente.")
        else:
            endpoint = get_object_or_404(WebhookEndpoint, pk=request.POST.get("endpoint_id"), company=company)
            if action == "toggle":
                endpoint.is_active = not endpoint.is_active
                endpoint.save(update_fields=["is_active", "updated_at"])
                messages.success(request, "Estado del webhook actualizado.")
            elif action == "rotate":
                endpoint.secret = generate_webhook_secret()
                endpoint.save(update_fields=["secret", "updated_at"])
                request.session["new_webhook_secret"] = {
                    "endpoint_id": endpoint.pk,
                    "name": endpoint.name,
                    "secret": endpoint.secret,
                }
                messages.success(request, "Secreto rotado. Actualiza el sistema receptor.")
            elif action == "delete":
                endpoint.delete()
                messages.success(request, "Webhook eliminado.")
        return redirect("admin_webhook_center")

    endpoints = list(WebhookEndpoint.objects.filter(company=company).order_by("name", "id"))
    latest_deliveries = WebhookDelivery.objects.filter(endpoint__company=company).select_related("endpoint")[:50]
    return render(
        request,
        "admin_panel/system/webhooks.html",
        {
            "endpoints": endpoints,
            "event_choices": WebhookEndpoint.EVENT_CHOICES,
            "latest_deliveries": latest_deliveries,
            "new_webhook_secret": request.session.pop("new_webhook_secret", None),
        },
    )


__all__ = ["backup_center", "backup_run", "webhook_center"]
