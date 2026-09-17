"""Consistent filtering and counted facets, using one grouped query."""
from fractions import Fraction
from django.db.models import Count
from .clamp_parser import SPEC_FIELDS, normalize_diameter

LABELS = {"fabrication": "Fabricación", "diameter": "Diámetro", "width": "Ancho (mm)", "length": "Largo (mm)", "shape": "Forma"}


def option_sort(field, value):
    if field in ("diameter", "width", "length"):
        try:
            number = sum(Fraction(part) for part in str(value).split())
            return (0, number)
        except (ValueError, ZeroDivisionError):
            pass
    return (1, str(value))


def build_clamp_filters(products, params):
    selected = {field: str(params.get(field, "")).strip() for field in SPEC_FIELDS}
    errors = []
    filters = {}
    for field, value in selected.items():
        if not value:
            continue
        try:
            if field in ("width", "length"):
                if not value.isdigit() or not 0 < int(value) <= 10000:
                    raise ValueError()
                value = int(value)
            elif field == "diameter":
                value = normalize_diameter(value)
            elif field == "fabrication" and value not in ("TREFILADA", "LAMINADA", "FORJADA"):
                raise ValueError()
            elif field == "shape" and value not in ("CURVA", "PLANA", "SEMICURVA"):
                raise ValueError()
            filters[f"clamp_specs__{field}"] = value
            selected[field] = str(value)
        except ValueError:
            errors.append(f"{LABELS[field]}: el valor «{value}» no es válido.")

    lookups = [f"clamp_specs__{field}" for field in SPEC_FIELDS]
    groups = products.order_by().values(*lookups).annotate(total=Count("pk", distinct=True))
    # Compact rows also support instant draft counts without downloading products.
    combinations = [[str(row[key]) if row[key] is not None else "" for key in lookups] + [row["total"]] for row in groups]
    fields = []
    legacy_options = {}
    all_options = {}
    for index, field in enumerate(SPEC_FIELDS):
        values = {row[index] for row in combinations if row[index]}
        counts = {}
        for row in combinations:
            if all(not selected[other] or row[i] == selected[other] for i, other in enumerate(SPEC_FIELDS) if i != index):
                counts[row[index]] = counts.get(row[index], 0) + row[-1]
        if selected[field]:
            values.add(selected[field])
        ordered = sorted(values, key=lambda value: option_sort(field, value))
        # Keep the full option universe for instant updates when a filter is cleared.
        all_options[field] = [{"value": value, "label": value.title() if field in ("shape", "fabrication") else value} for value in ordered]
        available = [option for option in all_options[field] if counts.get(option["value"], 0) > 0 or option["value"] == selected[field]]
        legacy_options[field] = [int(option["value"]) if field in ("width", "length") and option["value"].isdigit() else option["value"] for option in available]
        fields.append({
            "name": field, "label": LABELS[field], "selected": selected[field],
            # Preserve an explicitly selected, incompatible URL value so it can be removed.
            "options": [{**option, "count": counts.get(option["value"], 0), "selected": option["value"] == selected[field]} for option in available],
        })
    filtered = products.none() if errors else products.filter(**filters)
    return filtered, {
        "clamp_options": legacy_options,
        "clamp_filter_fields": fields,
        "clamp_combinations": combinations,
        "clamp_all_options": all_options,
        "clamp_filter_errors": errors,
    }, {field: value for field, value in selected.items() if value}
