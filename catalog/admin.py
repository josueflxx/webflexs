from django.contrib import admin
from .models import Category, CategoryAttribute, Product, ClampSpecs, Supplier
from .services.clamp_parser import ClampParser

class CategoryAttributeInline(admin.TabularInline):
    model = CategoryAttribute
    extra = 1
    prepopulated_fields = {'slug': ('name',)}

class ClampSpecsInline(admin.StackedInline):
    model = ClampSpecs
    can_delete = False
    verbose_name_plural = 'Especificaciones de Abrazaderas'

@admin.action(description='Reparsear especificaciones de Abrazaderas')
def reparse_abrazaderas(modeladmin, request, queryset):
    count = 0
    for product in queryset:
        is_clamp = product.name.upper().startswith('ABRAZADERA')
        if not is_clamp:
            primary_category = product.get_primary_category()
            if primary_category:
                is_clamp = 'ABRAZADERA' in primary_category.name.upper()
            if not is_clamp:
                is_clamp = product.categories.filter(name__icontains='ABRAZADERA').exists()
            
        if is_clamp:
            specs_data = ClampParser.parse(product.description or product.name)
            specs, created = ClampSpecs.objects.get_or_create(product=product)
            
            if not specs.manual_override:
                specs.fabrication = specs_data.get('fabrication')
                specs.diameter = specs_data.get('diameter')
                specs.width = specs_data.get('width')
                specs.length = specs_data.get('length')
                specs.shape = specs_data.get('shape')
                specs.parse_confidence = specs_data.get('parse_confidence', 0)
                specs.parse_warnings = specs_data.get('parse_warnings', [])
                specs.save()
                count += 1
                
    modeladmin.message_user(request, f"{count} productos re-parseados exitosamente.")

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'parent', 'is_active', 'order')
    list_filter = ('is_active', 'parent')
    search_fields = ('name',)
    prepopulated_fields = {'slug': ('name',)}
    inlines = [CategoryAttributeInline]

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('sku', 'name', 'supplier', 'supplier_ref', 'category', 'categories_display', 'price', 'stock', 'is_active')
    list_filter = ('supplier_ref', 'category', 'categories', 'is_active')
    search_fields = ('sku', 'name', 'supplier', 'description')
    readonly_fields = ('created_at', 'updated_at')
    inlines = [ClampSpecsInline]
    actions = [reparse_abrazaderas]

    def categories_display(self, obj):
        return ", ".join(obj.categories.values_list('name', flat=True)[:4]) or "-"

    categories_display.short_description = "Categorias"


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_active', 'updated_at')
    list_filter = ('is_active',)
    search_fields = ('name', 'normalized_name')
