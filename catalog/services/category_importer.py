
from core.services.importer import BaseImporter, ImportRowResult
from catalog.models import Category
from django.utils.text import slugify

class CategoryImporter(BaseImporter):
    """
    Importer for Categories.
    Required Headers: name
    Optional: parent, active
    """
    
    def __init__(self, file):
        super().__init__(file)
        self.required_columns = ['nombre']

    def process_row(self, row, dry_run=True):
        result = ImportRowResult(row_number=0, data=row)
        errors = []
        
        # 1. Validation
        name = str(row.get('nombre', '')).strip()
        if not name:
            errors.append("Nombre es requerido")

        if errors:
            result.success = False
            result.errors = errors
            result.action = "error"
            return result

        # 2. Logic (Update or Create)
        parent_name = str(row.get('padre', '')).strip()
        parent = None
        
        # Resolve Parent (Self-referential dependency is tricky in bulk, 
        # but simplistic approach: Parent must exist or be created now)
        if parent_name:
            parent = Category.objects.filter(name__iexact=parent_name).first()
            if not parent and not dry_run:
                # Create parent on the fly? Or error?
                # Let's create to be friendly
                parent = Category.objects.create(name=parent_name, slug=slugify(parent_name))
        
        category_exists = Category.objects.filter(name__iexact=name).exists()
        
        if dry_run:
            result.success = True
            result.action = "updated" if category_exists else "created"
            if parent_name and not parent:
                 result.errors.append(f"Info: Categoría padre '{parent_name}' no existe (se creará)")
            return result
            
        # Actual DB Operation
        try:
            defaults = {
                'parent': parent,
                'is_active': str(row.get('activo', 'si')).lower() in ['si', 'yes', 'true', '1']
            }
            
            # Since name isn't unique in DB constraint but we treat it as unique for import:
            # We use update_or_create logic based on name match
            
            # Note: Category model doesn't enforce unique names globally (usually slug), 
            # but for import we assume Name is the key.
            
            # We need to be careful not to create duplicates if slug is the same.
            slug = slugify(name)
            
            obj, created = Category.objects.update_or_create(
                slug=slug,
                defaults={**defaults, 'name': name}
            )
            
            result.success = True
            result.action = "created" if created else "updated"
            
        except Exception as e:
            result.success = False
            result.errors.append(str(e))
            result.action = "error"

        return result
