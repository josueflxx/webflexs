"""Granular capabilities for internal operators."""

from functools import wraps

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse


CAP_VIEW_DASHBOARD = "view_dashboard"
CAP_GLOBAL_SEARCH = "global_search"
CAP_SELL = "sell"
CAP_MANAGE_ORDERS = "manage_orders"
CAP_CANCEL_ORDERS = "cancel_orders"
CAP_CHANGE_PRICES = "change_prices"
CAP_MANAGE_PRODUCTS = "manage_products"
CAP_ISSUE_DOCUMENTS = "issue_documents"
CAP_RUN_IMPORTS = "run_imports"
CAP_MANAGE_USERS = "manage_users"
CAP_EXPORT_DATA = "export_data"
CAP_MANAGE_BACKUPS = "manage_backups"
CAP_MANAGE_INTEGRATIONS = "manage_integrations"

CAPABILITY_CHOICES = [
    (CAP_VIEW_DASHBOARD, "Ver dashboard gerencial", "Accede a indicadores y rankings comerciales."),
    (CAP_GLOBAL_SEARCH, "Usar buscador global", "Busca productos, clientes, pedidos y comprobantes."),
    (CAP_SELL, "Crear ventas", "Crea pedidos y convierte solicitudes comerciales."),
    (CAP_MANAGE_ORDERS, "Gestionar pedidos", "Edita items, notas y estados permitidos por su rol."),
    (CAP_CANCEL_ORDERS, "Anular pedidos", "Cancela pedidos y movimientos comerciales."),
    (CAP_CHANGE_PRICES, "Modificar precios", "Cambia costos, precios y listas comerciales."),
    (
        CAP_MANAGE_PRODUCTS,
        "Revisar productos",
        "Gestiona relaciones con proveedores y clasifica posibles duplicados.",
    ),
    (CAP_ISSUE_DOCUMENTS, "Emitir comprobantes", "Crea, emite, anula y reabre documentos fiscales."),
    (CAP_RUN_IMPORTS, "Ejecutar importaciones", "Importa y revierte productos, clientes y categorias."),
    (CAP_MANAGE_USERS, "Administrar usuarios", "Gestiona operadores, roles y credenciales."),
    (CAP_EXPORT_DATA, "Exportar informacion", "Descarga reportes, catalogos y datos comerciales."),
    (CAP_MANAGE_BACKUPS, "Administrar backups", "Ejecuta y consulta copias de seguridad."),
    (CAP_MANAGE_INTEGRATIONS, "Administrar integraciones", "Configura API, tokens y webhooks."),
]

ALL_CAPABILITIES = {value for value, _label, _description in CAPABILITY_CHOICES}

ROLE_DEFAULT_CAPABILITIES = {
    "admin": set(ALL_CAPABILITIES),
    "administracion": set(ALL_CAPABILITIES),
    "ventas": {
        CAP_VIEW_DASHBOARD,
        CAP_GLOBAL_SEARCH,
        CAP_SELL,
        CAP_MANAGE_ORDERS,
        CAP_CANCEL_ORDERS,
        CAP_EXPORT_DATA,
    },
    "deposito": {
        CAP_VIEW_DASHBOARD,
        CAP_GLOBAL_SEARCH,
        CAP_MANAGE_ORDERS,
        CAP_EXPORT_DATA,
    },
    "facturacion": {
        CAP_VIEW_DASHBOARD,
        CAP_GLOBAL_SEARCH,
        CAP_MANAGE_ORDERS,
        CAP_ISSUE_DOCUMENTS,
        CAP_EXPORT_DATA,
    },
}


def normalize_capabilities(values):
    return sorted(
        {
            str(value or "").strip().lower()
            for value in values or []
            if str(value or "").strip().lower() in ALL_CAPABILITIES
        }
    )


def get_user_capabilities(user):
    if not user or not getattr(user, "is_authenticated", False):
        return set()
    if getattr(user, "is_superuser", False):
        return set(ALL_CAPABILITIES)
    if not getattr(user, "is_staff", False):
        return set()

    profile = getattr(user, "admin_capability_profile", None)
    if profile and profile.is_configured:
        return set(normalize_capabilities(profile.capabilities))

    capabilities = set()
    for name in user.groups.values_list("name", flat=True):
        capabilities.update(ROLE_DEFAULT_CAPABILITIES.get(str(name).strip().lower(), set()))
    return capabilities


def has_capability(user, capability):
    return str(capability or "").strip().lower() in get_user_capabilities(user)


def capability_required(capability):
    """Decorator for internal views that returns a useful denial response."""
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            if has_capability(getattr(request, "user", None), capability):
                return view_func(request, *args, **kwargs)

            wants_json = (
                request.path.startswith("/api/")
                or "application/json" in request.headers.get("Accept", "")
            )
            if wants_json:
                return JsonResponse(
                    {"detail": "No tienes permiso para realizar esta accion.", "capability": capability},
                    status=403,
                )
            messages.error(request, "No tienes permiso para realizar esta accion.")
            return redirect(reverse("admin_dashboard"))

        return wrapped
    return decorator
