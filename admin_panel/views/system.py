"""Operational pages for backups and integrations."""

from django.contrib import messages
from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.http import FileResponse, Http404
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from core.models import WebhookDelivery, WebhookEndpoint, generate_webhook_secret
from core.services.authorization import (
    CAP_MANAGE_BACKUPS,
    CAP_MANAGE_INTEGRATIONS,
    capability_required,
)
from core.services.backups import BackupError, get_backup_dashboard, get_backup_download, request_backup
from core.services.company_context import get_active_company
from core.tasks import create_automatic_backup_task


@staff_member_required
@capability_required(CAP_MANAGE_BACKUPS)
@never_cache
def backup_center(request):
    if request.GET.get('download'):
        try:
            path = get_backup_download(request.GET['download'], request.GET.get('file', ''))
        except (BackupError, OSError, ValueError) as exc:
            raise Http404('No se encontró el archivo solicitado.') from exc
        response = FileResponse(path.open('rb'), as_attachment=True, filename=path.name)
        response['Cache-Control'] = 'private, no-store'
        response['X-Content-Type-Options'] = 'nosniff'
        return response
    try:
        context = get_backup_dashboard()
    except (BackupError, OSError, ValueError):
        context = {'backup_storage_error': True}
    return render(
        request,
        "admin_panel/system/backups.html",
        context,
    )


@staff_member_required
@capability_required(CAP_MANAGE_BACKUPS)
@require_POST
def backup_run(request):
    try:
        action = request.POST.get('action', 'backup')
        if action not in {'backup', 'verify'}:
            raise BackupError('La operación solicitada no es válida.')
        row = request_backup(
            kind=action, manifest_name=request.POST.get('manifest', ''),
            requested_by=request.user.get_username(),
        )
        if settings.BACKUP_EXECUTION_MODE == 'celery':
            try:
                create_automatic_backup_task.delay(row['id'])
            except Exception:
                messages.warning(request, 'La solicitud quedó guardada, pero el ejecutor no respondió. Revisá el estado del servicio.')
                return redirect('admin_backup_center')
        messages.info(request, 'Solicitud guardada. El panel mostrará el resultado cuando termine; se actualiza automáticamente.')
    except (BackupError, OSError, ValueError) as exc:
        messages.error(request, str(exc) if isinstance(exc, BackupError) else 'No se pudo guardar la solicitud. Revisá los permisos de la carpeta de backups.')
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
