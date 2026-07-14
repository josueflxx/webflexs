"""Company-scoped API v1 endpoints."""

from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.models import Q
from django.shortcuts import render
from rest_framework import generics, permissions
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from accounts.models import ClientProfile
from catalog.models import Category, Product
from core.api_v1.permissions import (
    HasAdminCapability,
    HasRequiredCapabilityWhenStaff,
    IsStaffUser,
)
from core.api_v1.serializers import (
    CategorySerializer,
    ClientProfileSerializer,
    OrderListSerializer,
    ProductClientSerializer,
    ProductStaffSerializer,
)
from orders.models import Order
from core.models import WebhookDelivery, WebhookEndpoint
from core.services.authorization import (
    CAP_CHANGE_PRICES,
    CAP_GLOBAL_SEARCH,
    CAP_MANAGE_INTEGRATIONS,
    CAP_MANAGE_ORDERS,
    capability_required,
    has_capability,
)
from orders.services.workflow import (
    get_allowed_next_statuses_for_user,
    get_order_queue_queryset_for_user,
    get_role_queue_statuses,
    get_user_order_roles,
    resolve_user_order_role,
)
from core.services.company_context import get_active_company, user_has_company_access


def _parse_bool_param(raw_value):
    if raw_value is None:
        return None
    value = str(raw_value).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return None


def _resolve_request_company(request):
    user = getattr(request, "user", None)
    requested_company_id = str(
        request.query_params.get("company_id")
        or request.query_params.get("company")
        or ""
    ).strip()
    if requested_company_id:
        if not requested_company_id.isdigit():
            return None
        from core.models import Company

        requested_company = Company.objects.filter(pk=int(requested_company_id), is_active=True).first()
        if (
            user
            and getattr(user, "is_authenticated", False)
            and requested_company
            and user_has_company_access(user, requested_company)
        ):
            return requested_company
        return None

    return get_active_company(request)


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
        db_ok = True
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
        except Exception:
            db_ok = False

        return Response(
            {
                "ok": db_ok,
                "api_version": "v1",
                "feature_api_v1_enabled": bool(getattr(settings, "FEATURE_API_V1_ENABLED", False)),
                "db_ok": db_ok,
            },
            status=200 if db_ok else 503,
        )


class ApiCategoryListView(ApiV1BaseListView):
    serializer_class = CategorySerializer
    throttle_scope = "api_v1_catalog"

    def get_queryset(self):
        queryset = Category.objects.select_related("parent").order_by("order", "name")

        user = self.request.user
        if not user.is_staff or not has_capability(user, CAP_GLOBAL_SEARCH):
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
        if user.is_staff and has_capability(user, CAP_GLOBAL_SEARCH):
            active_param = _parse_bool_param(self.request.query_params.get("active"))
            if active_param is not None:
                queryset = queryset.filter(is_active=active_param)
        else:
            queryset = Product.catalog_visible(queryset=queryset, include_uncategorized=False)

        q = (self.request.query_params.get("q") or "").strip()
        if q:
            queryset = queryset.filter(
                Q(sku__icontains=q)
                | Q(name__icontains=q)
                | Q(supplier__icontains=q)
                | Q(supplier_ref__name__icontains=q)
                | Q(supplier_offers__supplier__name__icontains=q)
                | Q(supplier_offers__supplier_code__icontains=q)
                | Q(supplier_offers__supplier_description__icontains=q)
                | Q(filter_1__icontains=q)
                | Q(filter_2__icontains=q)
                | Q(filter_3__icontains=q)
                | Q(filter_4__icontains=q)
                | Q(filter_5__icontains=q)
            ).distinct()

        supplier = (self.request.query_params.get("supplier") or "").strip()
        if supplier:
            queryset = queryset.filter(
                Q(supplier__icontains=supplier)
                | Q(supplier_ref__name__icontains=supplier)
                | Q(supplier_offers__supplier__name__icontains=supplier)
            ).distinct()

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
        if self.request.user.is_staff and has_capability(
            self.request.user,
            CAP_CHANGE_PRICES,
        ):
            return ProductStaffSerializer
        return ProductClientSerializer


class ApiClientListView(ApiV1BaseListView):
    permission_classes = [IsStaffUser, HasAdminCapability]
    required_capability = CAP_GLOBAL_SEARCH
    serializer_class = ClientProfileSerializer
    throttle_scope = "api_v1_admin"

    def get_queryset(self):
        queryset = ClientProfile.objects.select_related("user").order_by("company_name")
        company = _resolve_request_company(self.request)
        if company is None:
            return queryset.none()
        queryset = queryset.filter(
            company_links__company=company,
            company_links__is_active=True,
        ).distinct()

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
    permission_classes = [permissions.IsAuthenticated, HasRequiredCapabilityWhenStaff]
    required_staff_capability = CAP_MANAGE_ORDERS
    serializer_class = OrderListSerializer
    throttle_scope = "api_v1_default"

    def get_queryset(self):
        user = self.request.user
        queryset = Order.objects.select_related("user", "company").prefetch_related("items").order_by("-created_at")
        company = _resolve_request_company(self.request)
        if company is None:
            return queryset.none()
        queryset = queryset.filter(company=company)

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


