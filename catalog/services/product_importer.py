from decimal import Decimal
import json

from django.db import transaction

from core.services.importer import BaseImporter, ImportRowResult
from catalog.models import Category, Product, ClampSpecs, ProductSupplier
from catalog.services.clamp_parser import ClampParser
from catalog.services.import_utils import (
    is_blank,
    normalize_columns,
    normalize_header,
    normalize_sku,
    normalize_text,
    parse_bool,
    parse_decimal,
    parse_int,
    split_cell_values,
    unique_slug_for_model,
)
from catalog.services.supplier_sync import ensure_supplier, clean_supplier_name
from catalog.services.product_suppliers import upsert_product_supplier_offer


def _truthy_option(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if not text:
        return default
    return text in {"1", "true", "on", "yes", "si", "s"}


class ProductImporter(BaseImporter):
    """
    Importador tolerante para productos.

    Mantiene compatibilidad con el layout historico:
    A=SKU, B=Nombre, C=Proveedor, D=Precio, E=Stock, F..J=Filtro_1..Filtro_5.

    Tambien acepta encabezados reales de Excel como Codigo, Articulo, Venta,
    Costo, Rubro, Subrubro, Categoria, Activo, etc.
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

    SAAS_FIXED_SIGNATURE = [
        "no_de_producto",
        "estado",
        "disponible_para_la_venta",
        "disponible_para_la_compra",
        "disponible_para_integrar_otros_productos",
        "compuesto_por_otros_productos",
        "rubro",
        "nombre",
        "codigo",
        "codigo_universal_de_producto_upc",
        "codigo_de_proveedor",
        "stock_actual",
        "stock_ideal",
        "stock_minimo",
        "unidad",
        "alicuota_de_iva",
        "proveedor",
        "costo",
        "utilidad",
        "precio",
        "precio_final",
        "controla_stock",
        "stock_negativo",
        "mostrar_en_tienda",
        "no_de_publicacion_en_mercadolibre",
        "no_de_publicacion_adicional_en_mercadolibre",
        "descripcion",
        "descripcion_para_la_tienda",
        "observaciones_internas",
    ]
    SAAS_FIXED_IGNORED_COLUMN_INDEXES = {
        2,   # C - Disponible para la venta
        3,   # D - Disponible para la compra
        5,   # F - Compuesto por otros productos
        6,   # G - Rubro
        9,   # J - Codigo universal / UPC
        10,  # K - Codigo de proveedor
        11,  # L - Stock actual
        12,  # M - Stock ideal
        13,  # N - Stock minimo
        14,  # O - Unidad
        17,  # R - Costo
        18,  # S - Utilidad
        21,  # V - Controla stock
        22,  # W - Stock negativo
        23,  # X - Mostrar en tienda
        24,  # Y - Publicacion MercadoLibre
        25,  # Z - Publicacion adicional MercadoLibre
        26,  # AA - Descripcion
        27,  # AB - Descripcion para tienda
        28,  # AC - Observaciones internas
    }

    COLUMN_ALIASES = {
        "sku": "sku",
        "codigo": "sku",
        "cod": "sku",
        "cod_producto": "sku",
        "codigo_producto": "sku",
        "codigo_articulo": "sku",
        "articulo_codigo": "sku",
        "codigo_interno": "sku",
        "codigo_flexs": "sku",
        "codigo_flex": "sku",
        "cod_flexs": "sku",
        "nombre": "nombre",
        "articulo": "nombre",
        "producto": "nombre",
        "detalle": "nombre",
        "denominacion": "nombre",
        "nombre_producto": "nombre",
        "descripcion": "descripcion",
        "descripcion_larga": "descripcion",
        "detalle_largo": "descripcion",
        "descripción": "descripcion",
        "observacion": "descripcion",
        "observaciones": "descripcion",
        "proveedor": "proveedor",
        "marca": "proveedor",
        "fabricante": "proveedor",
        "fabrica": "proveedor",
        "origen": "proveedor",
        "precio": "precio",
        "precio_venta": "precio",
        "venta": "precio",
        "precio_publico": "precio",
        "precio_final": "precio_final",
        "precio_lista": "precio",
        "precio_lista_1": "precio",
        "publico": "precio",
        "pvp": "precio",
        "lista": "precio",
        "costo": "costo",
        "coste": "costo",
        "precio_costo": "costo",
        "costo_proveedor": "costo",
        "compra": "costo",
        "stock": "stock",
        "existencia": "stock",
        "existencias": "stock",
        "cantidad": "stock",
        "disponible": "stock",
        "categoria": "categoria",
        "categorias": "categorias",
        "rubro": "rubro",
        "familia": "rubro",
        "linea": "rubro",
        "subcategoria": "subcategoria",
        "subrubro": "subrubro",
        "subfamilia": "subrubro",
        "sublinea": "subrubro",
        "activo": "activo",
        "activa": "activo",
        "visible": "activo",
        "publicado": "activo",
        "publicada": "activo",
        "estado": "activo",
        "mostrar_catalogo": "activo",
        "filtro_1": "filtro_1",
        "filtro1": "filtro_1",
        "filtro_2": "filtro_2",
        "filtro2": "filtro_2",
        "filtro_3": "filtro_3",
        "filtro3": "filtro_3",
        "filtro_4": "filtro_4",
        "filtro4": "filtro_4",
        "filtro_5": "filtro_5",
        "filtro5": "filtro_5",
        "atributos": "atributos",
        "attributes": "atributos",
        "caracteristicas": "atributos",
        "diametro": "diametro",
        "diametro_mm": "diametro",
        "diametro_pulgadas": "diametro",
        "ancho": "ancho",
        "ancho_mm": "ancho",
        "largo": "largo",
        "largo_mm": "largo",
        "forma": "forma",
        "curvatura": "forma",
        "fabricacion": "fabricacion",
        "tipo_abrazadera": "fabricacion",
        "material": "material",
        "terminacion": "terminacion",
        "zincado": "zincado",
        "posicion": "posicion",
        "aplicacion": "aplicacion",
        "vehiculo": "vehiculo",
        "modelo": "modelo",
        "codigo_oem": "codigo_oem",
        "oem": "codigo_oem",
        "codigo_referencia": "codigo_referencia",
        "referencia": "codigo_referencia",
        "ubicacion": "ubicacion",
        "unidad": "unidad",
        "no_de_producto": "numero_saas",
        "n_de_producto": "numero_saas",
        "numero_de_producto": "numero_saas",
        "nro_de_producto": "numero_saas",
        "codigo_universal_de_producto_upc": "upc",
        "codigo_upc": "upc",
        "upc": "upc",
        "codigo_de_proveedor": "codigo_proveedor",
        "codigo_proveedor": "codigo_proveedor",
        "cod_proveedor": "codigo_proveedor",
        "stock_actual": "stock_saas",
        "stock_ideal": "stock_ideal",
        "stock_minimo": "stock_minimo",
        "alicuota_de_iva": "iva",
        "iva": "iva",
        "utilidad": "utilidad_saas",
        "utilidad_pct": "utilidad_saas",
        "descripcion_para_la_tienda": "descripcion_tienda",
        "observaciones_internas": "observaciones_internas",
        "disponible_para_la_venta": "disponible_venta",
        "disponible_para_la_compra": "disponible_compra",
        "mostrar_en_tienda": "mostrar_tienda",
        "controla_stock": "controla_stock",
        "stock_negativo": "stock_negativo",
        "características": "atributos",
    }

    ATTRIBUTE_COLUMNS = {
        "diametro": "Diametro",
        "ancho": "Ancho",
        "largo": "Largo",
        "forma": "Forma",
        "fabricacion": "Fabricacion",
        "material": "Material",
        "terminacion": "Terminacion",
        "zincado": "Zincado",
        "posicion": "Posicion",
        "aplicacion": "Aplicacion",
        "vehiculo": "Vehiculo",
        "modelo": "Modelo",
        "codigo_oem": "Codigo OEM",
        "codigo_referencia": "Codigo referencia",
        "ubicacion": "Ubicacion",
        "unidad": "Unidad",
        "numero_saas": "Numero SaaS",
        "upc": "UPC",
        "codigo_proveedor": "Codigo proveedor",
        "iva": "IVA",
        "utilidad_saas": "Utilidad SaaS",
        "stock_saas": "Stock SaaS",
        "stock_ideal": "Stock ideal SaaS",
        "stock_minimo": "Stock minimo SaaS",
        "descripcion_tienda": "Descripcion tienda SaaS",
        "observaciones_internas": "Observaciones internas SaaS",
    }
    DUPLICATE_FLAG_KEY = "Duplicado en importacion"
    DUPLICATE_COUNT_KEY = "Duplicados importacion"
    DUPLICATE_DETAIL_KEY = "Detalle duplicados importacion"
    DUPLICATE_ORIGINAL_ROW_KEY = "Fila original importacion"
    CATEGORY_MODE_IGNORE = "ignore"
    CATEGORY_MODE_EXISTING = "existing"
    CATEGORY_MODE_HIDDEN = "hidden"
    CATEGORY_MODE_CREATE = "create"
    CATEGORY_MODES = {
        CATEGORY_MODE_IGNORE,
        CATEGORY_MODE_EXISTING,
        CATEGORY_MODE_HIDDEN,
        CATEGORY_MODE_CREATE,
    }
    UPDATE_MODE_COMMERCIAL = "commercial"
    UPDATE_MODE_PRICES = "prices"
    UPDATE_MODE_CREATE_ONLY = "create_only"
    UPDATE_MODES = {
        UPDATE_MODE_COMMERCIAL,
        UPDATE_MODE_PRICES,
        UPDATE_MODE_CREATE_ONLY,
    }

    def __init__(
        self,
        file,
        category_mode=None,
        preserve_existing_categories=True,
        allow_category_creation=False,
        update_mode=None,
        is_global_base=False,
    ):
        super().__init__(file)
        self.required_columns = ["sku"]
        self._seen_skus = {}
        self._seen_row_data = {}
        self.column_mapping_mode = "headers"
        self.is_global_base = _truthy_option(is_global_base, default=False)
        self.update_mode = update_mode or self.UPDATE_MODE_COMMERCIAL
        if self.update_mode not in self.UPDATE_MODES:
            self.update_mode = self.UPDATE_MODE_COMMERCIAL
        self.category_mode = category_mode or self.CATEGORY_MODE_EXISTING
        if self.category_mode not in self.CATEGORY_MODES:
            self.category_mode = self.CATEGORY_MODE_EXISTING
        self.preserve_existing_categories = _truthy_option(
            preserve_existing_categories,
            default=True,
        )
        self.allow_category_creation = _truthy_option(
            allow_category_creation,
            default=False,
        )
        if (
            self.category_mode in {self.CATEGORY_MODE_HIDDEN, self.CATEGORY_MODE_CREATE}
            and not self.allow_category_creation
        ):
            self.category_mode = self.CATEGORY_MODE_EXISTING

    def load_data(self):
        super().load_data()
        self._drop_ignored_saas_fixed_columns()
        self.df = self.df.dropna(how="all")
        mapped_columns, mapping_mode = normalize_columns(
            self.df.columns,
            self.COLUMN_ALIASES,
            positional_columns=self.POSITIONAL_COLUMNS,
            required_any={"sku", "nombre", "precio", "precio_final"},
        )
        self.df.columns = mapped_columns
        self.column_mapping_mode = mapping_mode
        return True

    def _drop_ignored_saas_fixed_columns(self):
        normalized_headers = [normalize_header(column) for column in self.df.columns]
        if normalized_headers[: len(self.SAAS_FIXED_SIGNATURE)] != self.SAAS_FIXED_SIGNATURE:
            return

        kept_columns = [
            column
            for index, column in enumerate(self.df.columns)
            if index not in self.SAAS_FIXED_IGNORED_COLUMN_INDEXES
        ]
        self.df = self.df.loc[:, kept_columns]

    @staticmethod
    def _text(value):
        return normalize_text(value)

    def _field_present(self, row, key):
        return key in row

    def _get_name(self, row, existing=None):
        name = self._text(row.get("nombre"))
        if name:
            return name
        description = self._text(row.get("descripcion"))
        if description:
            return description
        return existing.name if existing else ""

    def _get_category_names(self, row):
        names = []
        names.extend(split_cell_values(row.get("categorias")))
        names.extend(split_cell_values(row.get("categoria")))
        rubro = self._text(row.get("rubro"))
        rubro_names = [rubro] if rubro else []
        if any("abrazadera" in normalize_header(name) for name in rubro_names):
            names.append("ABRAZADERAS")
        names.extend(rubro_names)
        subcategory = self._text(row.get("subrubro") or row.get("subcategoria"))
        if subcategory:
            names.append(subcategory)
        return list(dict.fromkeys(names))

    def _get_parent_category_name(self, row):
        rubro = self._text(row.get("rubro"))
        subrubro = self._text(row.get("subrubro") or row.get("subcategoria"))
        if "abrazadera" in normalize_header(rubro):
            return "ABRAZADERAS"
        if rubro and subrubro:
            return rubro
        return ""

    def _resolve_category(self, name, parent=None, dry_run=True):
        if not name:
            return None
        qs = Category.objects.filter(name__iexact=name)
        if parent:
            qs = qs.filter(parent=parent)
        category = qs.first()
        if category or dry_run or self.category_mode in {
            self.CATEGORY_MODE_IGNORE,
            self.CATEGORY_MODE_EXISTING,
        }:
            return category

        category_defaults = {
            "name": name,
            "parent": parent,
            "slug": unique_slug_for_model(Category, name),
            "is_active": True,
        }
        if self.category_mode == self.CATEGORY_MODE_HIDDEN:
            category_defaults.update({
                "is_active": False,
                "visible_in_catalog": False,
            })
        return Category.objects.create(
            **category_defaults,
        )

    def _assign_categories(self, product, row, dry_run=True):
        if self.category_mode == self.CATEGORY_MODE_IGNORE:
            return []

        category_names = self._get_category_names(row)
        if not category_names:
            return []

        parent = None
        parent_name = self._get_parent_category_name(row)
        missing = []
        if parent_name:
            parent = self._resolve_category(parent_name, dry_run=dry_run)
            if not parent:
                missing.append(parent_name)

        resolved = []
        for name in category_names:
            if parent and normalize_header(name) == normalize_header(parent.name):
                category = parent
            else:
                category = self._resolve_category(name, parent=parent, dry_run=dry_run)
            if category:
                resolved.append(category)
            else:
                missing.append(name)

        if dry_run or not resolved:
            return list(dict.fromkeys(missing))

        product.categories.add(*resolved)
        if product.category_id is None:
            product.category = resolved[-1]
            product.save(update_fields=["category", "updated_at"])
        return list(dict.fromkeys(missing))

    def _parse_price(self, row, existing, errors):
        raw = row.get("precio_final")
        field_label = "Precio final"
        if is_blank(raw):
            raw = row.get("precio")
            field_label = "Precio"
        if is_blank(raw):
            if existing:
                return None
            errors.append("Precio requerido para producto nuevo")
            return None
        try:
            return parse_decimal(raw, field_label=field_label, min_value=0)
        except ValueError as exc:
            errors.append(str(exc))
            return None

    def _parse_cost(self, row, errors):
        raw = row.get("costo")
        if is_blank(raw):
            return None
        try:
            return parse_decimal(raw, field_label="Costo", min_value=0)
        except ValueError as exc:
            errors.append(str(exc))
            return None

    def _parse_stock(self, row, existing, errors):
        raw = row.get("stock")
        if is_blank(raw):
            return None if existing else 0
        try:
            return parse_int(raw, field_label="Stock", min_value=0)
        except ValueError as exc:
            errors.append(str(exc))
            return None

    def _parse_attributes(self, row, errors):
        attributes = {}
        raw = row.get("atributos")

        if isinstance(raw, dict):
            attributes.update(raw)
        else:
            text = "" if is_blank(raw) else self._text(raw)
            if text:
                if text.startswith("{") and text.endswith("}"):
                    try:
                        parsed = json.loads(text)
                        if isinstance(parsed, dict):
                            attributes.update(parsed)
                        else:
                            errors.append("Atributos JSON debe ser un objeto")
                            return None
                    except json.JSONDecodeError:
                        errors.append("Atributos JSON invalido")
                        return None
                else:
                    for part in text.replace("|", ";").split(";"):
                        if ":" not in part:
                            continue
                        key, value = part.split(":", 1)
                        key = self._text(key)
                        value = self._text(value)
                        if key:
                            attributes[key] = value

        for row_key, label in self.ATTRIBUTE_COLUMNS.items():
            value = self._text(row.get(row_key))
            if value:
                attributes[label] = value

        if self._field_present(row, "precio_final"):
            net_price = self._text(row.get("precio"))
            final_price = self._text(row.get("precio_final"))
            if net_price:
                attributes["Precio neto SaaS"] = net_price
            if final_price:
                attributes["Precio final SaaS"] = final_price
            if (
                self._text(row.get("numero_saas"))
                or self._text(row.get("codigo_proveedor"))
                or final_price
            ):
                attributes["Origen importacion"] = "SaaS Argentina"

        return attributes or None

    def _build_duplicate_result_data(self, sku, source_row_number, public_row_data):
        return {
            **public_row_data,
            "_duplicate_sku": sku,
            "_duplicate_first_row": self._seen_skus.get(sku, ""),
            "_duplicate_row": source_row_number,
            "_duplicate_first_data": self._seen_row_data.get(sku, {}),
        }

    def _format_duplicate_detail(self, source_row_number, public_row_data, first_row):
        name = self._text(public_row_data.get("nombre") or public_row_data.get("descripcion")) or "-"
        supplier = self._text(public_row_data.get("proveedor")) or "-"
        rubro = self._text(public_row_data.get("rubro") or public_row_data.get("categoria")) or "-"
        price = self._text(public_row_data.get("precio_final") or public_row_data.get("precio")) or "-"
        return (
            f"Fila {source_row_number} contra fila {first_row}: "
            f"{name} | Proveedor: {supplier} | Rubro: {rubro} | Precio final: {price}"
        )

    def _register_duplicate_warning(self, sku, source_row_number, public_row_data):
        if not sku:
            return
        product = Product.objects.filter(sku=sku).first()
        if not product:
            return

        first_row = self._seen_skus.get(sku, "")
        attrs = dict(product.attributes or {})
        detail = self._format_duplicate_detail(source_row_number, public_row_data, first_row)
        existing_details = self._text(attrs.get(self.DUPLICATE_DETAIL_KEY))
        detail_lines = [line for line in existing_details.split("\n") if line.strip()]
        if detail not in detail_lines:
            detail_lines.append(detail)
        detail_lines = detail_lines[-25:]

        attrs[self.DUPLICATE_FLAG_KEY] = "Si"
        attrs[self.DUPLICATE_COUNT_KEY] = str(len(detail_lines))
        attrs[self.DUPLICATE_ORIGINAL_ROW_KEY] = str(first_row)
        attrs[self.DUPLICATE_DETAIL_KEY] = "\n".join(detail_lines)
        product.attributes = attrs
        product.save(update_fields=["attributes", "updated_at"])

    def process_row(self, row, dry_run=True):
        if dry_run:
            return self._process_row(row, dry_run=True)
        with transaction.atomic():
            result = self._process_row(row, dry_run=False)
            if not result.success:
                transaction.set_rollback(True)
            return result

    def _process_row(self, row, dry_run=True):
        source_row_number = row.get("__row_number")
        public_row_data = {key: value for key, value in dict(row).items() if key != "__row_number"}
        result = ImportRowResult(row_number=0, data=public_row_data)
        errors = []

        sku = normalize_sku(row.get("sku"))
        if not sku:
            errors.append("SKU es requerido")
        elif sku in self._seen_skus:
            message = f"SKU duplicado dentro del archivo; primera aparicion en fila {self._seen_skus[sku]}"
            result.data = self._build_duplicate_result_data(sku, source_row_number, public_row_data)
            result.success = True
            result.errors = [message]
            result.action = "skipped"
            if not dry_run:
                self._register_duplicate_warning(sku, source_row_number, public_row_data)
            return result
        else:
            self._seen_skus[sku] = source_row_number or len(self._seen_skus) + 2
            self._seen_row_data[sku] = public_row_data

        existing = Product.objects.filter(sku=sku).first() if sku else None
        if existing and self.update_mode == self.UPDATE_MODE_CREATE_ONLY:
            result.data = {
                "sku": sku,
                "nombre": existing.name,
                "modo_actualizacion": self.update_mode,
                "omitido": "Producto existente; modo crear nuevos solamente.",
            }
            result.success = True
            result.action = "skipped"
            return result

        name = self._get_name(row, existing=existing)
        if not name:
            errors.append("Nombre es requerido")

        price = self._parse_price(row, existing, errors)
        cost = self._parse_cost(row, errors)
        stock = self._parse_stock(row, existing, errors)
        attributes = self._parse_attributes(row, errors)
        supplier = "" if is_blank(row.get("proveedor")) else clean_supplier_name(row.get("proveedor"))
        supplier_code = self._text(row.get("codigo_proveedor"))
        active = parse_bool(row.get("activo"), default=None)
        if self._field_present(row, "activo") and not is_blank(row.get("activo")) and active is None:
            errors.append("Activo invalido: usa SI/NO, ACTIVO/INACTIVO, X o BAJA")

        if errors:
            result.success = False
            result.errors = errors
            result.action = "error"
            return result

        result.data = {
            "sku": sku,
            "nombre": name,
            "proveedor": supplier,
            "codigo_proveedor": supplier_code,
            "precio": str(price) if price is not None else "",
            "costo": str(cost) if cost is not None else "",
            "stock": stock if stock is not None else "",
            "categorias": self._get_category_names(row),
            "modo_categorias": self.category_mode,
            "modo_actualizacion": self.update_mode,
            "preservar_categorias_existentes": self.preserve_existing_categories,
            "atributos": attributes or {},
        }
        preserve_categories = bool(existing and self.preserve_existing_categories)

        if dry_run:
            if preserve_categories:
                result.data["categorias_preservadas"] = True
            else:
                missing_categories = self._assign_categories(None, row, dry_run=True)
                if missing_categories:
                    result.data["categorias_no_encontradas"] = missing_categories
            result.success = True
            result.action = "updated" if existing else "created"
            return result

        try:
            if existing:
                product = existing
                update_fields = []

                if price is not None and product.price != price:
                    product.price = price
                    update_fields.append("price")

                if cost is not None and product.cost != cost:
                    product.cost = cost
                    update_fields.append("cost")

                if self.update_mode != self.UPDATE_MODE_PRICES:
                    if name and product.name != name:
                        product.name = name
                        update_fields.append("name")

                    description = self._text(row.get("descripcion"))
                    if description and product.description != description:
                        product.description = description
                        update_fields.append("description")

                    if supplier:
                        supplier_ref = ensure_supplier(supplier)
                        if product.supplier != supplier:
                            product.supplier = supplier
                            update_fields.append("supplier")
                        if product.supplier_ref_id != getattr(supplier_ref, "id", None):
                            product.supplier_ref = supplier_ref
                            update_fields.append("supplier_ref")

                    if stock is not None and product.stock != stock:
                        product.stock = stock
                        update_fields.append("stock")

                    if active is not None and product.is_active != active:
                        product.is_active = active
                        update_fields.append("is_active")

                    if attributes is not None:
                        merged_attributes = {
                            **(product.attributes or {}),
                            **attributes,
                        }
                        if product.attributes != merged_attributes:
                            product.attributes = merged_attributes
                            update_fields.append("attributes")

                    filter_map = {
                        "filter_1": "filtro_1",
                        "filter_2": "filtro_2",
                        "filter_3": "filtro_3",
                        "filter_4": "filtro_4",
                        "filter_5": "filtro_5",
                    }
                    for model_field, row_key in filter_map.items():
                        value = self._text(row.get(row_key))
                        if value and getattr(product, model_field) != value:
                            setattr(product, model_field, value)
                            update_fields.append(model_field)

                if update_fields:
                    update_fields.append("updated_at")
                    product.save(update_fields=list(dict.fromkeys(update_fields)))

                created = False
            else:
                product = Product.objects.create(
                    sku=sku,
                    name=name,
                    description=self._text(row.get("descripcion")),
                    supplier=supplier,
                    supplier_ref=ensure_supplier(supplier) if supplier else None,
                    cost=cost if cost is not None else Decimal("0.00"),
                    price=price if price is not None else Decimal("0.00"),
                    stock=stock if stock is not None else 0,
                    is_active=True if active is None else active,
                    filter_1=self._text(row.get("filtro_1")),
                    filter_2=self._text(row.get("filtro_2")),
                    filter_3=self._text(row.get("filtro_3")),
                    filter_4=self._text(row.get("filtro_4")),
                    filter_5=self._text(row.get("filtro_5")),
                    attributes=attributes or {},
                )
                created = True

            if existing and self.update_mode == self.UPDATE_MODE_PRICES:
                result.data["categorias_preservadas"] = True
                result.data["orden_manual_preservado"] = True
            elif not (existing and self.preserve_existing_categories):
                missing_categories = self._assign_categories(product, row, dry_run=False)
                if missing_categories:
                    result.data["categorias_no_encontradas"] = missing_categories
            else:
                result.data["categorias_preservadas"] = True
            if self.update_mode != self.UPDATE_MODE_PRICES:
                self.check_and_run_parser(product, dry_run=dry_run)

            if product.supplier_ref_id:
                source_file = str(getattr(self.file, "name", "") or "")
                existing_offer = ProductSupplier.objects.filter(
                    product=product,
                    supplier_id=product.supplier_ref_id,
                ).first()
                upsert_product_supplier_offer(
                    product=product,
                    supplier=product.supplier_ref,
                    current_cost=product.cost,
                    currency=existing_offer.currency if existing_offer else ProductSupplier.CURRENCY_ARS,
                    supplier_code=supplier_code or (existing_offer.supplier_code if existing_offer else ""),
                    supplier_description=(existing_offer.supplier_description if existing_offer else ""),
                    discount_percentage=(existing_offer.discount_percentage if existing_offer else 0),
                    bonus_percentage=(existing_offer.bonus_percentage if existing_offer else 0),
                    tax_percentage=(existing_offer.tax_percentage if existing_offer else 0),
                    minimum_purchase_quantity=(
                        existing_offer.minimum_purchase_quantity if existing_offer else 1
                    ),
                    is_available=existing_offer.is_available if existing_offer else True,
                    lead_time_days=existing_offer.lead_time_days if existing_offer else 0,
                    price_list_date=existing_offer.price_list_date if existing_offer else None,
                    source="product_import",
                    source_file=source_file,
                    source_row=int(source_row_number) if source_row_number is not None else None,
                    reason="Costo informado por importacion de productos.",
                    is_preferred=True,
                    match_method="supplier_name_and_product_sku",
                )

            result.success = True
            result.action = "created" if created else "updated"
        except Exception as exc:
            result.success = False
            result.errors.append(str(exc))
            result.action = "error"

        return result

    def run(self, dry_run=True, progress_callback=None):
        results = super().run(dry_run=dry_run, progress_callback=progress_callback)
        
        if self.is_global_base:
            seen_skus = set(self._seen_skus.keys())
            omitted_qs = Product.objects.filter(is_active=True).exclude(sku__in=seen_skus)
            
            deactivated_count = 0
            deactivated_skus = []
            
            if not dry_run:
                deactivated_skus = list(omitted_qs.values_list('sku', flat=True))
                deactivated_count = len(deactivated_skus)
                
                archived_category, _ = Category.objects.get_or_create(
                    name="Bajas por Importación",
                    defaults={
                        'slug': 'bajas-por-importacion',
                        'is_active': False,
                        'visible_in_catalog': False,
                    }
                )
                
                from django.db import transaction
                with transaction.atomic():
                    for product in omitted_qs:
                        product.is_active = False
                        product.categories.clear()
                        product.category = archived_category
                        product.categories.add(archived_category)
                        product.save()
            else:
                deactivated_skus = list(omitted_qs.values_list('sku', flat=True))
                deactivated_count = len(deactivated_skus)
                
            results.deactivated_count = deactivated_count
            results.deactivated_skus = deactivated_skus
            
        return results

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
