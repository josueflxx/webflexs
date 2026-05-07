"""Catalog Excel export builder using configurable templates."""

import json
from types import SimpleNamespace
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from catalog.models import Category, Product, PriceListItem
from core.models import CATALOG_EXPORT_COLUMN_CHOICES


SORT_MAP = {
    "name_asc": ("name", "id"),
    "name_desc": ("-name", "-id"),
    "sku_asc": ("sku", "id"),
    "sku_desc": ("-sku", "-id"),
    "updated_desc": ("-updated_at", "-id"),
    "price_desc": ("-price", "-id"),
    "price_asc": ("price", "id"),
}

MONEY_KEYS = {"price", "cost"}
INTEGER_KEYS = {"stock"}
STATUS_KEYS = {"is_active", "is_visible_in_catalog"}
DATE_KEYS = {"created_at", "updated_at"}
TEXT_WRAP_KEYS = {"description", "attributes_json", "categories"}

HEADER_FILL = PatternFill(fill_type="solid", fgColor="1F2937")
HEADER_FONT = Font(color="FFFFFF", bold=True)
HEADER_ALIGNMENT = Alignment(horizontal="left", vertical="center")
GROUP_TITLE_FILL = PatternFill(fill_type="solid", fgColor="E5E7EB")
GROUP_TITLE_FONT = Font(color="111827", bold=True, size=12)
ALT_ROW_FILL = PatternFill(fill_type="solid", fgColor="F8FAFC")
STATUS_OK_FILL = PatternFill(fill_type="solid", fgColor="D1FAE5")
STATUS_BAD_FILL = PatternFill(fill_type="solid", fgColor="FEE2E2")
THIN_BORDER = Border(
    left=Side(style="thin", color="D1D5DB"),
    right=Side(style="thin", color="D1D5DB"),
    top=Side(style="thin", color="D1D5DB"),
    bottom=Side(style="thin", color="D1D5DB"),
)


def _decimal_to_excel(value):
    if value is None:
        return 0
    if isinstance(value, Decimal):
        return float(value)
    return value


def _yes_no(value):
    return "Si" if value else "No"


def _serialize_product_value(product, key, price_map=None, discount_percentage=None):
    if key == "sku":
        return product.sku
    if key == "name":
        return product.name
    if key == "description":
        return product.description or ""
    if key == "supplier":
        return product.supplier or ""
    if key == "supplier_normalized":
        return product.supplier_ref.name if product.supplier_ref_id else ""
    if key == "price":
        base_price = product.price
        if price_map is not None:
            base_price = price_map.get(product.id, product.price)
        if discount_percentage is not None and discount_percentage != 0:
            discount_decimal = Decimal(str(discount_percentage)) / Decimal("100")
            base_price = base_price * (Decimal("1") - discount_decimal)
        return _decimal_to_excel(base_price)
    if key == "cost":
        return _decimal_to_excel(product.cost)
    if key == "stock":
        return product.stock
    if key == "is_active":
        return _yes_no(product.is_active)
    if key == "is_visible_in_catalog":
        return _yes_no(product.is_visible_in_catalog())
    if key == "primary_category":
        primary = product.get_primary_category()
        return primary.name if primary else ""
    if key == "categories":
        linked = product.get_linked_categories()
        if not linked:
            return ""
        return " | ".join(sorted({cat.name for cat in linked}))
    if key == "filter_1":
        return product.filter_1 or ""
    if key == "filter_2":
        return product.filter_2 or ""
    if key == "filter_3":
        return product.filter_3 or ""
    if key == "filter_4":
        return product.filter_4 or ""
    if key == "filter_5":
        return product.filter_5 or ""
    if key == "created_at":
        if not product.created_at:
            return ""
        return timezone.localtime(product.created_at).replace(tzinfo=None)
    if key == "updated_at":
        if not product.updated_at:
            return ""
        return timezone.localtime(product.updated_at).replace(tzinfo=None)
    if key == "attributes_json":
        return json.dumps(product.attributes or {}, ensure_ascii=False)
    return ""


