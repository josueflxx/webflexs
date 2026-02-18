
from core.services.importer import BaseImporter, ImportRowResult
from catalog.models import Product, Category, ClampSpecs
from catalog.services.clamp_parser import ClampParser
from django.utils.text import slugify
from decimal import Decimal

class AbrazaderaImporter(BaseImporter):
    """
    Importador dedicado para Abrazaderas.
    Columnas esperadas: 
    - DESCRIPCION (Nombre)
    - CODIGO (SKU)
    - PRECIO
    
    Lógica:
    1. Importa/Actualiza Producto
    2. Asigna Categoría "Abrazaderas" (auto-creación)
    3. Ejecuta ClampParser sobre la descripción
    4. Guarda ClampSpecs
    """
    
    def __init__(self, file):
        super().__init__(file)
        # Map user columns to internal/standard names or just check them manually
        # Expected from requirement: "DESCRIPICION", "CODIGO", "PRECIO" (case insensitive usually in base class?)
        # BaseImporter checks normalized headers usually. Let's assume headers are reasonably standard.
        # User said: "La colmuna que sigue es el codigo y despues el precio"
        # Let's enforce these columns.
        self.required_columns = ['descripcion', 'codigo', 'precio']

    def process_row(self, row, dry_run=True):
        result = ImportRowResult(row_number=0, data=row)
        errors = []
        
        # 1. Extract Data
        # Keys are lowercased by BaseImporter usually? Assuming dict keys from pandas are cleaned.
        # Let's be safe and check variants if BaseImporter doesn't standardized perfectly, 
        # but BaseImporter typically normalizes to lowercase.
        
        description = str(row.get('descripcion', '')).strip()
        sku = str(row.get('codigo', '')).strip()
        try:
            price = Decimal(str(row.get('precio', 0)))
        except:
            price = Decimal(0)
            errors.append("Precio inválido")

        if not sku:
            errors.append("Falta CODIGO")
        if not description:
            errors.append("Falta DESCRIPCION")

        if errors:
            result.success = False
            result.errors = errors
            result.action = "error"
            return result

        # 2. Category Logic
        category_name = "Abrazaderas"
        # In a real run, we need the object
        
        if dry_run:
            result.success = True
            result.action = "updated" if Product.objects.filter(sku=sku).exists() else "created"
            # Parse check (simulation)
            parsed = ClampParser.parse(description)
            if parsed['parse_confidence'] < 100:
                warnings = "; ".join(parsed['parse_warnings'])
                result.errors.append(f"Parser Warnings: {warnings}")
            return result

        # 3. Real Import
        try:
            # Ensure category exists
            category, _ = Category.objects.get_or_create(
                name=category_name,
                defaults={'slug': slugify(category_name)}
            )

            # Update/Create Product
            defaults = {
                'name': description,
                'price': price,
                'description': description, # Use name as description too? Or just empty? User said description is the text source.
                'category': category
            }
            
            product, created = Product.objects.update_or_create(
                sku=sku,
                defaults=defaults
            )
            product.categories.add(category)
            
            # 4. Run Parser
            specs_data = ClampParser.parse(description)
            
            # Save Specs
            specs, _ = ClampSpecs.objects.get_or_create(product=product)
            
            # Only update if not manual override
            if not specs.manual_override:
                specs.fabrication = specs_data.get('fabrication')
                specs.diameter = specs_data.get('diameter')
                specs.width = specs_data.get('width')
                specs.length = specs_data.get('length')
                specs.shape = specs_data.get('shape')
                specs.parse_confidence = specs_data.get('parse_confidence', 0)
                specs.parse_warnings = specs_data.get('parse_warnings', [])
                specs.save()
            
            result.success = True
            result.action = "created" if created else "updated"
            result.created = created
            result.updated = not created
            
        except Exception as e:
            result.success = False
            result.errors.append(str(e))
            result.action = "error"
            
        return result
