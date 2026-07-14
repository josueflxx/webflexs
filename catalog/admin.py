from django.contrib import admin
from .models import (
    Category, CategoryAttribute, Product, ClampSpecs, Supplier, PriceList, PriceListItem,
    ProductSupplier, SupplierCostHistory, ProductDuplicateReview,
    SupplierImportProfile, SupplierPriceListBatch, SupplierPriceListRow,
    Brand, BrandRubro, BrandSubrubro, BrandSubrubroProductOrder, BrandRubroProductOrder
)
from .services.clamp_parser import ClampParser

class CategoryAttributeInline(admin.TabularInline):
    model = CategoryAttribute
    extra = 1
    prepopulated_fields = {'slug': ('name',)}


class ClampSpecsInline(admin.StackedInline):
    model = ClampSpecs
    can_delete = False
    verbose_name_plural = 'Especificaciones de Abrazaderas'


class PriceListItemInline(admin.TabularInline):
    model = PriceListItem
    extra = 0
    autocomplete_fields = ("product",)


class ProductSupplierInline(admin.TabularInline):
    model = ProductSupplier
    extra = 0
    autocomplete_fields = ("supplier",)
    fields = (
        "supplier",
        "supplier_code",
        "current_cost",
        "currency",
        "is_preferred",
        "status",
    )

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
    inlines = [ClampSpecsInline, ProductSupplierInline]
    actions = [reparse_abrazaderas]

    def categories_display(self, obj):
        return ", ".join(obj.categories.values_list('name', flat=True)[:4]) or "-"

    categories_display.short_description = "Categorias"


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_active', 'updated_at')
    list_filter = ('is_active',)
    search_fields = ('name', 'normalized_name')


@admin.register(ProductSupplier)
class ProductSupplierAdmin(admin.ModelAdmin):
    list_display = (
        "product",
        "supplier",
        "supplier_code",
        "current_cost",
        "currency",
        "is_preferred",
        "status",
        "updated_at",
    )
    list_filter = ("status", "is_preferred", "currency", "supplier")
    search_fields = ("product__sku", "product__name", "supplier__name", "supplier_code")
    autocomplete_fields = ("product", "supplier")


@admin.register(SupplierCostHistory)
class SupplierCostHistoryAdmin(admin.ModelAdmin):
    list_display = (
        "product_supplier",
        "previous_cost",
        "new_cost",
        "difference_amount",
        "difference_percentage",
        "currency",
        "source",
        "changed_by",
        "created_at",
    )
    list_filter = ("source", "currency", "created_at")
    search_fields = ("product_supplier__product__sku", "product_supplier__supplier__name")
    readonly_fields = (
        "product_supplier",
        "previous_cost",
        "new_cost",
        "difference_amount",
        "difference_percentage",
        "currency",
        "source",
        "source_file",
        "source_row",
        "import_execution",
        "changed_by",
        "reason",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ProductDuplicateReview)
class ProductDuplicateReviewAdmin(admin.ModelAdmin):
    list_display = (
        "primary_product",
        "candidate_product",
        "reason",
        "confidence",
        "status",
        "reviewed_by",
        "updated_at",
    )
    list_filter = ("status", "reason", "confidence")
    search_fields = (
        "primary_product__sku",
        "primary_product__name",
        "candidate_product__sku",
        "candidate_product__name",
    )
    autocomplete_fields = ("primary_product", "candidate_product", "reviewed_by")


@admin.register(SupplierImportProfile)
class SupplierImportProfileAdmin(admin.ModelAdmin):
    list_display = ("name", "supplier", "sheet_name", "header_row", "default_currency", "is_active", "updated_at")
    list_filter = ("is_active", "default_currency", "supplier")
    search_fields = ("name", "supplier__name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(SupplierPriceListBatch)
class SupplierPriceListBatchAdmin(admin.ModelAdmin):
    list_display = ("id", "supplier", "company", "original_filename", "status", "created_by", "created_at", "applied_at")
    list_filter = ("status", "supplier", "company", "created_at")
    search_fields = ("original_filename", "file_sha256", "supplier__name")
    readonly_fields = (
        "supplier", "company", "profile", "import_execution", "source_file",
        "original_filename", "file_sha256", "file_size", "sheet_name", "header_row",
        "column_mapping", "default_currency", "status", "preview_signature", "summary",
        "error_message", "created_by", "applied_by", "created_at", "previewed_at",
        "applied_at", "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SupplierPriceListRow)
class SupplierPriceListRowAdmin(admin.ModelAdmin):
    list_display = ("batch", "row_number", "supplier_code", "matched_product", "change_type", "decision", "applied")
    list_filter = ("change_type", "decision", "applied", "row_type")
    search_fields = ("supplier_code", "supplier_description", "matched_product__sku", "matched_product__name")
    readonly_fields = tuple(field.name for field in SupplierPriceListRow._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PriceList)
class PriceListAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "is_active", "updated_at")
    list_filter = ("company", "is_active")
    search_fields = ("name", "company__name")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [PriceListItemInline]


@admin.register(PriceListItem)
class PriceListItemAdmin(admin.ModelAdmin):
    list_display = ("price_list", "product", "price", "updated_at")
    list_filter = ("price_list", "price_list__company")
    search_fields = ("price_list__name", "product__sku", "product__name")


class BrandRubroInline(admin.TabularInline):
    model = BrandRubro
    extra = 1
    prepopulated_fields = {'slug': ('name',)}


class BrandSubrubroInline(admin.TabularInline):
    model = BrandSubrubro
    extra = 1
    prepopulated_fields = {'slug': ('name',)}


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_active', 'order')
    list_filter = ('is_active',)
    search_fields = ('name',)
    prepopulated_fields = {'slug': ('name',)}
    inlines = [BrandRubroInline]


@admin.register(BrandRubro)
class BrandRubroAdmin(admin.ModelAdmin):
    list_display = ('name', 'brand', 'is_active', 'order')
    list_filter = ('is_active', 'brand')
    search_fields = ('name', 'brand__name')
    prepopulated_fields = {'slug': ('name',)}
    inlines = [BrandSubrubroInline]


@admin.register(BrandSubrubro)
class BrandSubrubroAdmin(admin.ModelAdmin):
    list_display = ('name', 'brand_rubro', 'is_active', 'order')
    list_filter = ('is_active', 'brand_rubro__brand', 'brand_rubro')
    search_fields = ('name', 'brand_rubro__name', 'brand_rubro__brand__name')
    prepopulated_fields = {'slug': ('name',)}
    autocomplete_fields = ('helper_categories',)


@admin.register(BrandRubroProductOrder)
class BrandRubroProductOrderAdmin(admin.ModelAdmin):
    list_display = ('brand_rubro', 'product', 'sort_order')
    list_filter = ('brand_rubro__brand', 'brand_rubro')
    search_fields = ('product__sku', 'product__name', 'brand_rubro__name')
    autocomplete_fields = ('product',)
