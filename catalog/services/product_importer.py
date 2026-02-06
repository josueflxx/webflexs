
from core.services.importer import BaseImporter, ImportRowResult
from catalog.models import Product, Category, ClampSpecs
from catalog.services.clamp_parser import ClampParser
from django.utils.text import slugify
from decimal import Decimal

class ProductImporter(BaseImporter):
    """
    Importer for Products.
    Required Headers: sku, name, price
    Optional: description, stock, category, brand, active
    """
    
    def __init__(self, file):
        super().__init__(file)
        self.required_columns = ['sku', 'nombre', 'precio']

    def process_row(self, row, dry_run=True):
        result = ImportRowResult(row_number=0, data=row)
        errors = []
        
        # 1. Validation
        sku = str(row.get('sku', '')).strip()
        if not sku:
            errors.append("SKU es requerido")
            
        name = str(row.get('nombre', '')).strip()
        if not name:
            errors.append("Nombre es requerido")
            
        try:
            price = Decimal(str(row.get('precio', 0)))
        except:
            errors.append("Precio inválido")
            price = Decimal(0)

        # Category Lookup (by Name)
        category_name = str(row.get('categoria', '')).strip()
        category = None
        if category_name:
            # Case insensitive match or create?
            # ideally match existing, strict for now to avoid mess
            # Logic: Try to find by name, if not assume root category or error?
            # Let's try flexible search
            category = Category.objects.filter(name__iexact=category_name).first()
            if not category and not dry_run:
                # Optional: Create category if not exists? 
                # For safety, maybe better to error if cat doesn't exist to avoid typos?
                # Decision: Error if not found to ensure data quality
                pass
                # errors.append(f"Categoría '{category_name}' no encontrada")
                # Alternatively allow creation:
                # category = Category.objects.create(name=category_name, slug=slugify(category_name))
        
        if errors:
            result.success = False
            result.errors = errors
            result.action = "error"
            return result

        # 2. Logic (Update or Create)
        product_exists = Product.objects.filter(sku=sku).exists()
        
        if dry_run:
            result.success = True
            result.action = "updated" if product_exists else "created"
            if category_name and not category:
                 result.errors.append(f"Warning: Categoría '{category_name}' no encontrada (se creará o ignorará)")
            return result
            
        # Actual DB Operation
        try:
            defaults = {
                'name': name,
                'price': price,
                'description': row.get('descripcion', ''),
                'stock': int(row.get('stock', 0)),
                'stock': int(row.get('stock', 0)),
                # 'brand' removed as it is not a model field
                'is_active': str(row.get('activo', 'si')).lower() in ['si', 'yes', 'true', '1'],
            }
            
            # Handle brand in attributes
            brand = row.get('marca', '').strip()
            
            # Handle category creation if missing and configured to do so
            if category_name and not category:
                category = Category.objects.create(name=category_name, slug=slugify(category_name))
                
            product, created = Product.objects.update_or_create(
                sku=sku,
                defaults=defaults
            )
            
            if category:
                product.category = category
                product.save()
                
            # JSON Attributes Parsing
            # If there's a column 'atributos' with format "Key:Val;Key2:Val2"
            attrs_raw = row.get('atributos', '')
            
            # Initialize attributes if needed
            current_attrs = product.attributes or {}
            
            # Add brand if exists
            if brand:
                current_attrs['Marca'] = brand
                
            if attrs_raw and isinstance(attrs_raw, str):
                # simple parser
                for pair in attrs_raw.split(';'):
                    if ':' in pair:
                        k, v = pair.split(':', 1)
                        current_attrs[k.strip()] = v.strip()
                product.attributes = current_attrs
                product.save()

            # --- PHASE 4: Abrazaderas Parsing ---
            self.check_and_run_parser(product, dry_run=dry_run)

            result.success = True
            result.action = "created" if created else "updated"
            
        except Exception as e:
            result.success = False
            result.errors.append(str(e))
            result.action = "error"

        return result

    def check_and_run_parser(self, product, dry_run=False):
        """
        Check if product is 'Abrazadera' and run parser.
        """
        if not product or not product.name:
            return

        is_clamp = product.name.upper().startswith('ABRAZADERA')
        if not is_clamp and product.category:
            # Also check category name
            is_clamp = 'ABRAZADERA' in product.category.name.upper()
            
        if is_clamp:
            # Run parser
            specs_data = ClampParser.parse(product.description or product.name)
            
            if dry_run:
                # In strict implementation we might log what WOULD happen
                return 
            
            # Get or Create Specs
            specs, created = ClampSpecs.objects.get_or_create(product=product)
            
            # Check manual override
            if specs.manual_override:
                return # Do nothing
                
            # Update fields
            specs.fabrication = specs_data.get('fabrication')
            specs.diameter = specs_data.get('diameter')
            specs.width = specs_data.get('width')
            specs.length = specs_data.get('length')
            specs.shape = specs_data.get('shape')
            specs.parse_confidence = specs_data.get('parse_confidence', 0)
            specs.parse_warnings = specs_data.get('parse_warnings', [])
            specs.save()
