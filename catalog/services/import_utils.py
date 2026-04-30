"""Helpers for tolerant spreadsheet imports."""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
import unicodedata

import pandas as pd
from django.utils.text import slugify


MONEY_QUANT = Decimal("0.01")


def is_blank(value):
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    return str(value).strip() == ""


def normalize_text(value):
    if is_blank(value):
        return ""
    return re.sub(r"\s+", " ", str(value).strip())


def normalize_header(value):
    text = normalize_text(value).lower()
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("º", "").replace("°", "")
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def normalize_columns(columns, alias_map, positional_columns=None, required_any=None):
    """
    Map user-facing spreadsheet headers to canonical internal keys.

    If the file looks header-based, use aliases. Position-based mapping is used
    only when no useful headers are detected, so a missing optional column cannot
    shift every value into the wrong field.
    """
    normalized_headers = [normalize_header(col) for col in columns]
    mapped = []
    recognized_count = 0
    used = {}

    for original, normalized in zip(columns, normalized_headers):
        canonical = alias_map.get(normalized, normalized or str(original).strip())
        if canonical in set(alias_map.values()):
            recognized_count += 1

        # Keep duplicate spreadsheet headers importable without clobbering data.
        if canonical in used:
            used[canonical] += 1
            canonical = f"{canonical}_{used[canonical]}"
        else:
            used[canonical] = 1
        mapped.append(canonical)

    required_any = set(required_any or ())
    has_required_signal = bool(required_any.intersection(mapped))
    if recognized_count >= 2 or has_required_signal:
        return mapped, "headers"

    if positional_columns and len(columns) >= len(required_any or []):
        fallback = []
        for idx, col in enumerate(columns):
            fallback.append(positional_columns[idx] if idx < len(positional_columns) else str(col).strip())
        return fallback, "positional"

    return mapped, "headers"


def parse_decimal(value, field_label="valor", min_value=None, allow_blank=False, default=None):
    """
    Parse Decimal values entered by humans in Argentine spreadsheets.

    Accepts Excel numeric cells, "$ 12.500,00", "12500.00", "12,5",
    "1.234.567,89", and negative values when min_value allows them.
    """
    if is_blank(value):
        if allow_blank:
            return default
        raise ValueError(f"{field_label} vacio")

    if isinstance(value, Decimal):
        decimal_value = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        decimal_value = Decimal(str(value))
    else:
        raw = normalize_text(value)
        raw = raw.replace("\u00a0", " ")
        raw = re.sub(r"[$\s]", "", raw)
        raw = raw.replace("ARS", "").replace("ars", "")
        raw = raw.replace("+", "")

        if not raw:
            if allow_blank:
                return default
            raise ValueError(f"{field_label} vacio")

        if "," in raw and "." in raw:
            if raw.rfind(",") > raw.rfind("."):
                raw = raw.replace(".", "").replace(",", ".")
            else:
                raw = raw.replace(",", "")
        elif "," in raw:
            raw = raw.replace(".", "").replace(",", ".")
        elif raw.count(".") > 1:
            raw = raw.replace(".", "")
        elif raw.count(".") == 1:
            integer_part, decimal_part = raw.split(".", 1)
            if len(decimal_part) == 3 and len(integer_part) <= 3:
                raw = integer_part + decimal_part

        try:
            decimal_value = Decimal(raw)
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"{field_label} invalido: {value}") from exc

    decimal_value = decimal_value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    if min_value is not None and decimal_value < Decimal(str(min_value)):
        raise ValueError(f"{field_label} no puede ser menor a {min_value}")
    return decimal_value


def parse_int(value, field_label="valor", min_value=None, allow_blank=False, default=None):
    if is_blank(value):
        if allow_blank:
            return default
        raise ValueError(f"{field_label} vacio")
    try:
        number = parse_decimal(value, field_label=field_label)
        int_value = int(number)
    except ValueError as exc:
        raise ValueError(f"{field_label} invalido: {value}") from exc

    if min_value is not None and int_value < int(min_value):
        raise ValueError(f"{field_label} no puede ser menor a {min_value}")
    return int_value


def parse_bool(value, default=None):
    if is_blank(value):
        return default
    text = normalize_header(value)
    if text in {
        "si",
        "s",
        "yes",
        "y",
        "true",
        "1",
        "x",
        "ok",
        "activo",
        "activa",
        "habilitado",
        "habilitada",
        "visible",
        "publicado",
        "publicada",
    }:
        return True
    if text in {
        "no",
        "n",
        "false",
        "0",
        "inactivo",
        "inactiva",
        "deshabilitado",
        "deshabilitada",
        "oculto",
        "oculta",
        "desactivado",
        "desactivada",
        "baja",
    }:
        return False
    return default


def split_cell_values(value):
    if is_blank(value):
        return []
    text = normalize_text(value)
    parts = re.split(r"\s*(?:\||;|,|>|/)\s*", text)
    return [part for part in (normalize_text(part) for part in parts) if part]


def normalize_sku(value):
    text = normalize_text(value)
    if not text:
        return ""
    if re.fullmatch(r"\d+\.0", text):
        text = text[:-2]
    return text


def unique_slug_for_model(model, value, exclude_pk=None):
    base = slugify(value)[:90] or "item"
    candidate = base
    counter = 1
    qs = model.objects.all()
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    while qs.filter(slug=candidate).exists():
        suffix = f"-{counter}"
        candidate = f"{base[: 100 - len(suffix)]}{suffix}"
        counter += 1
    return candidate
