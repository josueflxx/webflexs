"""URL routes for API v1."""

from django.urls import path

from core.api_v1 import views

app_name = "api_v1"

urlpatterns = [
    path("health/", views.ApiHealthView.as_view(), name="health"),
    path("catalog/categories/", views.ApiCategoryListView.as_view(), name="catalog_categories"),
    path("catalog/products/", views.ApiProductListView.as_view(), name="catalog_products"),
    path("clients/", views.ApiClientListView.as_view(), name="clients"),
    path("clients/me/", views.ApiMyClientProfileView.as_view(), name="clients_me"),
    path("orders/", views.ApiOrderListView.as_view(), name="orders"),
]

