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
    path('productos/imagen-masiva/', views.product_bulk_image_update, name='admin_product_bulk_image'),
    path('proveedores/', views.supplier_list, name='admin_supplier_list'),
    path('proveedores/sin-proveedor/', views.supplier_unassigned, name='admin_supplier_unassigned'),
    path('proveedores/<int:supplier_id>/', views.supplier_detail, name='admin_supplier_detail'),
    path('proveedores/<int:supplier_id>/estado/', views.supplier_toggle_active, name='admin_supplier_toggle_active'),
    path('proveedores/<int:supplier_id>/acciones/', views.supplier_bulk_action, name='admin_supplier_bulk_action'),
    path('proveedores/<int:supplier_id>/exportar/', views.supplier_export, name='admin_supplier_export'),
    path('proveedores/<int:supplier_id>/imprimir/', views.supplier_print, name='admin_supplier_print'),
    path('abrazaderas-a-medida/', views.clamp_request_list, name='admin_clamp_request_list'),
    path('abrazaderas-a-medida/<int:pk>/', views.clamp_request_detail, name='admin_clamp_request_detail'),
    
    # Clients
    path('clientes/', views.client_dashboard, name='admin_client_dashboard'),
    path('clientes/buscar/', views.client_list, name='admin_client_list'),
    path('clientes/herramientas/', views.client_tools_hub, name='admin_client_tools_hub'),
    path('clientes/exportar/', views.client_export, name='admin_client_export'),
    path('clientes/informes/', views.client_reports_hub, name='admin_client_reports_hub'),
    path('clientes/informes/lista/', views.client_report_list, name='admin_client_report_list'),
    path('clientes/informes/ranking/', views.client_report_ranking, name='admin_client_report_ranking'),
    path('clientes/informes/deudores/', views.client_report_debtors, name='admin_client_report_debtors'),
    path('clientes/nuevo/', views.client_create, name='admin_client_create'),
    path('clientes/categorias/', views.client_category_list, name='admin_client_category_list'),
    path('clientes/categorias/nueva/', views.client_category_create, name='admin_client_category_create'),
    path('clientes/categorias/<int:pk>/editar/', views.client_category_edit, name='admin_client_category_edit'),
    path('clientes/categorias/<int:pk>/eliminar/', views.client_category_delete, name='admin_client_category_delete'),
    path('clientes/<int:pk>/pedidos/', views.client_order_history, name='admin_client_order_history'),
    path('clientes/<int:pk>/accion-rapida/', views.client_quick_order, name='admin_client_quick_order'),
    path('clientes/<int:pk>/editar/', views.client_edit, name='admin_client_edit'),
    path('clientes/<int:pk>/password/', views.client_password_change, name='admin_client_password'),
    path('clientes/<int:pk>/eliminar/', views.client_delete, name='admin_client_delete'),
    path('clientes/eliminar-todos/', views.client_delete_all, name='admin_client_delete_all'),
    path('clientes/cuit-lookup/', views.client_cuit_lookup, name='admin_client_cuit_lookup'),
    
    # Account Requests
    path('solicitudes/', views.request_list, name='admin_request_list'),
    path('solicitudes/<int:pk>/aprobar/', views.request_approve, name='admin_request_approve'),
    path('solicitudes/<int:pk>/rechazar/', views.request_reject, name='admin_request_reject'),
    
    # Orders
    path('pedidos/', views.order_list, name='admin_order_list'),
    path('pedidos/exportar-saas/', views.order_export_saas, name='admin_order_export_saas'),
    path('pedidos/<int:pk>/', views.order_detail, name='admin_order_detail'),
    path('pedidos/<int:pk>/facturar/', views.order_invoice_open, name='admin_order_invoice_open'),
    path('pedidos/<int:pk>/interno/crear/', views.order_internal_document_create, name='admin_order_internal_document_create'),
    path('pedidos/<int:pk>/fiscal/crear-local/', views.order_fiscal_create_local, name='admin_order_fiscal_create_local'),
    path('pedidos/<int:pk>/fiscal/registrar-externo/', views.order_fiscal_register_external, name='admin_order_fiscal_register_external'),
    path('pedidos/<int:pk>/items/agregar/', views.order_item_add, name='admin_order_item_add'),
    path('pedidos/<int:pk>/items/<int:item_id>/eliminar/', views.order_item_delete, name='admin_order_item_delete'),
    path(
        'pedidos/<int:pk>/items/<int:item_id>/publicar-abrazadera/',
        views.order_item_publish_clamp,
        name='admin_order_item_publish_clamp',
    ),
    path('pedidos/<int:pk>/eliminar/', views.order_delete, name='admin_order_delete'),
    path('pagos/', views.payment_list, name='admin_payment_list'),
    path('pagos/exportar-saas/', views.payment_export_saas, name='admin_payment_export_saas'),
    path('cotizador/', views.clamp_quoter, name='admin_clamp_quoter'),
    path('documentos/<int:doc_id>/imprimir/', views.internal_document_print, name='admin_internal_document_print'),
    path('fiscal/documentos/', views.fiscal_document_list, name='admin_fiscal_document_list'),
    path('fiscal/documentos/<int:pk>/', views.fiscal_document_detail, name='admin_fiscal_document_detail'),
    path('fiscal/documentos/<int:pk>/emitir/', views.fiscal_document_emit, name='admin_fiscal_document_emit'),
    path('fiscal/documentos/<int:pk>/cerrar/', views.fiscal_document_close, name='admin_fiscal_document_close'),
    path('fiscal/documentos/<int:pk>/reabrir/', views.fiscal_document_reopen, name='admin_fiscal_document_reopen'),
    path('fiscal/documentos/<int:pk>/anular/', views.fiscal_document_void, name='admin_fiscal_document_void'),
    path('fiscal/documentos/<int:pk>/imprimir/', views.fiscal_document_print, name='admin_fiscal_document_print'),

    # Catalog Excel templates
    path('exportar-catalogo/', views.catalog_excel_template_list, name='admin_catalog_excel_template_list'),
    path('exportar-catalogo/nueva/', views.catalog_excel_template_create, name='admin_catalog_excel_template_create'),
    path('exportar-catalogo/<int:template_id>/', views.catalog_excel_template_detail, name='admin_catalog_excel_template_detail'),
    path('exportar-catalogo/<int:template_id>/editar/', views.catalog_excel_template_edit, name='admin_catalog_excel_template_edit'),
    path('exportar-catalogo/<int:template_id>/eliminar/', views.catalog_excel_template_delete, name='admin_catalog_excel_template_delete'),
    path('exportar-catalogo/<int:template_id>/descargar/', views.catalog_excel_template_download, name='admin_catalog_excel_template_download'),
    path('exportar-catalogo/<int:template_id>/auto-hojas-principales/', views.catalog_excel_template_autogenerate_main_category_sheets, name='admin_catalog_excel_template_autogenerate_main_category_sheets'),
    path('exportar-catalogo/<int:template_id>/hojas/nueva/', views.catalog_excel_sheet_create, name='admin_catalog_excel_sheet_create'),
    path('exportar-catalogo/hojas/<int:sheet_id>/editar/', views.catalog_excel_sheet_edit, name='admin_catalog_excel_sheet_edit'),
    path('exportar-catalogo/hojas/<int:sheet_id>/eliminar/', views.catalog_excel_sheet_delete, name='admin_catalog_excel_sheet_delete'),
    path('exportar-catalogo/hojas/<int:sheet_id>/columnas/nueva/', views.catalog_excel_column_create, name='admin_catalog_excel_column_create'),
    path('exportar-catalogo/columnas/<int:column_id>/editar/', views.catalog_excel_column_edit, name='admin_catalog_excel_column_edit'),
    path('exportar-catalogo/columnas/<int:column_id>/eliminar/', views.catalog_excel_column_delete, name='admin_catalog_excel_column_delete'),
    
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
    path('configuracion/tipos-documento/', views.sales_document_type_list, name='admin_sales_document_type_list'),
    path('configuracion/tipos-documento/nuevo/', views.sales_document_type_create, name='admin_sales_document_type_create'),
    path('configuracion/tipos-documento/<int:pk>/editar/', views.sales_document_type_edit, name='admin_sales_document_type_edit'),
    path('configuracion/tipos-documento/<int:pk>/toggle/', views.sales_document_type_toggle_enabled, name='admin_sales_document_type_toggle_enabled'),
    path('configuracion/depositos/', views.warehouse_list, name='admin_warehouse_list'),
    path('configuracion/depositos/nuevo/', views.warehouse_create, name='admin_warehouse_create'),
    path('configuracion/depositos/<int:pk>/editar/', views.warehouse_edit, name='admin_warehouse_edit'),
    path('fiscal/configuracion/', views.fiscal_config, name='admin_fiscal_config'),
    path('fiscal/puntos-venta/nuevo/', views.fiscal_point_create, name='admin_fiscal_point_create'),
    path('fiscal/puntos-venta/<int:pk>/editar/', views.fiscal_point_edit, name='admin_fiscal_point_edit'),
    path('fiscal/puntos-venta/<int:pk>/toggle-activo/', views.fiscal_point_toggle_active, name='admin_fiscal_point_toggle_active'),
    path('fiscal/puntos-venta/<int:pk>/default/', views.fiscal_point_set_default, name='admin_fiscal_point_set_default'),
    path('empresas/', views.company_list, name='admin_company_list'),
    path('empresas/<int:pk>/', views.company_edit, name='admin_company_edit'),
    path('admins/', views.admin_user_list, name='admin_user_list'),
    path('admins/<int:user_id>/editar/', views.admin_user_edit, name='admin_user_edit'),
    path('admins/<int:user_id>/password/', views.admin_user_password_change, name='admin_user_password_change'),
    path('admins/<int:user_id>/permisos/', views.admin_user_permissions, name='admin_user_permissions'),
    path('admins/<int:user_id>/eliminar/', views.admin_user_delete, name='admin_user_delete'),
    
    # API
    path('api/category-attributes/<int:category_id>/', views.get_category_attributes, name='api_category_attributes'),
    path('api/parse-description/', views.parse_product_description, name='api_parse_description'),
    path('api/clamp-code/parse/', views.parse_clamp_code_api, name='api_clamp_code_parse'),
    path('api/clamp-code/generate/', views.generate_clamp_code_api, name='api_clamp_code_generate'),
    
    # Importers
    path('importar/', views.import_dashboard, name='admin_import_dashboard'),
    path('importar/status/<str:task_id>/', views.import_status, name='admin_import_status'),
    path('importar/<str:import_type>/', views.import_process, name='admin_import_process'),
    path('importar/rollback/<int:execution_id>/', views.import_rollback, name='admin_import_rollback'),
    # Commit view removed as we handle it in process view for MVP simplicity
    # path('importar/<str:import_type>/confirmar/', views.import_commit, name='admin_import_commit'),
]
