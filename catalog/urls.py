"""
Catalog app URL configuration.
"""
from django.urls import path
from . import views

urlpatterns = [
    path('', views.catalog, name='catalog'),
    path('abrazaderas-a-medida/', views.clamp_measure_request, name='catalog_clamp_request'),
    path(
        'abrazaderas-a-medida/<int:pk>/agregar-carrito/',
        views.clamp_request_add_to_cart,
        name='catalog_clamp_request_add_to_cart',
    ),
    path('producto/<path:sku>/', views.product_detail, name='product_detail'),
]
