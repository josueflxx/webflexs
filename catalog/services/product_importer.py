from decimal import Decimal

import pandas as pd

from core.services.importer import BaseImporter, ImportRowResult
from catalog.models import Product, ClampSpecs
from catalog.services.clamp_parser import ClampParser
from catalog.services.supplier_sync import ensure_supplier, clean_supplier_name


class ProductImporter(BaseImporter):
    """
    Importer for products using this excel layout:
    A: sku
    B: nombre
    C: proveedor
    D: precio
    E: stock
    F..J: filtro_1..filtro_5
    """

    POSITIONAL_COLUMNS = [
        "sku",
        "nombre",
        "proveedor",
        "precio",
        "stock",
        "filtro_1",
        "filtro_2",
        "filtro_3",
        "filtro_4",
        "filtro_5",
    ]

    def __init__(self, file):
        super().__init__(file)
        self.required_columns = ["sku", "nombre", "proveedor", "precio", "stock"]
        self._seen_skus = set()

    def load_data(self):
        """
        Support both exact headers and position-based A..J files.
        """
        super().load_data()
        columns = list(self.df.columns)

        if all(col in columns for col in self.required_columns):
            return True

        if len(columns) < 5:
            raise ValueError(
                "El archivo de productos necesita al menos 5 columnas (A:SKU, B:Nombre, C:Proveedor, D:Precio, E:Stock)."
            )

        remapped = []
        for idx, col in enumerate(columns):
            if idx < len(self.POSITIONAL_COLUMNS):
                remapped.append(self.POSITIONAL_COLUMNS[idx])
            else:
                remapped.append(col)
        self.df.columns = remapped
        return True

    @staticmethod
    def _text(value):
        if value is None or pd.isna(value):
            return ""
        return str(value).strip()

    @staticmethod
    def _int(value, default=0):
        if value is None or pd.isna(value) or str(value).strip() == "":
            return default
        return int(float(value))

    def process_row(self, row, dry_run=True):
        result = ImportRowResult(row_number=0, data=row)
        errors = []

        sku = self._text(row.get("sku"))
        name = self._text(row.get("nombre"))
        supplier = clean_supplier_name(row.get("proveedor"))
        stock = self._int(row.get("stock"), default=0)

        if not sku:
            errors.append("SKU es requerido")
        elif sku in self._seen_skus:
            errors.append("SKU duplicado dentro del archivo")
        else:
            self._seen_skus.add(sku)
        if not name:
            errors.append("Nombre es requerido")
        if not supplier:
            errors.append("Proveedor es requerido")

        try:
            price_raw = row.get("precio", 0)
            if price_raw is None or pd.isna(price_raw) or str(price_raw).strip() == "":
                raise ValueError()
            price = Decimal(str(price_raw))
            if price < 0:
                raise ValueError()
        except Exception:
            errors.append("Precio invalido")
            price = Decimal(0)

        if stock < 0:
            errors.append("Stock invalido")

        filter_1 = self._text(row.get("filtro_1"))
        filter_2 = self._text(row.get("filtro_2"))
        filter_3 = self._text(row.get("filtro_3"))
        filter_4 = self._text(row.get("filtro_4"))
        filter_5 = self._text(row.get("filtro_5"))

        if errors:
            result.success = False
            result.errors = errors
            result.action = "error"
            return result

        exists = Product.objects.filter(sku=sku).exists()
        if dry_run:
            result.success = True
            result.action = "updated" if exists else "created"
            return result

        try:
            defaults = {
                "name": name,
                "supplier": supplier,
                "supplier_ref": ensure_supplier(supplier) if supplier else None,
                "price": price,
                "stock": stock,
                "filter_1": filter_1,
                "filter_2": filter_2,
                "filter_3": filter_3,
                "filter_4": filter_4,
                "filter_5": filter_5,
            }

            active_raw = self._text(row.get("activo"))
            if active_raw:
                defaults["is_active"] = active_raw.lower() in ["si", "yes", "true", "1"]

            product, created = Product.objects.update_or_create(
                sku=sku,
                defaults=defaults,
            )

            self.check_and_run_parser(product, dry_run=dry_run)

            result.success = True
            result.action = "created" if created else "updated"
        except Exception as exc:
            result.success = False
            result.errors.append(str(exc))
            result.action = "error"

        return result

    def check_and_run_parser(self, product, dry_run=False):
        """
        Check if product is 'Abrazadera' and run parser.
        """
        if not product or not product.name:
            return

        is_clamp = product.name.upper().startswith("ABRAZADERA")
        if not is_clamp:
            primary_category = product.get_primary_category()
            if primary_category:
                is_clamp = "ABRAZADERA" in primary_category.name.upper()
            if not is_clamp:
                is_clamp = product.categories.filter(name__icontains="ABRAZADERA").exists()

        if not is_clamp:
            return

        specs_data = ClampParser.parse(product.description or product.name)
        if dry_run:
            return

        specs, _created = ClampSpecs.objects.get_or_create(product=product)
        if specs.manual_override:
            return

        specs.fabrication = specs_data.get("fabrication")
        specs.diameter = specs_data.get("diameter")
        specs.width = specs_data.get("width")
        specs.length = specs_data.get("length")
        specs.shape = specs_data.get("shape")
        specs.parse_confidence = specs_data.get("parse_confidence", 0)
        specs.parse_warnings = specs_data.get("parse_warnings", [])
        specs.save()