def _is_public_category(category, category_lookup=None):
    current = category
    seen = set()
    while current and current.pk not in seen:
        if not current.is_active or not current.visible_in_catalog:
            return False
        seen.add(current.pk)
        if category_lookup is None:
            current = current.parent
        else:
            current = category_lookup.get(current.parent_id)
    return bool(category)


def _selected_category_ids(sheet):
    return set(sheet.categories.values_list("id", flat=True))


def _selected_supplier_ids(sheet):
    return set(sheet.suppliers.values_list("id", flat=True))


def _sheet_search_query(sheet):
    return (getattr(sheet, "search_query", "") or "").strip()


def _sheet_requires_public_catalog(sheet):
    template = getattr(sheet, "template", None)
    return bool(sheet.only_catalog_visible or (template and template.is_client_download_enabled))


def _sheet_has_explicit_scope(sheet):
    return bool(
        _selected_category_ids(sheet)
        or _selected_supplier_ids(sheet)
        or _sheet_search_query(sheet)
    )


def _sheet_should_export(sheet):
    selected_category_ids = _selected_category_ids(sheet)
    if _sheet_requires_public_catalog(sheet):
        template = getattr(sheet, "template", None)
        if template and template.is_client_download_enabled and not _sheet_has_explicit_scope(sheet):
            return False
        if selected_category_ids:
            return bool(_resolve_category_ids(sheet, selected_ids=selected_category_ids))
    return True


def _public_category_ids_with_products():
    all_categories = {
        category.id: category
        for category in Category.objects.select_related("parent")
    }
    visible_categories = {
        category.id: category
        for category in all_categories.values()
        if category.is_active and category.visible_in_catalog
    }
    if not visible_categories:
        return set()

    products = Product.catalog_visible(Product.objects.all(), include_uncategorized=False)
    linked_ids = set(
        products.exclude(category_id__isnull=True).values_list("category_id", flat=True)
    )
    linked_ids.update(
        products.exclude(categories__id__isnull=True).values_list("categories__id", flat=True)
    )

    public_ids = set()
    for category_id in linked_ids:
        node = visible_categories.get(category_id)
        if not node:
            continue
        chain = []
        cursor = node
        is_public_path = True
        while cursor:
            if not cursor.is_active or not cursor.visible_in_catalog:
                is_public_path = False
                break
            chain.append(cursor)
            cursor = all_categories.get(cursor.parent_id)
        if is_public_path:
            public_ids.update(category.id for category in chain)
    return public_ids


def _category_public_sort_key(category):
    return (
        category.public_order,
        category.order,
        (category.display_name or category.name or "").lower(),
        category.id,
    )


class _ListRelationAdapter:
    def __init__(self, items):
        self._items = list(items)

    def values_list(self, field_name, flat=False):
        values = [getattr(item, field_name) for item in self._items]
        if flat:
            return values
        return [(value,) for value in values]

    def all(self):
        return list(self._items)


class _ColumnRelationAdapter:
    def __init__(self, items):
        self._items = list(items)

    def filter(self, **kwargs):
        items = self._items
        for field_name, expected in kwargs.items():
            items = [
                item
                for item in items
                if getattr(item, field_name, None) == expected
            ]
        return _ColumnRelationAdapter(items)

    def order_by(self, *fields):
        items = list(self._items)
        for field in reversed(fields):
            reverse = field.startswith("-")
            field_name = field[1:] if reverse else field
            items.sort(
                key=lambda item: getattr(item, field_name, None),
                reverse=reverse,
            )
        return _ColumnRelationAdapter(items)

    def __iter__(self):
        return iter(self._items)


