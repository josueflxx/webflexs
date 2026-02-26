"""Catalog Excel export builder using configurable templates."""

import json
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from catalog.models import Category, Product
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


def _serialize_product_value(product, key):
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
        return _decimal_to_excel(product.price)
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


def _resolve_category_ids(sheet):
    category_ids = set(sheet.categories.values_list("id", flat=True))
    if not category_ids:
        return set()
    if not sheet.include_descendant_categories:
        return category_ids

    categories = Category.objects.filter(id__in=category_ids)
    resolved = set()
    for category in categories:
        resolved.update(category.get_descendant_ids(include_self=True))
    return resolved


def _apply_sheet_filters(sheet):
    queryset = Product.objects.select_related("category", "supplier_ref").prefetch_related("categories").all()

    if sheet.only_active_products:
        queryset = queryset.filter(is_active=True)

    if sheet.only_catalog_visible:
        queryset = Product.catalog_visible(queryset=queryset, include_uncategorized=True)

    category_ids = _resolve_category_ids(sheet)
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


def _apply_header_styles(worksheet, total_columns):
    for col_idx in range(1, total_columns + 1):
        cell = worksheet.cell(row=1, column=col_idx)
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


def build_catalog_workbook(template):
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
    if not sheets:
        default_sheet = workbook.create_sheet("Catalogo")
        default_sheet.append(["Mensaje"])
        default_sheet.append(["La plantilla no tiene hojas configuradas."])
        rows_by_sheet["Catalogo"] = 1
        return workbook, {"rows_by_sheet": rows_by_sheet, "total_rows": 1}

    for sheet_config in sheets:
        worksheet = workbook.create_sheet(sheet_config.name[:31] or "Hoja")
        columns = list(
            sheet_config.columns.filter(is_active=True).order_by("order", "id")
        )
        if not columns:
            fallback_keys = ["sku", "name", "price", "stock"]
            columns = [
                type("TmpColumn", (), {"key": key, "header": export_columns_map.get(key, key)})
                for key in fallback_keys
            ]

        headers = [
            (col.header or export_columns_map.get(col.key, col.key))
            for col in columns
        ]
        column_keys = [col.key for col in columns]
        column_widths = [_string_len_for_width(header) for header in headers]

        if sheet_config.include_header:
            worksheet.append(headers)
            worksheet.freeze_panes = "A2"
            _apply_header_styles(worksheet, len(headers))

        row_count = 0
        for product in _apply_sheet_filters(sheet_config).iterator(chunk_size=500):
            row_values = [_serialize_product_value(product, col.key) for col in columns]
            worksheet.append(row_values)
            row_count += 1
            for idx, value in enumerate(row_values):
                column_widths[idx] = max(column_widths[idx], _string_len_for_width(value))

            excel_row = row_count + (1 if sheet_config.include_header else 0)
            _apply_row_styles(worksheet, excel_row, row_values, column_keys)

        if sheet_config.include_header and row_count > 0:
            worksheet.auto_filter.ref = worksheet.dimensions

        _set_auto_column_widths(worksheet, column_widths)

        rows_by_sheet[sheet_config.name] = row_count

    total_rows = sum(rows_by_sheet.values())
    return workbook, {"rows_by_sheet": rows_by_sheet, "total_rows": total_rows}


def build_export_filename(template):
    stamp = timezone.now().strftime("%Y%m%d_%H%M")
    return f"catalogo_{template.slug}_{stamp}.xlsx"
