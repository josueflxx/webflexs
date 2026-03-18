import base64
import json
from decimal import Decimal
from io import BytesIO

from django.conf import settings
from django.template.loader import render_to_string
from django.utils import timezone
import qrcode

try:
    from weasyprint import HTML
except ImportError:
    HTML = None

def generate_afip_qr_data(fiscal_document):
    """
    Generate the Base64 encoded JSON data required by AFIP for the QR code.
    See: http://www.afip.gob.ar/fe/qr/
    """
    if not fiscal_document.cae:
        return ""

    doc_date = fiscal_document.issued_at.strftime("%Y-%m-%d") if fiscal_document.issued_at else ""
    
    issuer_cuit = str(fiscal_document.company.cuit).replace("-", "") if fiscal_document.company.cuit else 0

    import re
    def sanitize_cuit(value):
        return int(re.sub(r"\D+", "", str(value or ""))) if value else 0

    client_profile = fiscal_document.client_profile
    if not client_profile and hasattr(fiscal_document, 'client_company_ref'):
        client_profile = fiscal_document.client_company_ref.client_profile if fiscal_document.client_company_ref else None

    # Determine DocType based on AFIP table
    doc_type = 99
    if client_profile:
        doc_type_raw = str(getattr(client_profile, "document_type", "")).lower()
        mapping = {"cuit": 80, "cuil": 86, "dni": 96, "cdi": 87, "passport": 94}
        doc_type = mapping.get(doc_type_raw, 99)

        doc_nro = sanitize_cuit(
            getattr(client_profile, "document_number", "")
            or getattr(client_profile, "cuit_dni", "")
            or "0"
        )
    else:
        doc_nro = 0

    # AFIP document types
    cbte_tipo = 1 if fiscal_document.doc_type == "FA" else (6 if fiscal_document.doc_type == "FB" else 0)

    qr_data = {
        "ver": 1, 
        "fecha": doc_date,
        "cuit": int(issuer_cuit),
        "ptoVta": int(fiscal_document.point_of_sale.number),
        "tipoCmp": cbte_tipo,
        "nroCmp": int(fiscal_document.number) if fiscal_document.number else 0,
        "importe": float(fiscal_document.total),
        "moneda": "PES", 
        "ctz": float(getattr(fiscal_document, "exchange_rate", 1)),
        "tipoDocRec": doc_type,
        "nroDocRec": doc_nro,
        "tipoCodAut": "E", # CAE
        "codAut": int(fiscal_document.cae)
    }

    json_str = json.dumps(qr_data, separators=(",", ":"))
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


def generate_fiscal_pdf(html_string, base_url=""):
    """
    Generate PDF for the fiscal document from an HTML string. Returns the binary PDF content.
    """
    if not HTML:
        raise ImportError("WeasyPrint no esta instalado. Instala weasyprint para generar PDFs.")

    pdf_file = HTML(string=html_string, base_url=base_url).write_pdf()
    return pdf_file