class ApiOrderQueueView(ApiV1BaseListView):
    permission_classes = [IsStaffUser, HasAdminCapability]
    required_capability = CAP_MANAGE_ORDERS
    serializer_class = OrderListSerializer
    throttle_scope = "api_v1_admin"

    def get_queryset(self):
        queryset = Order.objects.select_related("user", "company").prefetch_related("items").order_by("-updated_at")
        company = _resolve_request_company(self.request)
        if company is None:
            return queryset.none()
        queryset = queryset.filter(company=company)
        filtered_qs, role = get_order_queue_queryset_for_user(queryset, self.request.user)
        self._resolved_role = role

        status = (self.request.query_params.get("status") or "").strip().lower()
        if status:
            filtered_qs = filtered_qs.filter(status=status)
        return filtered_qs

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        serializer = self.get_serializer(page if page is not None else queryset, many=True)

        role = getattr(self, "_resolved_role", None) or resolve_user_order_role(request.user)
        roles = get_user_order_roles(request.user)
        queue_statuses = get_role_queue_statuses(roles or role)
        counts = {
            status: queryset.filter(status=status).count()
            for status in queue_statuses
        }

        payload = {
            "role": role,
            "roles": roles,
            "queue_statuses": queue_statuses,
            "counts": counts,
            "results": serializer.data,
        }

        if page is not None:
            paginated = self.get_paginated_response(serializer.data)
            paginated.data["role"] = role
            paginated.data["roles"] = roles
            paginated.data["queue_statuses"] = queue_statuses
            paginated.data["counts"] = counts
            return paginated
        return Response(payload)


class ApiOrderWorkflowView(APIView):
    permission_classes = [permissions.IsAuthenticated, HasRequiredCapabilityWhenStaff]
    required_staff_capability = CAP_MANAGE_ORDERS
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "api_v1_default"

    def get(self, request, order_id):
        company = _resolve_request_company(request)
        order_qs = Order.objects.filter(pk=order_id)
        if company is None:
            order_qs = order_qs.none()
        else:
            order_qs = order_qs.filter(company=company)
        if not request.user.is_staff:
            order_qs = order_qs.filter(user=request.user)
        order = order_qs.first()
        if not order:
            return Response({"detail": "Pedido no encontrado."}, status=404)

        if not request.user.is_staff and order.user_id != request.user.id:
            return Response({"detail": "No autorizado."}, status=403)

        allowed_statuses = get_allowed_next_statuses_for_user(request.user, order)
        return Response(
            {
                "order_id": order.id,
                "current_status": order.status,
                "allowed_next_statuses": allowed_statuses,
                "resolved_role": resolve_user_order_role(request.user),
                "resolved_roles": get_user_order_roles(request.user),
            },
        )


class RateLimitedObtainAuthToken(ObtainAuthToken):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "api_v1_admin"


class ApiWebhookEndpointListCreateView(APIView):
    permission_classes = [IsStaffUser, HasAdminCapability]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "api_v1_admin"
    required_capability = CAP_MANAGE_INTEGRATIONS

    def get(self, request):
        company = _resolve_request_company(request)
        if not company:
            return Response({"detail": "Empresa activa o company_id requerido."}, status=400)
        rows = WebhookEndpoint.objects.filter(company=company).order_by("name", "id")
        return Response({
            "results": [
                {
                    "id": row.pk,
                    "name": row.name,
                    "target_url": row.target_url,
                    "events": row.events,
                    "is_active": row.is_active,
                    "created_at": row.created_at,
                }
                for row in rows
            ]
        })

    def post(self, request):
        company = _resolve_request_company(request)
        if not company:
            return Response({"detail": "Empresa activa o company_id requerido."}, status=400)
        endpoint = WebhookEndpoint(
            company=company,
            name=str(request.data.get("name", "")).strip(),
            target_url=str(request.data.get("target_url", "")).strip(),
            events=request.data.get("events") or [],
            is_active=(
                _parse_bool_param(request.data.get("is_active"))
                if _parse_bool_param(request.data.get("is_active")) is not None
                else True
            ),
            created_by=request.user,
        )
        supplied_secret = str(request.data.get("secret", "") or "").strip()
        if supplied_secret:
            endpoint.secret = supplied_secret
        try:
            endpoint.save()
        except ValidationError as exc:
            details = exc.message_dict if hasattr(exc, "message_dict") else exc.messages
            return Response({"detail": details}, status=400)
        return Response(
            {
                "id": endpoint.pk,
                "name": endpoint.name,
                "target_url": endpoint.target_url,
                "events": endpoint.events,
                "is_active": endpoint.is_active,
                "secret": endpoint.secret,
                "secret_notice": "Guarda este secreto ahora; no vuelve a mostrarse en listados.",
            },
            status=201,
        )


