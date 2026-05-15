"""Technical parser for clamp measures used by catalog Excel exports."""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

from django.core.exceptions import ObjectDoesNotExist

from catalog.services.clamp_code import parsearCodigo


CLAMP_MEASURE_EXCEL_HEADERS = [
    "Codigo original",
    "Nombre original",
    "Precio",
]

DIAMETER_ORDER = ["7/16", "1/2", "9/16", "5/8", "3/4", "7/8", "1", "18", "20", "22", "24"]
SUPPORTED_DIAMETERS = ["11/16", *DIAMETER_ORDER]
TYPE_ORDER = {"TREFILADA": 0, "LAMINADA": 1, "FORJADA": 2}
SHAPE_ORDER = {"CURVA": 0, "S/CURVA": 1, "PLANA": 2}

_DIAMETER_ALTERNATION = "|".join(
    re.escape(value) for value in sorted(SUPPORTED_DIAMETERS, key=len, reverse=True)
)
_DIMENSIONS_RE = re.compile(
    rf"(?:\bDE\s+)?(?P<diameter>{_DIAMETER_ALTERNATION})\s*X\s*"
    rf"(?P<width>\d{{1,4}})\s*X\s*(?P<length>\d{{1,4}})\b"
)


@dataclass
class ClampMeasureParseResult:
    codigo_original: str
    nombre_original: str
    tipo: str = ""
    diametro: str = ""
    ancho: int | None = None
    largo: int | None = None
    forma: str = ""
    nombre_normalizado: str = ""
    observaciones: str = ""
    precio: float | None = None

    def as_excel_row(self):
        return [
            self.codigo_original,
            self.nombre_original,
            self.precio if self.precio is not None else "",
        ]


def _strip_accents(value):
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def normalize_measure_text(value):
    text = _strip_accents(value).upper().strip()
    text = text.replace("×", " X ").replace("*", " X ")
    text = re.sub(r"(?<=\d)\s*[X]\s*(?=\d)", " X ", text)
    text = re.sub(r"\s+", " ", text)
    return text


def normalize_shape(value):
    text = normalize_measure_text(value)
    if not text:
        return ""
    if re.search(r"\b(S\s*/\s*CURVA|S\s*-?\s*CURVA|SEMI\s*CURVA|SEMICURVA)\b", text):
        return "S/CURVA"
    if re.search(r"\bPLANO\b|\bPLANA\b", text):
        return "PLANA"
    if re.search(r"\bCURVO\b|\bCURVA\b", text):
        return "CURVA"
    if text == "S":
        return "S/CURVA"
    if text == "P":
        return "PLANA"
    if text == "C":
        return "CURVA"
    return ""


def normalize_type(value):
    text = normalize_measure_text(value)
    if re.search(r"\bLAMINAD[AO]\b", text):
        return "LAMINADA"
    if re.search(r"\bTREFILAD[AO]\b", text):
        return "TREFILADA"
    if re.search(r"\bFORJAD[AO]\b", text):
        return "FORJADA"
    return ""


def _parse_name_values(name):
    text = normalize_measure_text(name)
    values = {
        "tipo": normalize_type(text),
        "diametro": "",
        "ancho": None,
        "largo": None,
        "forma": normalize_shape(text),
    }
    match = _DIMENSIONS_RE.search(text)
    if match:
        values["diametro"] = match.group("diameter")
        values["ancho"] = int(match.group("width"))
        values["largo"] = int(match.group("length"))
    return values


def _parse_code_values(code):
    try:
        parsed = parsearCodigo(code)
    except (TypeError, ValueError):
        return {}
    return {
        "tipo": normalize_type(parsed.get("tipo")),
        "diametro": str(parsed.get("diametro") or "").strip(),
        "ancho": parsed.get("ancho"),
        "largo": parsed.get("largo"),
        "forma": normalize_shape(parsed.get("forma")),
    }