def _build_synthetic_category_sheet(template, category, columns, order, name=None):
    return SimpleNamespace(
        template=template,
        name=name or category.name[:31] or "Categoria",
        order=order,
        include_header=True,
        only_active_products=True,
        only_catalog_visible=True,
        include_descendant_categories=True,
        group_by_subcategories=True,
        search_query="",
        max_rows=None,
        sort_by="name_asc",
        categories=_ListRelationAdapter([category]),
        suppliers=_ListRelationAdapter([]),
        columns=_ColumnRelationAdapter(columns),
        _synthetic=True,
    )


def _complete_public_catalog_sheets(template, sheets):
    if not template.is_client_download_enabled:
        return sheets

    selected_root_ids = set()
    for sheet in sheets:
        selected_category_ids = _selected_category_ids(sheet)
        selected_root_ids.update(selected_category_ids)

    public_ids = _public_category_ids_with_products()
    if not public_ids:
        return sheets

    public_categories = {
        category.id: category
        for category in Category.objects.filter(
            id__in=public_ids,
            is_active=True,
            visible_in_catalog=True,
        ).select_related("parent")
    }
    public_roots = [
        category
        for category in public_categories.values()
        if category.parent_id not in public_categories
    ]
    public_roots.sort(key=_category_public_sort_key)

    base_columns = []
    for sheet in sheets:
        base_columns = list(sheet.columns.filter(is_active=True).order_by("order", "id"))
        if base_columns:
            break

    completed_sheets = list(sheets)
    used_names = {sheet.name[:31] for sheet in completed_sheets}
    next_order = len(completed_sheets) + 1

    def unique_sheet_name(base_name):
        base_name = (base_name[:31] or "Categoria").strip()
        candidate = base_name
        counter = 2
        while candidate in used_names:
            suffix = f" {counter}"
            candidate = f"{base_name[:31 - len(suffix)]}{suffix}"
            counter += 1
        used_names.add(candidate)
        return candidate

    for category in public_roots:
        if category.id in selected_root_ids:
            continue
        completed_sheets.append(
            _build_synthetic_category_sheet(
                template,
                category,
                base_columns,
                next_order,
                name=unique_sheet_name(category.name),
            )
        )
        next_order += 1
    return completed_sheets


def _filter_public_category_ids(category_ids):
    if not category_ids:
        return set()
    category_lookup = Category.objects.all().in_bulk()
    return {
        category_id
        for category_id in category_ids
        if _is_public_category(category_lookup.get(category_id), category_lookup=category_lookup)
    }


def _resolve_category_ids(sheet, selected_ids=None):
    """Resolve sheet categories, honoring public visibility for client catalog exports."""
    category_ids = selected_ids if selected_ids is not None else _selected_category_ids(sheet)
    if not category_ids:
        return set()

    if not sheet.include_descendant_categories:
        resolved_ids = category_ids
    else:
        categories = Category.objects.filter(id__in=category_ids)
        resolved_ids = set()
        for category in categories:
            resolved_ids.update(category.get_descendant_ids(include_self=True))

    if _sheet_requires_public_catalog(sheet):
        return _filter_public_category_ids(resolved_ids)
    return resolved_ids


def _apply_sheet_filters(sheet):
    queryset = Product.objects.select_related("category", "supplier_ref").prefetch_related("categories").all()

    if sheet.only_active_products:
        queryset = queryset.filter(is_active=True)

    if _sheet_requires_public_catalog(sheet):
        queryset = Product.catalog_visible(queryset=queryset, include_uncategorized=True)

    selected_category_ids = _selected_category_ids(sheet)
    category_ids = _resolve_category_ids(sheet, selected_ids=selected_category_ids)
    if selected_category_ids and not category_ids:
        return queryset.none()
    if category_ids:
        queryset = queryset.filter(
            Q(category_id__in=category_ids) | Q(categories__id__in=category_ids)
        ).distinct()

    supplier_ids = list(sheet.suppliers.values_list("id", flat=True))
    if supplier_ids:
        queryset = queryset.filter(supplier_ref_id__in=supplier_ids)

    search_query = (sheet.search_query or "").strip()
    if search_query:
        queryset = queryset.filter(
            Q(sku__icontains=search_query)
            | Q(name__icontains=search_query)
            | Q(description__icontains=search_query)
            | Q(supplier__icontains=search_query)
            | Q(filter_1__icontains=search_query)
            | Q(filter_2__icontains=search_query)
            | Q(filter_3__icontains=search_query)
            | Q(filter_4__icontains=search_query)
            | Q(filter_5__icontains=search_query)
        )

    order_by = SORT_MAP.get(sheet.sort_by) or SORT_MAP["name_asc"]
    queryset = queryset.order_by(*order_by)

    if sheet.max_rows:
        queryset = queryset[: sheet.max_rows]

    return queryset


