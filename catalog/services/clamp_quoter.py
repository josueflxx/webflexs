"""
Shared clamp quoter logic for admin and client-facing flows.
"""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from catalog.services.clamp_code import generarCodigo


CLAMP_WEIGHT_MAP = {
    "7/16": Decimal("0.76"),
    "1/2": Decimal("0.993"),
    "9/16": Decimal("1.258"),
    "5/8": Decimal("1.553"),
    "3/4": Decimal("2.236"),
    "7/8": Decimal("3.043"),
    "1": Decimal("3.975"),
    "18": Decimal("1.92"),
    "20": Decimal("2.5"),
    "22": Decimal("3.043"),
    "24": Decimal("3.8"),
}

CLAMP_LAMINATED_ALLOWED_DIAMETERS = ("3/4", "1", "7/8")

CLAMP_PROFILE_ADJUSTMENTS = {
    "PLANA": Decimal("20"),
    "SEMICURVA": Decimal("10"),
    "CURVA": Decimal("0"),
}

CLAMP_PRICE_LISTS = [
    ("lista_1", "Lista 1", Decimal("1.4")),
    ("lista_2", "Lista 2", Decimal("1.5")),
    ("lista_3", "Lista 3", Decimal("1.6")),
    ("lista_4", "Lista 4", Decimal("1.7")),
    ("facturacion", "Facturacion", Decimal("2.0")),
]


def get_allowed_diameter_options(clamp_type=None):
    """
    Return valid diameter options per clamp type.
    - Trefilada: all known diameters
    - Laminada: restricted business subset
    """
    all_options = list(CLAMP_WEIGHT_MAP.keys())
    normalized_type = str(clamp_type or "").strip().lower()
    if normalized_type == "laminada":
        return [diameter for diameter in CLAMP_LAMINATED_ALLOWED_DIAMETERS if diameter in CLAMP_WEIGHT_MAP]
    return all_options


def parse_decimal_value(value, field_label, min_value=Decimal("0")):
    raw = str(value if value is not None else "").strip().replace(",", ".")
    if raw == "":
        raise ValueError(f"{field_label} es obligatorio.")
    try:
        parsed = Decimal(raw)
    except (InvalidOperation, ValueError):
        raise ValueError(f"{field_label} es invalido.")
    if parsed < min_value:
        raise ValueError(f"{field_label} no puede ser menor a {min_value}.")
    return parsed


def parse_int_value(value, field_label, min_value=0):
    raw = str(value if value is not None else "").strip()
    if raw == "":
        raise ValueError(f"{field_label} es obligatorio.")
    if not raw.isdigit():
        raise ValueError(f"{field_label} debe ser numerico.")
    parsed = int(raw)
    if parsed < min_value:
        raise ValueError(f"{field_label} no puede ser menor a {min_value}.")
    return parsed


def build_clamp_description(clamp_type, is_zincated, diameter, width_mm, length_mm, profile_type):
    clamp_label = "TREFILADA" if clamp_type == "trefilada" else "LAMINADA"
    zinc_label = " ZINCADA" if is_zincated else ""
    return (
        f"ABRAZADERA {clamp_label}{zinc_label} "
        f"DE {diameter} X {width_mm} X {length_mm} {profile_type}"
    )


def calculate_clamp_quote(payload):
    """
    Clamp quoter business logic.
    Returns normalized inputs + calculation outputs.
    """
    clamp_type = str(payload.get("clamp_type", "trefilada")).strip().lower()
    profile_type = str(payload.get("profile_type", "PLANA")).strip().upper()
    diameter = str(payload.get("diameter", "")).strip()
    is_zincated = str(payload.get("is_zincated", "")).strip().lower() in {"1", "true", "on", "yes"}

    if clamp_type not in {"trefilada", "laminada"}:
        raise ValueError("Tipo de abrazadera invalido.")
    if profile_type not in CLAMP_PROFILE_ADJUSTMENTS:
        raise ValueError("Tipo invalido. Usa PLANA, SEMICURVA o CURVA.")
    if diameter not in CLAMP_WEIGHT_MAP:
        raise ValueError("Diametro invalido.")
    if clamp_type == "laminada" and diameter not in CLAMP_LAMINATED_ALLOWED_DIAMETERS:
        raise ValueError("Para abrazaderas laminadas solo se permiten diametros 3/4, 1 y 7/8.")

    dollar_rate = parse_decimal_value(payload.get("dollar_rate"), "Dolar", min_value=Decimal("0.0001"))
    steel_price_usd = parse_decimal_value(
        payload.get("steel_price_usd"),
        "Precio Acero (USD)",
        min_value=Decimal("0.0001"),
    )
    supplier_discount_pct = parse_decimal_value(
        payload.get("supplier_discount_pct", "0"),
        "Desc. Proveedor (%)",
        min_value=Decimal("0"),
    )
    general_increase_pct = parse_decimal_value(
        payload.get("general_increase_pct", "23"),
        "Aumento Gral. (%)",
        min_value=Decimal("0"),
    )
    width_mm = parse_int_value(payload.get("width_mm"), "Ancho (mm)", min_value=1)
    length_mm = parse_int_value(payload.get("length_mm"), "Largo (mm)", min_value=1)

    adjustment = CLAMP_PROFILE_ADJUSTMENTS[profile_type]
    development_meters = (Decimal(length_mm * 2) + Decimal(width_mm) + adjustment) / Decimal("1000")
    weight_per_meter = CLAMP_WEIGHT_MAP[diameter]
    total_weight_kg = development_meters * weight_per_meter

    discount_factor = Decimal("1") - (supplier_discount_pct / Decimal("100"))
    increase_factor = Decimal("1") + (general_increase_pct / Decimal("100"))
    base_cost = total_weight_kg * steel_price_usd * discount_factor * dollar_rate * increase_factor

    if is_zincated:
        base_cost = base_cost * Decimal("1.20")
    if clamp_type == "laminada":
        base_cost = base_cost * Decimal("2.0")

    base_cost = base_cost.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    description = build_clamp_description(
        clamp_type=clamp_type,
        is_zincated=is_zincated,
        diameter=diameter,
        width_mm=width_mm,
        length_mm=length_mm,
        profile_type=profile_type,
    )

    code_generation = generarCodigo(
        tipo=clamp_type.upper(),
        diametro=diameter,
        ancho=width_mm,
        largo=length_mm,
        forma=profile_type,
        with_metadata=True,
    )

    price_rows = []
    for key, label, multiplier in CLAMP_PRICE_LISTS:
        final_price = (base_cost * multiplier).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        price_rows.append(
            {
                "key": key,
                "label": label,
                "multiplier": multiplier,
                "final_price": final_price,
            }
        )

    return {
        "inputs": {
            "client_name": str(payload.get("client_name", "")).strip(),
            "dollar_rate": dollar_rate,
            "steel_price_usd": steel_price_usd,
            "supplier_discount_pct": supplier_discount_pct,
            "general_increase_pct": general_increase_pct,
            "clamp_type": clamp_type,
            "is_zincated": is_zincated,
            "diameter": diameter,
            "width_mm": width_mm,
            "length_mm": length_mm,
            "profile_type": profile_type,
        },
        "base_cost": base_cost,
        "description": description,
        "generated_code": code_generation["codigo"],
        "generated_code_metadata": code_generation,
        "development_meters": development_meters.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP),
        "total_weight_kg": total_weight_kg.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP),
        "price_rows": price_rows,
    }
