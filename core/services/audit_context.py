"""
Thread-local request context for audit logging.
"""
from threading import local


_state = local()


def set_request_context(request):
    from core.services.client_ip import get_client_ip

    user = getattr(request, "user", None)
    _state.user = user if getattr(user, "is_authenticated", False) else None
    _state.ip_address = get_client_ip(request)
    _state.user_agent = request.META.get("HTTP_USER_AGENT", "")[:255]


def clear_request_context():
    _state.user = None
    _state.ip_address = ""
    _state.user_agent = ""


def get_audit_actor():
    return getattr(_state, "user", None)


def get_audit_meta():
    return {
        "ip_address": getattr(_state, "ip_address", ""),
        "user_agent": getattr(_state, "user_agent", ""),
    }