def _string_len_for_width(value):
    if value is None:
        return 0
    if isinstance(value, float):
        return len(f"{value:,.2f}")
    if isinstance(value, (int, Decimal)):
        return len(f"{value:,}")
    if hasattr(value, "strftime"):
        return 16
    return len(str(value))


def _apply_header_styles(worksheet, total_columns, row=1):
    for col_idx in range(1, total_columns + 1):
        cell = worksheet.cell(row=row, column=col_idx)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = HEADER_ALIGNMENT
        cell.border = THIN_BORDER


def _apply_row_styles(worksheet, excel_row, row_values, column_keys):
    for col_idx, value in enumerate(row_values, start=1):
        key = column_keys[col_idx - 1]
        cell = worksheet.cell(row=excel_row, column=col_idx)

        if excel_row % 2 == 0:
            cell.fill = ALT_ROW_FILL

        cell.border = THIN_BORDER

        if key in MONEY_KEYS:
            cell.number_format = '"$"#,##0.00'
            cell.alignment = Alignment(horizontal="right", vertical="center")
        elif key in INTEGER_KEYS:
            cell.number_format = '#,##0'
            cell.alignment = Alignment(horizontal="right", vertical="center")
        elif key in DATE_KEYS:
            cell.number_format = "yyyy-mm-dd hh:mm"
            cell.alignment = Alignment(horizontal="center", vertical="center")
        elif key in STATUS_KEYS:
            cell.alignment = Alignment(horizontal="center", vertical="center")
            if str(value).strip().lower() == "si":
                cell.fill = STATUS_OK_FILL
            elif str(value).strip().lower() == "no":
                cell.fill = STATUS_BAD_FILL
        else:
            wrap = key in TEXT_WRAP_KEYS
            cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=wrap)


def _set_auto_column_widths(worksheet, column_widths):
    for idx, width in enumerate(column_widths, start=1):
        adjusted = max(10, min(width + 2, 72))
        worksheet.column_dimensions[get_column_letter(idx)].width = adjusted


def _worksheet_next_row(worksheet):
    if worksheet.max_row == 1 and worksheet.max_column == 1 and worksheet["A1"].value is None:
        return 1
    return worksheet.max_row + 1


def _category_depth(category, category_lookup):
    depth = 0
    parent_id = getattr(category, "parent_id", None)
    seen = set()
    while parent_id and parent_id not in seen:
        seen.add(parent_id)
        parent = category_lookup.get(parent_id)
        if parent is None:
            break
        depth += 1
        parent_id = parent.parent_id
    return depth


def _category_path(category, category_lookup, public=False):
    if category is None:
        return []
    path = []
    current = category
    seen = set()
    while current and current.pk not in seen:
        seen.add(current.pk)
        label = current.display_name if public else current.name
        path.insert(0, label)
        current = category_lookup.get(current.parent_id)
    return path


def _is_descendant_of(category, root_id, category_lookup):
    current = category
    seen = set()
    while current and current.pk not in seen:
        if current.pk == root_id:
            return True
        seen.add(current.pk)
        current = category_lookup.get(current.parent_id)
    return False


