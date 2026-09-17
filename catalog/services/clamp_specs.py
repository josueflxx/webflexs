"""One safe synchronization path for imports, editing and repairs."""
from django.db import transaction

from catalog.models import ClampSpecs
from .clamp_parser import ClampParser, SPEC_FIELDS


def plan_clamp_specs(product, existing=None, *, refresh=False):
    if existing and existing.manual_override:
        return {"status": "manual", "changes": {}, "warnings": []}
    sources = [ClampParser.parse(product.name), ClampParser.parse(product.description)]
    data = {}
    warnings = [
        f"{label}: {warning}"
        for label, source in zip(("Nombre", "Descripción"), sources)
        for warning in source["parse_warnings"]
        if not warning.startswith(("Falta:", "Ignorado:"))
    ]
    for field in SPEC_FIELDS:
        values = {source[field] for source in sources if source[field] is not None}
        if len(values) > 1:
            warnings.append(f"Revisar {field}: nombre y descripción no coinciden")
        elif values:
            data[field] = values.pop()
    if not data.get("fabrication"):
        return {"status": "review", "changes": {}, "warnings": warnings or ["No se reconoce el tipo de abrazadera"]}

    complete = len(data) == len(SPEC_FIELDS) and not warnings
    changes = {}
    for field, value in data.items():
        old = getattr(existing, field, None)
        # Incomplete parses fill blanks, but cannot overwrite known dimensions.
        if old != value and (old in (None, "") or (refresh and complete)):
            changes[field] = value
        elif old not in (None, "") and old != value:
            warnings.append(f"Revisar {field}: se conserva el valor existente {old}")
    final = {field: changes.get(field, getattr(existing, field, None)) for field in SPEC_FIELDS}
    warnings.extend(f"Falta: {field}" for field, value in final.items() if value in (None, ""))
    status = ("update" if existing else "create") if changes else ("review" if warnings else "unchanged")
    return {"status": status, "changes": changes, "warnings": warnings, "confidence": max(0, 100 - 15 * len(warnings))}


def sync_product_clamp_specs(product, *, refresh=True):
    # Ordinary catalog imports should not pay for clamp-specific database queries.
    if not any(ClampParser.normalize_text(value).startswith("ABRAZADERA") for value in (product.name, product.description)):
        return {"status": "ignored", "changes": {}, "warnings": []}
    with transaction.atomic():
        # Serialize automatic writers, including when the specs row does not exist yet.
        type(product).objects.select_for_update().get(pk=product.pk)
        existing = ClampSpecs.objects.select_for_update().filter(product=product).first()
        plan = plan_clamp_specs(product, existing, refresh=refresh)
        if plan["changes"]:
            spec = existing or ClampSpecs(product=product)
            for field, value in plan["changes"].items():
                setattr(spec, field, value)
            spec.parse_confidence = plan["confidence"]
            spec.parse_warnings = plan["warnings"]
            spec.save()
    return plan
