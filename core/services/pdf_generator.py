import base64
import json
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote, urlsplit

import qrcode
from django.conf import settings
from django.contrib.staticfiles import finders

def _digits(value):
    return "".join(char for char in str(value or "") if char.isdigit())


def _decimal_json_number(value):
    number = Decimal(str(value))
    if not number.is_finite() or number < 0:
        raise ValueError("El QR requiere un decimal fiscal no negativo.")
    rendered = format(number, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def generate_afip_qr_data(fiscal_document):
    """Build a deterministic QR exclusively from immutable fiscal evidence."""
    from core.models import (
        FISCAL_AUTHORIZED_STATUSES,
        FiscalPointOfSale,
    )
    from core.services.fiscal_integrity import fiscal_payload_hash

    snapshot = getattr(fiscal_document, "fiscal_snapshot", None)
    if not isinstance(snapshot, dict) or not snapshot:
        return ""
    if (
        not fiscal_document.snapshot_hash
        or fiscal_payload_hash(snapshot) != fiscal_document.snapshot_hash
    ):
        return ""
    if fiscal_document.status not in FISCAL_AUTHORIZED_STATUSES:
        return ""
    if not all(
        [
            fiscal_document.issued_at,
            fiscal_document.number is not None,
            fiscal_document.cae,
            fiscal_document.cae_due_date,
        ]
    ):
        return ""

    emitter = snapshot.get("emitter")
    client = snapshot.get("client")
    emitter = emitter if isinstance(emitter, dict) else {}
    client = client if isinstance(client, dict) else {}

    environment = str(
        fiscal_document.environment_snapshot
        or emitter.get("environment")
        or ""
    )
    if environment not in {
        FiscalPointOfSale.ENV_HOMOLOGATION,
        FiscalPointOfSale.ENV_PRODUCTION,
    }:
        return ""

    issuer_cuit = _digits(
        fiscal_document.issuer_cuit_snapshot
        or emitter.get("cuit")
    )
    point_of_sale = _digits(
        fiscal_document.point_of_sale_number_snapshot
        or emitter.get("point_of_sale")
    )
    cae = _digits(fiscal_document.cae)
    if len(issuer_cuit) != 11 or not point_of_sale or not cae:
        return ""

    receiver_doc_type_map = {
        "cuit": 80,
        "cuil": 86,
        "dni": 96,
        "cdi": 87,
        "passport": 94,
    }
    receiver_doc_type = receiver_doc_type_map.get(
        str(client.get("document_type", "") or "").lower(),
        99,
    )
    receiver_doc_number = _digits(client.get("document_number")) or "0"

    voucher_type_map = {
        "FA": 1,
        "FB": 6,
        "FC": 11,
        "NCA": 3,
        "NCB": 8,
        "NCC": 13,
        "NDA": 2,
        "NDB": 7,
        "NDC": 12,
    }
    voucher_type = voucher_type_map.get(fiscal_document.doc_type)
    if voucher_type is None:
        return ""

    currency = str(fiscal_document.currency or "").upper()
    currency_code = "PES" if currency in {"ARS", "PES"} else currency
    if len(currency_code) != 3:
        return ""

    json_str = (
        '{"ver":1'
        f',"fecha":{json.dumps(fiscal_document.issued_at.date().isoformat())}'
        f',"cuit":{int(issuer_cuit)}'
        f',"ptoVta":{int(point_of_sale)}'
        f',"tipoCmp":{voucher_type}'
        f',"nroCmp":{int(fiscal_document.number)}'
        f',"importe":{_decimal_json_number(fiscal_document.total)}'
        f',"moneda":{json.dumps(currency_code)}'
        f',"ctz":{_decimal_json_number(fiscal_document.exchange_rate)}'
        f',"tipoDocRec":{receiver_doc_type}'
        f',"nroDocRec":{int(receiver_doc_number)}'
        ',"tipoCodAut":"E"'
        f',"codAut":{int(cae)}'
        "}"
    )
    encoded = base64.b64encode(json_str.encode("utf-8")).decode("utf-8")
    return f"https://www.afip.gob.ar/fe/qr/?p={encoded}"


def generate_qr_image_base64(qr_url):
    """
    Generate a base64 inline PNG representation of the QR code.
    """
    if not qr_url:
        return ""
    qr = qrcode.QRCode(version=1, box_size=4, border=1)
    qr.add_data(qr_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("utf-8")


def _django_asset_url_fetcher(url):
    """Resolve Django static/media assets locally before considering HTTP."""
    from weasyprint import default_url_fetcher

    parsed = urlsplit(str(url))
    request_path = unquote(parsed.path or "")
    static_prefix = urlsplit(str(settings.STATIC_URL or "/static/")).path
    media_prefix = urlsplit(str(settings.MEDIA_URL or "/media/")).path

    if static_prefix and request_path.startswith(static_prefix):
        relative_path = request_path[len(static_prefix):].lstrip("/")
        static_path = finders.find(relative_path)
        if static_path:
            return default_url_fetcher(Path(static_path).resolve().as_uri())

    if media_prefix and request_path.startswith(media_prefix):
        relative_path = request_path[len(media_prefix):].lstrip("/")
        media_root = Path(settings.MEDIA_ROOT).resolve()
        media_path = (media_root / relative_path).resolve()
        try:
            media_path.relative_to(media_root)
        except ValueError:
            media_path = None
        if media_path and media_path.is_file():
            return default_url_fetcher(media_path.as_uri())

    return default_url_fetcher(url)


def generate_document_pdf(html_string, base_url=""):
    """
    Generate a PDF from an HTML string. Returns binary PDF content.
    """
    try:
        from weasyprint import HTML
    except (ImportError, OSError) as exc:
        raise ImportError(
            "WeasyPrint o sus librerias nativas no estan disponibles."
        ) from exc

    pdf_file = HTML(
        string=html_string,
        base_url=base_url,
        url_fetcher=_django_asset_url_fetcher,
    ).write_pdf()
    return pdf_file


def generate_fiscal_pdf(html_string, base_url=""):
    """
    Backward-compatible alias for fiscal document PDF generation.
    """
    return generate_document_pdf(html_string, base_url=base_url)
