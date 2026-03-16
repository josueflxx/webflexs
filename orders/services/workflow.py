"""Role-aware order workflow helpers."""

from django.conf import settings

from orders.models import Order

ROLE_ADMIN = "admin"
ROLE_VENTAS = "ventas"
ROLE_DEPOSITO = "deposito"
ROLE_FACTURACION = "facturacion"

KNOWN_ROLE_NAMES = {
    "admin": ROLE_ADMIN,
    "administracion": ROLE_ADMIN,
    "ventas": ROLE_VENTAS,
    "deposito": ROLE_DEPOSITO,
    "facturacion": ROLE_FACTURACION,
}

ROLE_QUEUE_STATUSES = {
    ROLE_ADMIN: [status for status, _ in Order.STATUS_CHOICES],
    ROLE_VENTAS: [Order.STATUS_DRAFT, Order.STATUS_CONFIRMED],
    ROLE_DEPOSITO: [Order.STATUS_CONFIRMED, Order.STATUS_PREPARING],
    ROLE_FACTURACION: [Order.STATUS_SHIPPED, Order.STATUS_DELIVERED],
}

ROLE_ALLOWED_TRANSITIONS = {
    ROLE_ADMIN: None,  # None = all transitions already validated by model.
    ROLE_VENTAS: {
        (Order.STATUS_DRAFT, Order.STATUS_CONFIRMED),
        (Order.STATUS_DRAFT, Order.STATUS_CANCELLED),
        (Order.STATUS_CONFIRMED, Order.STATUS_CANCELLED),
    },
    ROLE_DEPOSITO: {
        (Order.STATUS_CONFIRMED, Order.STATUS_PREPARING),
        (Order.STATUS_PREPARING, Order.STATUS_SHIPPED),
    },
    ROLE_FACTURACION: {
        (Order.STATUS_SHIPPED, Order.STATUS_DELIVERED),
    },
}

ROLE_DISPLAY_ORDER = [ROLE_ADMIN, ROLE_VENTAS, ROLE_DEPOSITO, ROLE_FACTURACION]
ROLE_PRIMARY_ORDER = [ROLE_ADMIN, ROLE_FACTURACION, ROLE_DEPOSITO, ROLE_VENTAS]


def _user_group_roles(user):
    if not getattr(user, "is_authenticated", False):
        return set()
    names = {
        str(name).strip().lower()
        for name in user.groups.values_list("name", flat=True)
    }
    roles = {KNOWN_ROLE_NAMES[name] for name in names if name in KNOWN_ROLE_NAMES}
    return roles


def get_user_order_roles(user):
    """
    Return all effective workflow roles for the user, ordered for UI display.
    """
    if not getattr(user, "is_authenticated", False):
        return []
    if user.is_superuser:
        return [ROLE_ADMIN]

    roles = _user_group_roles(user)
    if ROLE_ADMIN in roles:
        return [ROLE_ADMIN]

    ordered_roles = [role for role in ROLE_DISPLAY_ORDER if role in roles]
    if ordered_roles:
        return ordered_roles

    # Backward compatibility: existing staff users without groups keep full control.
    if user.is_staff:
        return [ROLE_ADMIN]
    return []


def resolve_user_order_role(user):
    """
    Determine effective workflow role for current user.
    """
    roles = set(get_user_order_roles(user))
    for role in ROLE_PRIMARY_ORDER:
        if role in roles:
            return role
    return None


def get_role_queue_statuses(role):
    if not role:
        return []

    if isinstance(role, (list, tuple, set)):
        roles = list(role)
    else:
        roles = [role]

    normalized_roles = [str(value or "").strip().lower() for value in roles if value]
    if ROLE_ADMIN in normalized_roles:
        return [status for status, _ in Order.STATUS_CHOICES]

    allowed_statuses = set()
    for current_role in normalized_roles:
        allowed_statuses.update(ROLE_QUEUE_STATUSES.get(current_role, []))

    return [
        status
        for status, _ in Order.STATUS_CHOICES
        if status in allowed_statuses
    ]


def get_order_queue_queryset_for_user(queryset, user):
    roles = get_user_order_roles(user)
    primary_role = resolve_user_order_role(user)
    if not roles:
        return queryset.none(), primary_role
    statuses = get_role_queue_statuses(roles)
    if not statuses:
        return queryset.none(), primary_role
    return queryset.filter(status__in=statuses), primary_role


def can_user_transition_order(user, order, new_status):
    """
    Validate whether user role can apply the target transition.
    Returns tuple: (allowed: bool, reason: str).
    """
    roles = get_user_order_roles(user)
    if not roles:
        return False, "No tienes permisos para actualizar pedidos."

    normalized_target = Order.LEGACY_STATUS_MAP.get(new_status, new_status)
    normalized_current = order.normalized_status()

    if not order.can_transition_to(normalized_target):
        return False, "Transicion de estado no permitida por workflow."

    if ROLE_ADMIN not in roles:
        transition_allowed = False
        for current_role in roles:
            allowed_transitions = ROLE_ALLOWED_TRANSITIONS.get(current_role)
            if allowed_transitions is None:
                transition_allowed = True
                break
            if (normalized_current, normalized_target) in allowed_transitions:
                transition_allowed = True
                break
        if not transition_allowed:
            role_list = ", ".join(roles)
            return False, f"Tus roles ({role_list}) no pueden mover este pedido a ese estado."

    require_payment = getattr(settings, "ORDER_REQUIRE_PAYMENT_FOR_CONFIRMATION", False)
    if require_payment and normalized_target == Order.STATUS_CONFIRMED and order.get_pending_amount() > 0:
        return False, "No se puede confirmar: el pedido tiene saldo pendiente."

    return True, ""


def get_allowed_next_statuses_for_user(user, order):
    """
    Return allowed next statuses (including current) for UI/API hints.
    """
    role = resolve_user_order_role(user)
    if not role:
        return []

    current = order.normalized_status()
    candidates = []
    for status, _ in Order.STATUS_CHOICES:
        if status == current:
            candidates.append(status)
            continue
        allowed, _ = can_user_transition_order(user, order, status)
        if allowed:
            candidates.append(status)
    return candidates
