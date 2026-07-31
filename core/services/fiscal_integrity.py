"""Canonical fiscal snapshots, hashes and deterministic money arithmetic."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from uuid import UUID

from django.core.exceptions import ValidationError


MONEY_QUANTUM = Decimal("0.01")
QUANTITY_QUANTUM = Decimal("0.001")
RATE_QUANTUM = Decimal("0.01")


class FiscalPayloadConflict(ValidationError):
    """An idempotency key was reused for different immutable fiscal content."""

    code = "fiscal_payload_conflict"


def decimal_value(value, *, default="0") -> Decimal:
    try:
        return Decimal(str(value if value is not None else default))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError(f"Valor decimal fiscal invalido: {value!r}.") from exc


def quantize_money(value) -> Decimal:
    return decimal_value(value).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def quantize_quantity(value) -> Decimal:
    return decimal_value(value).quantize(QUANTITY_QUANTUM, rounding=ROUND_HALF_UP)


def quantize_rate(value) -> Decimal:
    return decimal_value(value).quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)


def _canonicalize(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {
            str(key): _canonicalize(value[key])
            for key in sorted(value, key=lambda item: str(item))
        }
    if isinstance(value, (list, tuple)):
        return [_canonicalize(item) for item in value]
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    return str(value)


def canonical_json(payload) -> str:
    return json.dumps(
        _canonicalize(payload),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def fiscal_payload_hash(payload) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def totals_from_lines(lines):
    totals = {
        "subtotal_net": Decimal("0.00"),
        "discount_total": Decimal("0.00"),
        "tax_total": Decimal("0.00"),
        "total": Decimal("0.00"),
    }
    for line in lines:
        totals["subtotal_net"] += quantize_money(line.get("net_amount"))
        totals["discount_total"] += quantize_money(line.get("discount_amount"))
        totals["tax_total"] += quantize_money(line.get("iva_amount"))
        totals["total"] += quantize_money(line.get("total_amount"))
    return {name: quantize_money(value) for name, value in totals.items()}


def validate_line_and_totals(lines, totals):
    """Reject silent rounding drift; one cent is never hidden as a tolerance."""
    expected = totals_from_lines(lines)
    errors = []
    for name, expected_value in expected.items():
        actual = quantize_money(totals.get(name))
        if actual != expected_value:
            errors.append(
                f"{name}: informado {actual}, suma exacta de lineas {expected_value}"
            )
    for index, line in enumerate(lines, start=1):
        net = quantize_money(line.get("net_amount"))
        iva = quantize_money(line.get("iva_amount"))
        total = quantize_money(line.get("total_amount"))
        if quantize_money(net + iva) != total:
            errors.append(
                f"linea {index}: neto {net} + IVA {iva} no coincide con total {total}"
            )
    if errors:
        raise ValidationError(
            "El comprobante no reconcilia a centavos: " + " | ".join(errors)
        )
    return expected