def _category_sort_key(category, category_lookup):
    path = []
    current = category
    seen = set()
    while current and current.pk not in seen:
        seen.add(current.pk)
        path.insert(
            0,
            (
                current.public_order,
                current.order,
                (current.display_name or current.name or "").lower(),
                current.pk,
            ),
        )
        current = category_lookup.get(current.parent_id)
    return tuple(path)


def _client_export_uses_canonical_categories(sheet):
    template = getattr(sheet, "template", None)
    return bool(template and template.is_client_download_enabled and _selected_category_ids(sheet))


def _select_canonical_public_category(product, category_lookup):
    linked_categories = [
        category
        for category in product.get_linked_categories()
        if category and _is_public_category(category, category_lookup=category_lookup)
    ]
    if not linked_categories:
        return None

    primary_category = category_lookup.get(product.category_id) if product.category_id else None
    if primary_category and _is_public_category(primary_category, category_lookup=category_lookup):
        same_branch = [
            category
            for category in linked_categories
            if _is_descendant_of(category, primary_category.pk, category_lookup)
        ]
        if same_branch:
            same_branch.sort(
                key=lambda category: (
                    -_category_depth(category, category_lookup),
                    _category_sort_key(category, category_lookup),
                )
            )
            return same_branch[0]
        return primary_category

    linked_categories.sort(
        key=lambda category: (
            -_category_depth(category, category_lookup),
            _category_sort_key(category, category_lookup),
        )
    )
    return linked_categories[0]


def _iter_sheet_products(sheet):
    queryset = _apply_sheet_filters(sheet)
    if not _client_export_uses_canonical_categories(sheet):
        yield from queryset.iterator(chunk_size=500)
        return

    resolved_ids = _resolve_category_ids(sheet, selected_ids=_selected_category_ids(sheet))
    category_lookup = Category.objects.select_related("parent").in_bulk()
    for product in queryset.iterator(chunk_size=500):
        canonical_category = _select_canonical_public_category(product, category_lookup)
        if canonical_category and canonical_category.pk in resolved_ids:
            yield product


def _build_grouping_context(sheet):
    selected_ids = _selected_category_ids(sheet)
    resolved_ids = _resolve_category_ids(sheet, selected_ids=selected_ids)
    category_lookup = Category.objects.all().in_bulk()

    selected_categories = [
        category_lookup[category_id]
        for category_id in selected_ids
        if category_id in category_lookup
    ]
    selected_categories.sort(key=lambda category: _category_sort_key(category, category_lookup))

    return {
        "selected_ids": selected_ids,
        "resolved_ids": resolved_ids,
        "only_catalog_visible": _sheet_requires_public_catalog(sheet),
        "category_lookup": category_lookup,
        "selected_categories": selected_categories,
    }


def _select_product_group_category(product, grouping_context):
    resolved_ids = grouping_context["resolved_ids"]
    selected_ids = grouping_context["selected_ids"]
    only_catalog_visible = grouping_context["only_catalog_visible"]
    if selected_ids and not resolved_ids:
        return None

    linked_categories = [
        category
        for category in product.get_linked_categories()
        if category
        and (not resolved_ids or category.pk in resolved_ids)
        and (not only_catalog_visible or _is_public_category(category, grouping_context["category_lookup"]))
    ]
    if not linked_categories and not resolved_ids:
        linked_categories = [
            category
            for category in product.get_linked_categories()
            if category and (not only_catalog_visible or _is_public_category(category, grouping_context["category_lookup"]))
        ]
    if not linked_categories:
        return None

    category_lookup = grouping_context["category_lookup"]
    linked_categories.sort(
        key=lambda category: (
            -_category_depth(category, category_lookup),
            _category_sort_key(category, category_lookup),
        )
    )
    return linked_categories[0]


