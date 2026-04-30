from catalog.services.clamp_parser import ClampParser
from catalog.services.import_utils import normalize_header, normalize_sku
from catalog.services.product_importer import ProductImporter
from core.services.importer import ImportRowResult


class AbrazaderaImporter(ProductImporter):
    """
    Importador especializado para abrazaderas.

    Acepta tanto el formato historico:
    DESCRIPCION | CODIGO | PRECIO

    como formatos de producto:
    sku/codigo | nombre/articulo/descripcion | precio | stock | categoria.
    """

    COLUMN_ALIASES = {
        **ProductImporter.COLUMN_ALIASES,
        "descripcion": "descripcion",
        "descripicion": "descripcion",
        "descrip": "descripcion",
        "codigo": "sku",
        "cod": "sku",
    }

    def _looks_like_clamp(self, row):
        searchable = " ".join(
            self._text(row.get(key))
            for key in ("rubro", "categoria", "categorias", "subrubro", "nombre", "descripcion")
        )
        normalized = normalize_header(searchable)
        if "abrazadera" in normalized:
            return True

        sku = normalize_sku(row.get("sku")).upper()
        return sku.startswith(("ABT", "ABL", "ABF"))

    def _get_category_names(self, row):
        names = super()._get_category_names(row)
        normalized = {normalize_header(name) for name in names}
        if "abrazaderas" not in normalized:
            names.append("Abrazaderas")
        return names

    def process_row(self, row, dry_run=True):
        if not self._looks_like_clamp(row):
            return ImportRowResult(
                row_number=0,
                data={
                    "sku": normalize_sku(row.get("sku")),
                    "nombre": self._text(row.get("nombre") or row.get("descripcion")),
                    "motivo": "Fila omitida: no parece una abrazadera.",
                },
                success=True,
                action="skipped",
            )

        result = super().process_row(row, dry_run=dry_run)
        if result.success and dry_run:
            description = self._text(row.get("descripcion")) or self._text(row.get("nombre"))
            if description:
                parsed = ClampParser.parse(description)
                warnings = parsed.get("parse_warnings") or []
                if warnings:
                    result.data = {
                        **(result.data or {}),
                        "parser_warnings": "; ".join(warnings),
                        "parse_confidence": parsed.get("parse_confidence", 0),
                    }
        return result
