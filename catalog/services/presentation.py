"""Public display helpers. These never change stored prices or dimensions."""
from fractions import Fraction
from urllib.parse import urlsplit


def diameter_label(value):
    text = str(value or "").strip()
    if not text:
        return "Sin especificar"
    try:
        number = sum(Fraction(part) for part in text.replace(",", ".").split())
    except (ValueError, ZeroDivisionError):
        return f"{text} (unidad sin confirmar)"
    # The catalog's established notation uses fractions and 1 for inches,
    # and the listed integer bar diameters for millimetres. Do not guess others.
    if "/" in text or text == "1":
        return f"{text}″ (pulgadas)"
    if number in {6, 8, 10, 12, 14, 16, 18, 20, 22, 24, 25, 27, 30, 32, 36, 40}:
        return f"{text} mm"
    return f"{text} (unidad sin confirmar)"


def safe_catalog_return_url(value):
    """Only relative public catalog/brand locations are accepted as return links."""
    value = str(value or "")
    if any(char in value for char in ("\\", "\r", "\n")):
        return "/catalogo/"
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "/catalogo/"
    if parsed.scheme or parsed.netloc:
        return "/catalogo/"
    if parsed.path == "/catalogo/" or parsed.path.startswith("/catalogo/marcas/"):
        return value
    return "/catalogo/"
