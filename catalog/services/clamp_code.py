"""
Clamp code parser/generator for ABRAZADERAS.

Rules:
- Prefix: ABL (laminada) / ABT (trefilada)
- Numeric core: [medida_compactada][ancho][largo]
- Suffix: C/P/S (CURVA/PLANA/SEMICURVA)

The measurement compact segment has variable length.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple
import re


PREFIX_TO_TYPE = {
    "ABL": "LAMINADA",
    "ABT": "TREFILADA",
}

TYPE_TO_PREFIX = {
    "ABL": "ABL",
    "ABT": "ABT",
    "LAMINADA": "ABL",
    "TREFILADA": "ABT",
}

SHAPE_CODE_TO_NAME = {
    "C": "CURVA",
    "P": "PLANA",
    "S": "SEMICURVA",
}

SHAPE_NAME_TO_CODE = {
    "C": "C",
    "P": "P",
    "S": "S",
    "CURVA": "C",
    "PLANA": "P",
    "SEMICURVA": "S",
}

# Conservador: solo mapeos de medida validados por uso frecuente/ejemplos.
# Cualquier medida no mapeada queda marcada como "requiere mapeo".
DIAMETER_HUMAN_TO_COMPACT_DEFAULT = {
    "7/16": "716",
    "1/2": "12",
    "9/16": "916",
    "11/16": "1116",
    "5/8": "58",
    "3/4": "34",
    "7/8": "78",
    "1": "1",
    "18": "18",
    "20": "20",
    "22": "22",
    "24": "24",
}

DIAMETER_COMPACT_TO_HUMAN_DEFAULT = {
    compact: human for human, compact in DIAMETER_HUMAN_TO_COMPACT_DEFAULT.items()
}

DEFAULT_WIDTH_RANGE = (20, 300)
DEFAULT_LENGTH_RANGE = (100, 1200)


@dataclass
class _SplitCandidate:
    diameter_compact: str
    width: int
    length: int
    width_digits: int
    length_digits: int
    score: int
    reasons: List[str]


def _normalize_key(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().upper())


def _compact_measure(
    human_measure: str,
    human_to_compact: Dict[str, str],
    strict: bool = False,
) -> Tuple[str, bool, List[str]]:
    """
    Returns compact measure + requires_mapping flag + warnings.
    """
    warnings: List[str] = []
    normalized = _normalize_key(human_measure)
    if not normalized:
        raise ValueError("Diametro/medida principal es obligatorio.")

    if normalized in human_to_compact:
        return human_to_compact[normalized], False, warnings

    compact = re.sub(r"[^0-9]", "", normalized)
    if not compact:
        raise ValueError("No se pudo compactar la medida principal.")

    requires_mapping = bool("/" in normalized or " " in normalized)
    if requires_mapping:
        warnings.append(
            f"Medida '{human_measure}' no estandarizada en tabla; "
            f"se uso compactado '{compact}'."
        )
        if strict:
            raise ValueError(
                f"La medida '{human_measure}' requiere mapeo explicito. "
                f"Agregala a la tabla de equivalencias."
            )
    return compact, requires_mapping, warnings


def _split_numeric_core(
    numeric_core: str,
    known_widths: Optional[Iterable[int]],
    known_lengths: Optional[Iterable[int]],
    known_diameters_compact: Iterable[str],
) -> List[_SplitCandidate]:
    known_widths_set = {int(v) for v in known_widths or []}
    known_lengths_set = {int(v) for v in known_lengths or []}
    known_diameters = {str(v) for v in known_diameters_compact}

    candidates: List[_SplitCandidate] = []
    length_digit_candidates = [3]
    if known_lengths_set:
        length_digit_candidates = sorted({len(str(v)) for v in known_lengths_set}, reverse=True)
        if 3 not in length_digit_candidates:
            length_digit_candidates.append(3)

    for length_digits in length_digit_candidates:
        if len(numeric_core) <= length_digits + 2:
            continue

        length_str = numeric_core[-length_digits:]
        if not length_str.isdigit():
            continue
        length_val = int(length_str)

        remaining = numeric_core[:-length_digits]
        for width_digits in (2, 3):
            if len(remaining) <= width_digits:
                continue
            width_str = remaining[-width_digits:]
            diameter_compact = remaining[:-width_digits]
            if not (width_str.isdigit() and diameter_compact.isdigit()):
                continue

            width_val = int(width_str)
            score = 0
            reasons: List[str] = []

            if diameter_compact in known_diameters:
                score += 4
                reasons.append("diametro conocido")
            else:
                score -= 1
                reasons.append("diametro no mapeado")

            min_w, max_w = DEFAULT_WIDTH_RANGE
            min_l, max_l = DEFAULT_LENGTH_RANGE
            if min_w <= width_val <= max_w:
                score += 2
                reasons.append("ancho plausible")
            if min_l <= length_val <= max_l:
                score += 2
                reasons.append("largo plausible")

            if length_digits == 3:
                score += 2
                reasons.append("largo 3 digitos preferido")

            if known_widths_set:
                if width_val in known_widths_set:
                    score += 6
                    reasons.append("ancho coincide catalogo")
                else:
                    score -= 2
                    reasons.append("ancho fuera catalogo")

            if known_lengths_set:
                if length_val in known_lengths_set:
                    score += 6
                    reasons.append("largo coincide catalogo")
                else:
                    score -= 2
                    reasons.append("largo fuera catalogo")

            candidates.append(
                _SplitCandidate(
                    diameter_compact=diameter_compact,
                    width=width_val,
                    length=length_val,
                    width_digits=width_digits,
                    length_digits=length_digits,
                    score=score,
                    reasons=reasons,
                )
            )

    candidates.sort(
        key=lambda c: (
            c.score,
            c.length_digits == 3,
            c.width_digits == 3,
            -(len(c.diameter_compact)),
        ),
        reverse=True,
    )
    return candidates


def parsearCodigo(
    codigo: str,
    *,
    known_widths: Optional[Iterable[int]] = None,
    known_lengths: Optional[Iterable[int]] = None,
    diameter_compact_to_human: Optional[Dict[str, str]] = None,
) -> Dict[str, object]:
    """
    Parse a clamp code and return normalized attributes.
    """
    code = _normalize_key(codigo)
    match = re.fullmatch(r"(ABL|ABT)(\d+)([CPS])", code)
    if not match:
        raise ValueError("Codigo invalido. Debe cumplir formato ABL/ABT + numeros + C/P/S.")

    prefix, numeric_core, shape_code = match.groups()
    clamp_type = PREFIX_TO_TYPE[prefix]
    shape_name = SHAPE_CODE_TO_NAME[shape_code]

    compact_to_human = diameter_compact_to_human or DIAMETER_COMPACT_TO_HUMAN_DEFAULT
    known_diameters_compact = compact_to_human.keys()

    candidates = _split_numeric_core(
        numeric_core=numeric_core,
        known_widths=known_widths,
        known_lengths=known_lengths,
        known_diameters_compact=known_diameters_compact,
    )
    if not candidates:
        raise ValueError("No se pudo segmentar el bloque numerico en diametro/ancho/largo.")

    best = candidates[0]
    same_score_count = len([c for c in candidates if c.score == best.score])
    warnings: List[str] = []
    if same_score_count > 1:
        warnings.append(
            "Parseo ambiguo: se eligio la mejor combinacion por heuristica; "
            "revisar con catalogo de anchos/largos."
        )

    diameter_human = compact_to_human.get(best.diameter_compact, best.diameter_compact)
    requires_mapping = best.diameter_compact not in compact_to_human
    if requires_mapping:
        warnings.append(
            f"Diametro compactado '{best.diameter_compact}' sin mapeo humano; requiere estandarizacion."
        )

    return {
        "codigo_original": codigo,
        "codigo_normalizado": code,
        "prefijo": prefix,
        "tipo": clamp_type,
        "forma_codigo": shape_code,
        "forma": shape_name,
        "diametro_compactado": best.diameter_compact,
        "diametro": diameter_human,
        "ancho": best.width,
        "largo": best.length,
        "diametro_requiere_mapeo": requires_mapping,
        "parse_score": best.score,
        "parse_warnings": warnings,
    }


def parsear_codigo(*args, **kwargs):
    return parsearCodigo(*args, **kwargs)


def generarCodigo(
    *,
    tipo: str,
    diametro: str,
    ancho: int,
    largo: int,
    forma: str,
    human_to_compact: Optional[Dict[str, str]] = None,
    strict_diameter_mapping: bool = False,
    with_metadata: bool = False,
):
    """
    Generate a clamp code from attributes.
    """
    raw_type = _normalize_key(tipo)
    raw_shape = _normalize_key(forma)

    if raw_type not in TYPE_TO_PREFIX:
        raise ValueError("Tipo invalido. Usa ABL/ABT o LAMINADA/TREFILADA.")
    if raw_shape not in SHAPE_NAME_TO_CODE:
        raise ValueError("Forma invalida. Usa C/P/S o CURVA/PLANA/SEMICURVA.")

    try:
        width_int = int(ancho)
        length_int = int(largo)
    except (TypeError, ValueError):
        raise ValueError("Ancho y largo deben ser numericos enteros.")

    if width_int <= 0 or length_int <= 0:
        raise ValueError("Ancho y largo deben ser mayores a 0.")

    prefix = TYPE_TO_PREFIX[raw_type]
    shape_code = SHAPE_NAME_TO_CODE[raw_shape]
    map_to_use = human_to_compact or DIAMETER_HUMAN_TO_COMPACT_DEFAULT
    diameter_compact, requires_mapping, warnings = _compact_measure(
        diametro,
        map_to_use,
        strict=strict_diameter_mapping,
    )

    codigo = f"{prefix}{diameter_compact}{width_int}{length_int}{shape_code}"
    result = {
        "codigo": codigo,
        "prefijo": prefix,
        "tipo": PREFIX_TO_TYPE[prefix],
        "diametro": diametro,
        "diametro_compactado": diameter_compact,
        "diametro_requiere_mapeo": requires_mapping,
        "ancho": width_int,
        "largo": length_int,
        "forma_codigo": shape_code,
        "forma": SHAPE_CODE_TO_NAME[shape_code],
        "warnings": warnings,
    }

    if with_metadata:
        return result
    return codigo


def generar_codigo(*args, **kwargs):
    return generarCodigo(*args, **kwargs)
