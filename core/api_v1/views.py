"""API v1 read-only endpoints."""

from django.conf import settings
from django.db.models import Q
from rest_framework import generics, permissions
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from accounts.models import ClientProfile
from catalog.models import Category, Product
from core.api_v1.permissions import IsStaffUser
from core.api_v1.serializers import (
    CategorySerializer,
    ClientProfileSerializer,
    OrderListSerializer,
    ProductClientSerializer,
    ProductStaffSerializer,
)
from orders.models import Order


def _parse_bool_param(raw_value):
    if raw_value is None:
        return None
    value = str(raw_value).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return None


class ApiV1BaseListView(generics.ListAPIView):
    """Common list behavior for API v1 endpoints."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "api_v1_default"


class ApiHealthView(APIView):
    """Lightweight health endpoint for smoke checks and deploy validation."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "api_v1_admin"

    def get(self, request):
        return Response(
            {
                "ok": True,
                "api_version": "v1",
                "feature_api_v1_enabled": bool(getattr(settings, "FEATURE_API_V1_ENABLED", False)),
            }
        )


class ApiCategoryListView(ApiV1BaseListView):
    serializer_class = CategorySerializer
    throttle_scope = "api_v1_catalog"

    def get_queryset(self):
        queryset = Category.objects.select_related("parent").order_by("order", "name")

        user = self.request.user
        if not user.is_staff:
            queryset = queryset.filter(is_active=True)

        q = (self.request.query_params.get("q") or "").strip()
        if q:
            queryset = queryset.filter(
                Q(name__icontains=q)
                | Q(slug__icontains=q)
                | Q(parent__name__icontains=q)
            )

        parent = self.request.query_params.get("parent")
        if parent:
            if parent == "root":
                queryset = queryset.filter(parent__isnull=True)
            elif str(parent).isdigit():
                queryset = queryset.filter(parent_id=int(parent))

        active_param = _parse_bool_param(self.request.query_params.get("active"))
        if active_param is not None:
            queryset = queryset.filter(is_active=active_param)

        return queryset


class ApiProductListView(ApiV1BaseListView):
    throttle_scope = "api_v1_catalog"

    def get_queryset(self):
        queryset = Product.objects.select_related("category", "supplier_ref").prefetch_related("categories").order_by("name")

        user = self.request.user
        if user.is_staff:
            active_param = _parse_bool_param(self.request.query_params.get("active"))
            if active_param is not None:
                queryset = queryset.filter(is_active=active_param)
        else:
            queryset = Product.catalog_visible(queryset=queryset, include_uncategorized=True)

        q = (self.request.query_params.get("q") or "").strip()
        if q:
            queryset = queryset.filter(
                Q(sku__icontains=q)
                | Q(name__icontains=q)
                | Q(supplier__icontains=q)
                | Q(supplier_ref__name__icontains=q)
                | Q(filter_1__icontains=q)
                | Q(filter_2__icontains=q)
                | Q(filter_3__icontains=q)
                | Q(filter_4__icontains=q)
                | Q(filter_5__icontains=q)
            )

        supplier = (self.request.query_params.get("supplier") or "").strip()
        if supplier:
            queryset = queryset.filter(
                Q(supplier__icontains=supplier)
                | Q(supplier_ref__name__icontains=supplier)
            )

        category = (self.request.query_params.get("category") or "").strip()
        if category:
            category_filters = Q(category__slug=category) | Q(categories__slug=category)
            if category.isdigit():
                category_id = int(category)
                category_filters |= Q(category_id=category_id) | Q(categories__id=category_id)
            queryset = queryset.filter(category_filters).distinct()

        in_stock_param = _parse_bool_param(self.request.query_params.get("in_stock"))
        if in_stock_param is True:
            queryset = queryset.filter(stock__gt=0)
        elif in_stock_param is False:
            queryset = queryset.filter(stock__lte=0)

        return queryset

    def get_serializer_class(self):
        if self.request.user.is_staff:
            return ProductStaffSerializer
        return ProductClientSerializer


class ApiClientListView(ApiV1BaseListView):
    permission_classes = [IsStaffUser]
    serializer_class = ClientProfileSerializer
    throttle_scope = "api_v1_admin"

    def get_queryset(self):
        queryset = ClientProfile.objects.select_related("user").order_by("company_name")

        q = (self.request.query_params.get("q") or "").strip()
        if q:
            queryset = queryset.filter(
                Q(company_name__icontains=q)
                | Q(cuit_dni__icontains=q)
                | Q(user__username__icontains=q)
                | Q(user__email__icontains=q)
            )

        approved_param = _parse_bool_param(self.request.query_params.get("approved"))
        if approved_param is not None:
            queryset = queryset.filter(is_approved=approved_param)

        return queryset


class ApiMyClientProfileView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "api_v1_default"

    def get(self, request):
        profile = (
            ClientProfile.objects.select_related("user")
            .filter(user=request.user)
            .first()
        )
        if not profile:
            return Response({"detail": "Perfil de cliente no encontrado."}, status=404)
        serializer = ClientProfileSerializer(profile, context={"request": request})
        return Response(serializer.data)


class ApiOrderListView(ApiV1BaseListView):
    serializer_class = OrderListSerializer
    throttle_scope = "api_v1_default"

    def get_queryset(self):
        user = self.request.user
        queryset = Order.objects.select_related("user").prefetch_related("items").order_by("-created_at")

        if not user.is_staff:
            queryset = queryset.filter(user=user)

        status = (self.request.query_params.get("status") or "").strip().lower()
        if status:
            queryset = queryset.filter(status=status)

        if user.is_staff:
            user_id = (self.request.query_params.get("user_id") or "").strip()
            if user_id and user_id.isdigit():
                queryset = queryset.filter(user_id=int(user_id))

        return queryset
