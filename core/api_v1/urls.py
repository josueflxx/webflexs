"""URL routes for API v1."""

from django.urls import path

from core.api_v1 import views

app_name = "api_v1"

urlpatterns = [
    path("health/", views.ApiHealthView.as_view(), name="health"),
    path("auth/token/", views.RateLimitedObtainAuthToken.as_view(), name="auth_token"),
    path("schema/", views.ApiSchemaView.as_view(), name="schema"),
    path("docs/", views.api_docs, name="docs"),
    path("catalog/categories/", views.ApiCategoryListView.as_view(), name="catalog_categories"),
    path("catalog/products/", views.ApiProductListView.as_view(), name="catalog_products"),
    path("clients/", views.ApiClientListView.as_view(), name="clients"),
    path("clients/me/", views.ApiMyClientProfileView.as_view(), name="clients_me"),
    path("orders/", views.ApiOrderListView.as_view(), name="orders"),
    path("orders/queue/", views.ApiOrderQueueView.as_view(), name="orders_queue"),
    path("orders/<int:order_id>/workflow/", views.ApiOrderWorkflowView.as_view(), name="orders_workflow"),
    path("webhooks/", views.ApiWebhookEndpointListCreateView.as_view(), name="webhooks"),
    path("webhooks/<int:endpoint_id>/", views.ApiWebhookEndpointDetailView.as_view(), name="webhook_detail"),
    path("webhooks/<int:endpoint_id>/deliveries/", views.ApiWebhookDeliveryListView.as_view(), name="webhook_deliveries"),
]
