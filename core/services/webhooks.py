"""Signed webhook outbox and delivery helpers."""

import hashlib
import hmac
import ipaddress
import json
import logging
import socket
import uuid
from datetime import timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.models import WebhookDelivery, WebhookEndpoint

logger = logging.getLogger(__name__)


class _RejectRedirects(HTTPRedirectHandler):
    """Do not let a public webhook URL redirect a worker into a private network."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_webhook_opener = build_opener(_RejectRedirects())


def _validate_delivery_target(target_url):
    parsed = urlsplit(target_url)
    if not parsed.hostname:
        raise ValueError("El webhook no tiene un host valido.")
    if getattr(settings, "WEBHOOK_ALLOW_PRIVATE_TARGETS", False):
        return
    addresses = {
        row[4][0]
        for row in socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    }
    for raw_address in addresses:
        address = ipaddress.ip_address(raw_address)
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        ):
            raise ValueError("El webhook apunta a una red privada o reservada.")


def enqueue_webhook_event(*, company, event_type, data):
    """Persist one delivery per subscribed endpoint and dispatch after commit."""
    if not company:
        return []
    valid_events = {value for value, _label in WebhookEndpoint.EVENT_CHOICES}
    if event_type not in valid_events:
        raise ValueError("Evento de webhook no reconocido.")

    event_id = uuid.uuid4()
    created_at = timezone.now()
    envelope = {
        "id": str(event_id),
        "type": event_type,
        "created_at": created_at.isoformat(),
        "company": {"id": company.pk, "slug": company.slug, "name": company.name},
        "data": data,
    }
    endpoints = [
        endpoint
        for endpoint in WebhookEndpoint.objects.filter(company=company, is_active=True)
        if event_type in (endpoint.events or [])
    ]
    deliveries = WebhookDelivery.objects.bulk_create(
        [
            WebhookDelivery(
                endpoint=endpoint,
                event_id=event_id,
                event_type=event_type,
                payload=envelope,
            )
            for endpoint in endpoints
        ]
    )
    delivery_ids = [delivery.pk for delivery in deliveries if delivery.pk]
    if delivery_ids:
        def dispatch():
            from core.tasks import deliver_webhook_task

            for delivery_id in delivery_ids:
                try:
                    deliver_webhook_task.delay(delivery_id)
                except Exception:
                    logger.exception("No se pudo encolar el webhook %s; queda pendiente.", delivery_id)

        transaction.on_commit(dispatch)
    return deliveries


def deliver_webhook(delivery_id):
    delivery = WebhookDelivery.objects.select_related("endpoint").filter(pk=delivery_id).first()
    if not delivery:
        return {"status": "missing"}
    if delivery.status == WebhookDelivery.STATUS_DELIVERED:
        return {"status": "delivered", "attempts": delivery.attempts_count}

    endpoint = delivery.endpoint
    max_attempts = max(int(getattr(settings, "WEBHOOK_MAX_ATTEMPTS", 6)), 1)
    timeout = max(int(getattr(settings, "WEBHOOK_TIMEOUT_SECONDS", 10)), 1)
    attempt_number = delivery.attempts_count + 1
    body = json.dumps(delivery.payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    timestamp = str(int(timezone.now().timestamp()))
    signed_content = timestamp.encode("ascii") + b"." + body
    signature = hmac.new(endpoint.secret.encode("utf-8"), signed_content, hashlib.sha256).hexdigest()
    status_code = None
    response_excerpt = ""
    error_message = ""

    try:
        if not endpoint.is_active:
            raise ValueError("El webhook esta desactivado.")
        _validate_delivery_target(endpoint.target_url)
        request = Request(
            endpoint.target_url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "User-Agent": "FLEXS-Webhooks/1.0",
                "X-FLEXS-Event": delivery.event_type,
                "X-FLEXS-Event-ID": str(delivery.event_id),
                "X-FLEXS-Timestamp": timestamp,
                "X-FLEXS-Signature": f"sha256={signature}",
            },
        )
        with _webhook_opener.open(request, timeout=timeout) as response:
            status_code = int(response.status)
            response_excerpt = response.read(500).decode("utf-8", errors="replace")
        if not 200 <= status_code < 300:
            raise ValueError(f"Respuesta HTTP {status_code}")
    except HTTPError as exc:
        status_code = int(exc.code)
        response_excerpt = exc.read(500).decode("utf-8", errors="replace")
        error_message = f"HTTP {status_code}"
    except (URLError, OSError, ValueError) as exc:
        error_message = str(exc)[:500]

    delivery.attempts_count = attempt_number
    delivery.response_status = status_code
    delivery.response_excerpt = response_excerpt[:500]
    if not error_message and status_code is not None and 200 <= status_code < 300:
        delivery.status = WebhookDelivery.STATUS_DELIVERED
        delivery.delivered_at = timezone.now()
        delivery.next_retry_at = None
        delivery.last_error = ""
    else:
        delivery.last_error = error_message or "No se obtuvo una respuesta valida."
        if attempt_number >= max_attempts:
            delivery.status = WebhookDelivery.STATUS_FAILED
            delivery.next_retry_at = None
        else:
            delivery.status = WebhookDelivery.STATUS_PENDING
            delay_seconds = min(60 * (2 ** (attempt_number - 1)), 60 * 60)
            delivery.next_retry_at = timezone.now() + timedelta(seconds=delay_seconds)
    delivery.save(
        update_fields=[
            "attempts_count",
            "response_status",
            "response_excerpt",
            "status",
            "delivered_at",
            "next_retry_at",
            "last_error",
            "updated_at",
        ]
    )
    return {"status": delivery.status, "attempts": delivery.attempts_count}


def retry_pending_webhooks():
    max_attempts = max(int(getattr(settings, "WEBHOOK_MAX_ATTEMPTS", 6)), 1)
    ids = list(
        WebhookDelivery.objects.filter(
            status=WebhookDelivery.STATUS_PENDING,
            attempts_count__lt=max_attempts,
        )
        .filter(Q(next_retry_at__isnull=True) | Q(next_retry_at__lte=timezone.now()))
        .values_list("id", flat=True)[:200]
    )
    from core.tasks import deliver_webhook_task

    for delivery_id in ids:
        try:
            deliver_webhook_task.delay(delivery_id)
        except Exception:
            logger.exception("No se pudo reencolar el webhook %s.", delivery_id)
    return ids
