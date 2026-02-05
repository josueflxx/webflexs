"""
Orders app URL configuration.
"""
from django.urls import path
from . import views

urlpatterns = [
    path('carrito/', views.cart_view, name='cart'),
    path('carrito/agregar/', views.add_to_cart, name='add_to_cart'),
    path('carrito/actualizar/', views.update_cart_item, name='update_cart_item'),
    path('carrito/eliminar/', views.remove_from_cart, name='remove_from_cart'),
    path('checkout/', views.checkout, name='checkout'),
    path('pedidos/', views.order_list, name='order_list'),
    path('pedidos/<int:order_id>/', views.order_detail, name='order_detail'),
]