def _category_group_label(category, grouping_context):
    if category is None:
        return "Sin categoria"

    category_lookup = grouping_context["category_lookup"]
    selected_categories = grouping_context["selected_categories"]
    matching_roots = [
        root
        for root in selected_categories
        if _is_descendant_of(category, root.pk, category_lookup)
    ]

    if len(selected_categories) == 1 and matching_roots:
        root = matching_roots[0]
        if category.pk == root.pk:
            return "Sin subcategoria"
        root_path = _category_path(root, category_lookup, public=True)
        category_path = _category_path(category, category_lookup, public=True)
        relative_path = category_path[len(root_path):]
        return " > ".join(relative_path) if relative_path else category.display_name

    return " > ".join(_category_path(category, category_lookup, public=True)) or category.display_name


def _append_group_title(worksheet, row_index, label, product_count, total_columns):
    title_cell = worksheet.cell(row=row_index, column=1)
    title_cell.value = f"{label} ({product_count} productos)"
    title_cell.font = GROUP_TITLE_FONT
    title_cell.fill = GROUP_TITLE_FILL
    title_cell.alignment = Alignment(horizontal="left", vertical="center")
    title_cell.border = THIN_BORDER

    if total_columns > 1:
        for col_idx in range(2, total_columns + 1):
            cell = worksheet.cell(row=row_index, column=col_idx)
            cell.fill = GROUP_TITLE_FILL
            cell.border = THIN_BORDER
        worksheet.merge_cells(
            start_row=row_index,
            start_column=1,
            end_row=row_index,
            end_column=total_columns,
        )

    worksheet.row_dimensions[row_index].height = 22


def _append_product_row(
    worksheet,
    excel_row,
    product,
    columns,
    column_keys,
    column_widths,
    price_map,
    discount_percentage,
):
    row_values = [
        _serialize_product_value(
            product,
            col.key,
            price_map=price_map,
            discount_percentage=discount_percentage,
        )
        for col in columns
    ]
    worksheet.append(row_values)
    for idx, value in enumerate(row_values):
        column_widths[idx] = max(column_widths[idx], _string_len_for_width(value))
    _apply_row_styles(worksheet, excel_row, row_values, column_keys)


def _append_grouped_products(
    worksheet,
    sheet_config,
    columns,
    headers,
    column_keys,
    column_widths,
    price_map,
    discount_percentage,
):
    grouping_context = _build_grouping_context(sheet_config)
    grouped_products = {}

    products = list(_iter_sheet_products(sheet_config))
    for product in products:
        group_category = _select_product_group_category(product, grouping_context)
        group_key = group_category.pk if group_category else 0
        if group_key not in grouped_products:
            grouped_products[group_key] = {
                "category": group_category,
                "products": [],
            }
        grouped_products[group_key]["products"].append(product)

    sorted_groups = sorted(
        grouped_products.values(),
        key=lambda group: (
            group["category"] is None,
            _category_sort_key(group["category"], grouping_context["category_lookup"])
            if group["category"] is not None
            else ((999999, 999999, "sin categoria", 0),),
        ),
    )

    if not sorted_groups:
        if sheet_config.include_header:
            worksheet.append(headers)
            _apply_header_styles(worksheet, len(headers))
        return 0

    row_count = 0
    for group in sorted_groups:
        products_in_group = group["products"]
        if not products_in_group:
            continue

        title = _category_group_label(group["category"], grouping_context)
        next_row = _worksheet_next_row(worksheet)
        _append_group_title(worksheet, next_row, title, len(products_in_group), len(headers))
        column_widths[0] = max(column_widths[0], _string_len_for_width(title) + 12)

        if sheet_config.include_header:
            worksheet.append(headers)
            header_row = worksheet.max_row
            _apply_header_styles(worksheet, len(headers), row=header_row)

        for product in products_in_group:
            excel_row = worksheet.max_row + 1
            _append_product_row(
                worksheet,
                excel_row,
                product,
                columns,
                column_keys,
                column_widths,
                price_map,
                discount_percentage,
            )
            row_count += 1

        worksheet.append([])

    return row_count


