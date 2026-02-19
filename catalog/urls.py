"""
Catalog app URL configuration.
"""
from django.urls import path
from . import views

urlpatterns = [
    path('', views.catalog, name='catalog'),
    path('producto/<path:sku>/', views.product_detail, name='product_detail'),
]
