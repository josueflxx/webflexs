"""Serializers for API v1 resources."""

from rest_framework import serializers

from accounts.models import ClientProfile
from catalog.models import Category, Product
from orders.models import Order


class CategorySerializer(serializers.ModelSerializer):
    parent_name = serializers.CharField(source="parent.name", read_only=True)

    class Meta:
        model = Category
        fields = [
            "id",
            "name",
            "slug",
            "parent",
            "parent_name",
            "is_active",
            "order",
            "updated_at",
        ]


class ProductBaseSerializer(serializers.ModelSerializer):
    supplier_name = serializers.SerializerMethodField()
    primary_category = serializers.SerializerMethodField()
    category_ids = serializers.SerializerMethodField()
    catalog_visible = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "id",
            "sku",
            "name",
            "supplier_name",
            "description",
            "price",
            "stock",
            "filter_1",
            "filter_2",
            "filter_3",
            "filter_4",
            "filter_5",
            "primary_category",
            "category_ids",
            "catalog_visible",
            "updated_at",
        ]

    def get_supplier_name(self, obj):
        if obj.supplier_ref_id:
            return obj.supplier_ref.name
        return obj.supplier

    def get_primary_category(self, obj):
        category = obj.get_primary_category()
        if not category:
            return None
        return {
            "id": category.id,
            "name": category.name,
            "slug": category.slug,
        }

    def get_category_ids(self, obj):
        return [cat.id for cat in obj.get_linked_categories()]

    def get_catalog_visible(self, obj):
        return obj.is_visible_in_catalog()


class ProductStaffSerializer(ProductBaseSerializer):
    class Meta(ProductBaseSerializer.Meta):
        fields = ProductBaseSerializer.Meta.fields + [
            "cost",
            "is_active",
        ]


class ProductClientSerializer(ProductBaseSerializer):
    pass


class ClientProfileSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)
    is_active = serializers.BooleanField(source="user.is_active", read_only=True)
    current_balance = serializers.SerializerMethodField()

    class Meta:
        model = ClientProfile
        fields = [
            "id",
            "username",
            "email",
            "is_active",
            "company_name",
            "cuit_dni",
            "province",
            "address",
            "phone",
            "discount",
            "is_approved",
            "current_balance",
            "updated_at",
        ]

    def get_current_balance(self, obj):
        return obj.get_current_balance()


class OrderListSerializer(serializers.ModelSerializer):
    item_count = serializers.SerializerMethodField()
    client = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "id",
            "status",
            "priority",
            "subtotal",
            "discount_percentage",
            "discount_amount",
            "total",
            "item_count",
            "client",
            "created_at",
            "updated_at",
            "status_updated_at",
        ]

    def get_item_count(self, obj):
        return obj.get_item_count()

    def get_client(self, obj):
        user = getattr(obj, "user", None)
        if not user:
            return None
        return {
            "id": user.id,
            "username": user.username,
            "email": user.email,
        }

