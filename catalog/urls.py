"""
Catalog app URL configuration.
"""
from django.urls import path
from . import views

urlpatterns = [
    path('', views.catalog, name='catalog'),
    path('descargar-excel/', views.client_catalog_excel_download, name='catalog_client_excel_download'),
    path('abrazaderas-a-medida/', views.clamp_measure_request, name='catalog_clamp_request'),
    path('como-medir/', views.how_to_measure, name='catalog_how_to_measure'),
    path(
        'abrazaderas-a-medida/<int:pk>/agregar-carrito/',
        views.clamp_request_add_to_cart,
        name='catalog_clamp_request_add_to_cart',
    ),
    path('producto/<path:sku>/', views.product_detail, name='product_detail'),
    path('marcas/', views.brands_list, name='brands_list'),
    path('marcas/<slug:brand_slug>/', views.brand_detail, name='brand_detail'),
]