def _parse_specs_values(specs):
    if not specs:
        return {}
    return {
        "tipo": normalize_type(getattr(specs, "fabrication", "")),
        "diametro": str(getattr(specs, "diameter", "") or "").strip(),
        "ancho": getattr(specs, "width", None),
        "largo": getattr(specs, "length", None),
        "forma": normalize_shape(getattr(specs, "shape", "")),
    }


def _same_value(field, left, right):
    if left in (None, "") or right in (None, ""):
        return True
    if field in {"ancho", "largo"}:
        try:
            return int(left) == int(right)
        except (TypeError, ValueError):
            return str(left).strip() == str(right).strip()
    return normalize_measure_text(left) == normalize_measure_text(right)


def _append_observation(observations, text):
    if text and text not in observations:
        observations.append(text)


def _build_normalized_name(values):
    required = ["tipo", "diametro", "ancho", "largo", "forma"]
    if any(values.get(field) in (None, "") for field in required):
        return ""
    return (
        f"ABRAZADERA {values['tipo']} {values['diametro']} X "
        f"{values['ancho']} X {values['largo']} {values['forma']}"
    )


def parse_clamp_measure(code, name, specs=None):
    name_values = _parse_name_values(name)
    values = dict(name_values)
    sources = {
        field: "nombre"
        for field, value in values.items()
        if value not in (None, "")
    }
    observations = []

    fallback_sources = [
        ("ficha tecnica", _parse_specs_values(specs)),
        ("codigo", _parse_code_values(code)),
    ]

    for source_name, source_values in fallback_sources:
        used_source = False
        for field, source_value in source_values.items():
            if source_value in (None, ""):
                continue
            current_value = values.get(field)
            if current_value in (None, ""):
                values[field] = source_value
                sources[field] = source_name
                used_source = True
            elif not _same_value(field, current_value, source_value):
                _append_observation(
                    observations,
                    f"{source_name.capitalize()} no coincide en {field}",
                )
        if used_source:
            _append_observation(observations, f"Datos completados desde {source_name}")

    missing = [
        label
        for field, label in (
            ("tipo", "tipo"),
            ("diametro", "diametro"),
            ("ancho", "ancho"),
            ("largo", "largo"),
            ("forma", "forma"),
        )
        if values.get(field) in (None, "")
    ]
    if missing:
        _append_observation(observations, "Revisar: falta " + ", ".join(missing))
    elif sources and all(source == "nombre" for source in sources.values()):
        _append_observation(observations, "Detectado desde nombre")

    normalized_name = _build_normalized_name(values)

    return ClampMeasureParseResult(
        codigo_original=str(code or ""),
        nombre_original=str(name or ""),
        tipo=values.get("tipo") or "",
        diametro=str(values.get("diametro") or ""),
        ancho=values.get("ancho"),
        largo=values.get("largo"),
        forma=values.get("forma") or "",
        nombre_normalizado=normalized_name,
        observaciones="; ".join(observations),
    )


def parse_product_clamp_measure(product, price=None):
    try:
        specs = product.clamp_specs
    except ObjectDoesNotExist:
        specs = None
    result = parse_clamp_measure(product.sku, product.name, specs=specs)
    result.precio = price
    return result


def clamp_measure_group_label(result):
    return result.diametro if result.diametro else "PARA REVISAR"


def clamp_measure_group_sort_key(result):
    if result.diametro in DIAMETER_ORDER:
        return (0, DIAMETER_ORDER.index(result.diametro), result.diametro)
    if result.diametro:
        return (1, 999, result.diametro)
    return (2, 999, "PARA REVISAR")


def clamp_measure_result_sort_key(result):
    return (
        clamp_measure_group_sort_key(result),
        TYPE_ORDER.get(result.tipo, 99),
        result.ancho if result.ancho is not None else 999999,
        result.largo if result.largo is not None else 999999,
        SHAPE_ORDER.get(result.forma, 99),
        normalize_measure_text(result.nombre_original),
        result.codigo_original,
    )


def sort_clamp_measure_results(results):
    return sorted(results, key=clamp_measure_result_sort_key)
