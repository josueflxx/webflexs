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
    path('carrito/count/', views.cart_count, name='cart_count'),
    path('checkout/', views.checkout, name='checkout'),
    path('portal/', views.client_portal, name='client_portal'),
    path('favoritos/toggle/', views.toggle_favorite, name='toggle_favorite'),
    path('solicitudes/', views.order_request_list, name='order_request_list'),
    path('solicitudes/<int:request_id>/', views.order_request_detail, name='order_request_detail'),
    path(
        'solicitudes/<int:request_id>/propuestas/<int:proposal_id>/aceptar/',
        views.order_request_accept_proposal,
        name='order_request_accept_proposal',
    ),
    path(
        'solicitudes/<int:request_id>/propuestas/<int:proposal_id>/rechazar/',
        views.order_request_reject_proposal,
        name='order_request_reject_proposal',
    ),
    path('documentos/internos/<int:doc_id>/', views.order_internal_document_print, name='order_internal_document_print'),
    path('documentos/fiscales/<int:doc_id>/', views.order_fiscal_document_print, name='order_fiscal_document_print'),
    path('pedidos/', views.order_list, name='order_list'),
    path('pedidos/<int:order_id>/', views.order_detail, name='order_detail'),
    path('pedidos/<int:order_id>/reordenar/', views.reorder_to_cart, name='reorder_to_cart'),
]
