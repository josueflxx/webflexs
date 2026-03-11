"""Fiscal readiness helpers (pre-ARCA)."""
from typing import List, Tuple

from core.models import FiscalPointOfSale


def _is_blank(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


def is_invoice_ready(order) -> Tuple[bool, List[str]]:
    """
    Validate if an order is ready for fiscal invoicing.

    Returns:
        (is_ready, errors)
    """
    errors: List[str] = []
    if not order:
        return False, ["Pedido invalido."]

    company = getattr(order, "company", None)
    if not company:
        errors.append("Pedido sin empresa asignada.")
    client_company = getattr(order, "client_company_ref", None)
    if not client_company:
        errors.append("Pedido sin cliente empresa.")

    if company and client_company and client_company.company_id != company.id:
        errors.append("La empresa del pedido no coincide con el cliente empresa.")

    allowed_statuses = {
        getattr(order, "STATUS_CONFIRMED", "confirmed"),
        getattr(order, "STATUS_PREPARING", "preparing"),
        getattr(order, "STATUS_SHIPPED", "shipped"),
        getattr(order, "STATUS_DELIVERED", "delivered"),
    }
    if getattr(order, "status", None) not in allowed_statuses:
        errors.append("El pedido no esta en estado facturable.")

    if company:
        company_requirements = [
            ("legal_name", "Razon social de la empresa"),
            ("cuit", "CUIT de la empresa"),
            ("tax_condition", "Condicion fiscal de la empresa"),
            ("fiscal_address", "Domicilio fiscal de la empresa"),
            ("fiscal_city", "Localidad fiscal de la empresa"),
            ("fiscal_province", "Provincia fiscal de la empresa"),
            ("postal_code", "Codigo postal de la empresa"),
            ("point_of_sale_default", "Punto de venta de la empresa"),
        ]
        for field, label in company_requirements:
            if _is_blank(getattr(company, field, "")):
                errors.append(f"Falta {label}.")

    client_profile = None
    if client_company:
        client_profile = getattr(client_company, "client_profile", None)
    if not client_profile and getattr(order, "user_id", None):
        client_profile = getattr(order.user, "client_profile", None)
    if not client_profile:
        errors.append("Pedido sin perfil de cliente asociado.")
    else:
        client_requirements = [
            ("document_type", "Tipo de documento del cliente"),
            ("document_number", "Numero de documento del cliente"),
            ("iva_condition", "Condicion IVA del cliente"),
            ("fiscal_address", "Domicilio fiscal del cliente"),
            ("fiscal_city", "Localidad fiscal del cliente"),
            ("fiscal_province", "Provincia fiscal del cliente"),
            ("postal_code", "Codigo postal del cliente"),
        ]
        for field, label in client_requirements:
            if _is_blank(getattr(client_profile, field, "")):
                errors.append(f"Falta {label}.")

    return len(errors) == 0, errors


def is_company_fiscal_ready(company) -> Tuple[bool, List[str]]:
    """Validate if a company is minimally ready for fiscal emission flows."""
    errors: List[str] = []
    if not company:
        return False, ["Empresa invalida."]

    company_requirements = [
        ("legal_name", "Razon social de la empresa"),
        ("cuit", "CUIT de la empresa"),
        ("tax_condition", "Condicion fiscal de la empresa"),
        ("fiscal_address", "Domicilio fiscal de la empresa"),
        ("fiscal_city", "Localidad fiscal de la empresa"),
        ("fiscal_province", "Provincia fiscal de la empresa"),
        ("postal_code", "Codigo postal de la empresa"),
        ("point_of_sale_default", "Punto de venta default de la empresa"),
    ]
    for field, label in company_requirements:
        if _is_blank(getattr(company, field, "")):
            errors.append(f"Falta {label}.")

    points = FiscalPointOfSale.objects.filter(company=company)
    active_points = points.filter(is_active=True)
    default_point = points.filter(is_default=True).first()

    if not active_points.exists():
        errors.append("No existe ningun punto de venta fiscal activo.")
    if not default_point:
        errors.append("No existe punto de venta fiscal default.")
    else:
        if not default_point.is_active:
            errors.append("El punto de venta fiscal default esta inactivo.")
        company_default = str(getattr(company, "point_of_sale_default", "") or "").strip()
        if company_default and default_point.number != company_default:
            errors.append(
                "El punto de venta default de la empresa no coincide con el POS default marcado."
            )

    return len(errors) == 0, errors
