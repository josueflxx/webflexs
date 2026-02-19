"""
Admin Panel URL configuration.
"""
from django.urls import path
from . import views

urlpatterns = [
    # Dashboard
    path('', views.dashboard, name='admin_dashboard'),
    
    # Products
    path('productos/', views.product_list, name='admin_product_list'),
    path('productos/nuevo/', views.product_create, name='admin_product_create'),
    path('productos/<int:pk>/editar/', views.product_edit, name='admin_product_edit'),
    path('productos/toggle-active/', views.product_toggle_active, name='admin_product_toggle'),
    path('productos/<int:pk>/eliminar/', views.product_delete, name='admin_product_delete'),
    path('productos/eliminar-todos/', views.product_delete_all, name='admin_product_delete_all'),
    path('productos/asignar-categoria/', views.product_bulk_category_update, name='admin_product_bulk_category'),
    path('productos/estado-masivo/', views.product_bulk_status_update, name='admin_product_bulk_status'),
    path('proveedores/', views.supplier_list, name='admin_supplier_list'),
    path('proveedores/sin-proveedor/', views.supplier_unassigned, name='admin_supplier_unassigned'),
    path('proveedores/<int:supplier_id>/', views.supplier_detail, name='admin_supplier_detail'),
    path('proveedores/<int:supplier_id>/estado/', views.supplier_toggle_active, name='admin_supplier_toggle_active'),
    path('proveedores/<int:supplier_id>/acciones/', views.supplier_bulk_action, name='admin_supplier_bulk_action'),
    path('proveedores/<int:supplier_id>/exportar/', views.supplier_export, name='admin_supplier_export'),
    path('proveedores/<int:supplier_id>/imprimir/', views.supplier_print, name='admin_supplier_print'),
    
    # Clients
    path('clientes/', views.client_list, name='admin_client_list'),
    path('clientes/<int:pk>/editar/', views.client_edit, name='admin_client_edit'),
    path('clientes/<int:pk>/password/', views.client_password_change, name='admin_client_password'),
    path('clientes/<int:pk>/eliminar/', views.client_delete, name='admin_client_delete'),
    path('clientes/eliminar-todos/', views.client_delete_all, name='admin_client_delete_all'),
    
    # Account Requests
    path('solicitudes/', views.request_list, name='admin_request_list'),
    path('solicitudes/<int:pk>/aprobar/', views.request_approve, name='admin_request_approve'),
    path('solicitudes/<int:pk>/rechazar/', views.request_reject, name='admin_request_reject'),
    
    # Orders
    path('pedidos/', views.order_list, name='admin_order_list'),
    path('pedidos/<int:pk>/', views.order_detail, name='admin_order_detail'),
    path('pedidos/<int:pk>/eliminar/', views.order_delete, name='admin_order_delete'),
    
    # Categories
    path('categorias/', views.category_list, name='admin_category_list'),
    path('categorias/nueva/', views.category_create, name='admin_category_create'),
    path('categorias/<int:pk>/editar/', views.category_edit, name='admin_category_edit'),
    path('categorias/<int:pk>/mover/', views.category_move, name='admin_category_move'),
    path('categorias/<int:pk>/eliminar/', views.category_delete, name='admin_category_delete'),
    path('categorias/eliminar-todas/', views.category_delete_all, name='admin_category_delete_all'),
    path('categorias/reordenar/', views.category_reorder, name='admin_category_reorder'),
    path('categorias/estado-masivo/', views.category_bulk_status, name='admin_category_bulk_status'),
    path('categorias/<int:pk>/productos/', views.category_manage_products, name='admin_category_products'),
    
    # Category Attributes
    path('categorias/<int:category_id>/atributos/nuevo/', views.category_attribute_create, name='admin_category_attribute_create'),
    path('categorias/<int:category_id>/atributos/<int:attribute_id>/editar/', views.category_attribute_edit, name='admin_category_attribute_edit'),
    path('categorias/<int:category_id>/atributos/<int:attribute_id>/eliminar/', views.category_attribute_delete, name='admin_category_attribute_delete'),
    
    # Settings
    path('configuracion/', views.settings_view, name='admin_settings'),
    
    # API
    path('api/category-attributes/<int:category_id>/', views.get_category_attributes, name='api_category_attributes'),
    path('api/parse-description/', views.parse_product_description, name='api_parse_description'),
    
    # Importers
    path('importar/', views.import_dashboard, name='admin_import_dashboard'),
    path('importar/status/<str:task_id>/', views.import_status, name='admin_import_status'),
    path('importar/<str:import_type>/', views.import_process, name='admin_import_process'),
    path('importar/rollback/<int:execution_id>/', views.import_rollback, name='admin_import_rollback'),
    # Commit view removed as we handle it in process view for MVP simplicity
    # path('importar/<str:import_type>/confirmar/', views.import_commit, name='admin_import_commit'),
]