class ApiWebhookEndpointDetailView(APIView):
    permission_classes = [IsStaffUser, HasAdminCapability]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "api_v1_admin"
    required_capability = CAP_MANAGE_INTEGRATIONS

    def _get_endpoint(self, request, endpoint_id):
        company = _resolve_request_company(request)
        if not company:
            return None
        return WebhookEndpoint.objects.filter(company=company, pk=endpoint_id).first()

    def patch(self, request, endpoint_id):
        endpoint = self._get_endpoint(request, endpoint_id)
        if not endpoint:
            return Response({"detail": "Webhook no encontrado."}, status=404)
        for field in ("name", "target_url", "events", "is_active"):
            if field in request.data:
                value = request.data[field]
                if field == "is_active":
                    parsed_value = _parse_bool_param(value)
                    if parsed_value is None:
                        return Response({"detail": {"is_active": "Valor booleano invalido."}}, status=400)
                    value = parsed_value
                setattr(endpoint, field, value)
        if request.data.get("rotate_secret"):
            from core.models import generate_webhook_secret

            endpoint.secret = generate_webhook_secret()
        try:
            endpoint.save()
        except ValidationError as exc:
            details = exc.message_dict if hasattr(exc, "message_dict") else exc.messages
            return Response({"detail": details}, status=400)
        payload = {
            "id": endpoint.pk,
            "name": endpoint.name,
            "target_url": endpoint.target_url,
            "events": endpoint.events,
            "is_active": endpoint.is_active,
        }
        if request.data.get("rotate_secret"):
            payload["secret"] = endpoint.secret
        return Response(payload)

    def delete(self, request, endpoint_id):
        endpoint = self._get_endpoint(request, endpoint_id)
        if not endpoint:
            return Response({"detail": "Webhook no encontrado."}, status=404)
        endpoint.delete()
        return Response(status=204)


class ApiWebhookDeliveryListView(APIView):
    permission_classes = [IsStaffUser, HasAdminCapability]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "api_v1_admin"
    required_capability = CAP_MANAGE_INTEGRATIONS

    def get(self, request, endpoint_id):
        company = _resolve_request_company(request)
        endpoint = WebhookEndpoint.objects.filter(company=company, pk=endpoint_id).first() if company else None
        if not endpoint:
            return Response({"detail": "Webhook no encontrado."}, status=404)
        rows = WebhookDelivery.objects.filter(endpoint=endpoint).order_by("-created_at")[:100]
        return Response({
            "results": [
                {
                    "id": row.pk,
                    "event_id": row.event_id,
                    "event_type": row.event_type,
                    "status": row.status,
                    "attempts_count": row.attempts_count,
                    "response_status": row.response_status,
                    "last_error": row.last_error,
                    "next_retry_at": row.next_retry_at,
                    "created_at": row.created_at,
                }
                for row in rows
            ]
        })


class ApiSchemaView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        base = request.build_absolute_uri("/api/v1/")
        return Response({
            "openapi": "3.0.3",
            "info": {"title": "FLEXS API", "version": "1.0.0"},
            "servers": [{"url": base}],
            "authentication": {"header": "Authorization: Token <token>"},
            "tenant_scope": (
                "Enviar company_id (o company) cuando el usuario tenga varias empresas. "
                "Sin una empresa autorizada, los recursos empresariales no devuelven datos."
            ),
            "staff_capabilities": {
                "clients": CAP_GLOBAL_SEARCH,
                "orders": CAP_MANAGE_ORDERS,
                "orders_queue": CAP_MANAGE_ORDERS,
                "order_workflow": CAP_MANAGE_ORDERS,
                "webhooks": CAP_MANAGE_INTEGRATIONS,
                "product_cost": CAP_CHANGE_PRICES,
            },
            "paths": {
                "/health/": {"get": {"summary": "Estado de API y base"}},
                "/catalog/products/": {"get": {"summary": "Listado y busqueda de productos"}},
                "/catalog/categories/": {"get": {"summary": "Categorias"}},
                "/clients/": {"get": {"summary": "Clientes de la empresa"}},
                "/orders/": {"get": {"summary": "Pedidos de la empresa"}},
                "/orders/queue/": {"get": {"summary": "Cola por rol"}},
                "/webhooks/": {"get": {"summary": "Listar webhooks"}, "post": {"summary": "Crear webhook"}},
                "/webhooks/{id}/": {"patch": {"summary": "Editar o rotar secreto"}, "delete": {"summary": "Eliminar webhook"}},
                "/webhooks/{id}/deliveries/": {"get": {"summary": "Ultimas entregas"}},
            },
            "webhook_signature": "HMAC-SHA256 de '<timestamp>.<raw_body>' con el secreto; header X-FLEXS-Signature.",
            "webhook_events": [value for value, _label in WebhookEndpoint.EVENT_CHOICES],
        })


@login_required
@capability_required(CAP_MANAGE_INTEGRATIONS)
def api_docs(request):
    return render(request, "core/api_docs.html")
