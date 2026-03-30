"""Email notifications for fiscal documents."""

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from core.models import (
    FISCAL_STATUS_AUTHORIZED,
    FISCAL_STATUS_EXTERNAL_RECORDED,
)


def _resolve_recipient(document):
    client_profile = getattr(document, "client_profile", None)
    if not client_profile and getattr(document, "client_company_ref", None):
        client_profile = document.client_company_ref.client_profile
    if not client_profile or not getattr(client_profile, "user_id", None):
        return ""
    return str(getattr(client_profile.user, "email", "") or "").strip()


def send_fiscal_document_email(*, fiscal_document, actor=None, force=False):
    """
    Send a fiscal document email to the client contact.
    Stores delivery trace on FiscalDocument even on error.
    """
    if not fiscal_document:
        return False, "Comprobante fiscal invalido."
    if fiscal_document.status not in {FISCAL_STATUS_AUTHORIZED, FISCAL_STATUS_EXTERNAL_RECORDED}:
        return False, "El comprobante debe estar emitido/cerrado antes de enviar por email."

    recipient = _resolve_recipient(fiscal_document)
    if not recipient:
        fiscal_document.email_last_error = "El cliente no tiene email configurado."
        fiscal_document.save(update_fields=["email_last_error", "updated_at"])
        return False, fiscal_document.email_last_error

    if (
        not force
        and fiscal_document.email_last_sent_at
        and fiscal_document.email_last_recipient.lower() == recipient.lower()
    ):
        return True, "El comprobante ya fue enviado a este destinatario."

    subject = (
        f"{fiscal_document.company.name} | "
        f"{fiscal_document.commercial_type_label} {fiscal_document.display_number}"
    )
    issued_at = fiscal_document.issued_at or fiscal_document.created_at
    due_text = fiscal_document.payment_due_date.strftime("%d/%m/%Y") if fiscal_document.payment_due_date else "-"
    body_lines = [
        f"Hola,",
        "",
        "Adjuntamos/confirmamos el comprobante fiscal de su operacion.",
        "",
        f"Comprobante: {fiscal_document.commercial_type_label}",
        f"Numero: {fiscal_document.display_number}",
        f"Fecha: {issued_at.strftime('%d/%m/%Y %H:%M')}",
        f"Total: ${fiscal_document.total:.2f}",
        f"Vencimiento: {due_text}",
        "",
        "Si necesitas una copia adicional o detalle de items, responde este email.",
        "",
        f"{fiscal_document.company.name}",
    ]
    body = "\n".join(body_lines)
    from_email = (
        str(getattr(settings, "DEFAULT_FROM_EMAIL", "") or "").strip()
        or str(getattr(settings, "EMAIL_HOST_USER", "") or "").strip()
        or "no-reply@localhost"
    )

    try:
        sent = send_mail(
            subject=subject,
            message=body,
            from_email=from_email,
            recipient_list=[recipient],
            fail_silently=False,
        )
        if sent:
            fiscal_document.email_last_sent_at = timezone.now()
            fiscal_document.email_last_recipient = recipient
            fiscal_document.email_last_error = ""
            fiscal_document.save(
                update_fields=[
                    "email_last_sent_at",
                    "email_last_recipient",
                    "email_last_error",
                    "updated_at",
                ]
            )
            return True, "Email enviado correctamente."
        fiscal_document.email_last_error = "El backend de email no confirmo envio."
        fiscal_document.save(update_fields=["email_last_error", "updated_at"])
        return False, fiscal_document.email_last_error
    except Exception as exc:
        fiscal_document.email_last_error = str(exc)[:255]
        fiscal_document.save(update_fields=["email_last_error", "updated_at"])
        return False, fiscal_document.email_last_error