def build_catalog_workbook(template, price_list=None, discount_percentage=None):
    """
    Build an XLSX workbook from one CatalogExcelTemplate instance.
    Returns (workbook, stats_dict).
    """
    workbook = Workbook()
    workbook.remove(workbook.active)

    rows_by_sheet = {}
    export_columns_map = dict(CATALOG_EXPORT_COLUMN_CHOICES)

    sheets = list(
        template.sheets.prefetch_related("columns", "categories", "suppliers").order_by("order", "id")
    )
    sheets = _complete_public_catalog_sheets(template, sheets)
    if not sheets:
        default_sheet = workbook.create_sheet("Catalogo")
        default_sheet.append(["Mensaje"])
        default_sheet.append(["La plantilla no tiene hojas configuradas."])
        rows_by_sheet["Catalogo"] = 1
        return workbook, {"rows_by_sheet": rows_by_sheet, "total_rows": 1}

    price_map = None
    if price_list:
        price_map = {
            product_id: price
            for product_id, price in PriceListItem.objects.filter(price_list=price_list).values_list(
                "product_id", "price"
            )
        }

    exported_any_sheet = False
    skipped_sheets = []

    for sheet_config in sheets:
        if not _sheet_should_export(sheet_config):
            skipped_sheets.append(sheet_config.name)
            continue

        worksheet = workbook.create_sheet(sheet_config.name[:31] or "Hoja")
        columns = list(
            sheet_config.columns.filter(is_active=True).order_by("order", "id")
        )
        if not columns:
            fallback_columns = [
                ("sku", "SKU"),
                ("name", "Articulo"),
                ("price", "Precio"),
            ]
            columns = [
                type("TmpColumn", (), {"key": key, "header": header})
                for key, header in fallback_columns
            ]

        headers = [
            (col.header or export_columns_map.get(col.key, col.key))
            for col in columns
        ]
        column_keys = [col.key for col in columns]
        column_widths = [_string_len_for_width(header) for header in headers]

        if sheet_config.group_by_subcategories:
            row_count = _append_grouped_products(
                worksheet,
                sheet_config,
                columns,
                headers,
                column_keys,
                column_widths,
                price_map,
                discount_percentage,
            )
            _set_auto_column_widths(worksheet, column_widths)
            if _sheet_requires_public_catalog(sheet_config) and row_count == 0:
                workbook.remove(worksheet)
                skipped_sheets.append(sheet_config.name)
                continue
            exported_any_sheet = True
            rows_by_sheet[sheet_config.name] = row_count
            continue

        if sheet_config.include_header:
            worksheet.append(headers)
            worksheet.freeze_panes = "A2"
            _apply_header_styles(worksheet, len(headers))

        row_count = 0
        for product in _iter_sheet_products(sheet_config):
            excel_row = row_count + (1 if sheet_config.include_header else 0) + 1
            _append_product_row(
                worksheet,
                excel_row,
                product,
                columns,
                column_keys,
                column_widths,
                price_map,
                discount_percentage,
            )
            row_count += 1

        if sheet_config.include_header and row_count > 0:
            worksheet.auto_filter.ref = worksheet.dimensions

        _set_auto_column_widths(worksheet, column_widths)

        if _sheet_requires_public_catalog(sheet_config) and row_count == 0:
            workbook.remove(worksheet)
            skipped_sheets.append(sheet_config.name)
            continue

        exported_any_sheet = True
        rows_by_sheet[sheet_config.name] = row_count

    if not exported_any_sheet:
        default_sheet = workbook.create_sheet("Catalogo")
        default_sheet.append(["Mensaje"])
        default_sheet.append(["No hay categorias visibles para exportar."])
        rows_by_sheet["Catalogo"] = 0

    total_rows = sum(rows_by_sheet.values())
    return workbook, {
        "rows_by_sheet": rows_by_sheet,
        "total_rows": total_rows,
        "skipped_sheets": skipped_sheets,
    }


def build_export_filename(template):
    stamp = timezone.now().strftime("%Y%m%d_%H%M")
    return f"catalogo_{template.slug}_{stamp}.xlsx"
