"""Resolve client addresses without trusting spoofable proxy headers."""

from ipaddress import ip_address, ip_network

from django.conf import settings


def _normalized_ip(raw_value):
    value = str(raw_value or "").strip()
    if not value:
        return ""
    try:
        return str(ip_address(value))
    except ValueError:
        return ""


def _trusted_proxy_networks():
    networks = []
    for raw_value in getattr(settings, "TRUSTED_PROXY_IPS", ()) or ():
        value = str(raw_value or "").strip()
        if not value:
            continue
        try:
            networks.append(ip_network(value, strict=False))
        except ValueError:
            continue
    return networks


def is_trusted_proxy(raw_ip):
    normalized = _normalized_ip(raw_ip)
    if not normalized:
        return False
    parsed = ip_address(normalized)
    return any(parsed in network for network in _trusted_proxy_networks())


def get_client_ip(request):
    """
    Return a canonical client IP.

    ``X-Forwarded-For`` is accepted only when the direct peer is explicitly
    listed in ``TRUSTED_PROXY_IPS``. The chain is walked right-to-left so a
    client cannot prepend a fake address ahead of trusted reverse proxies.
    """
    if request is None:
        return "unknown"

    remote_addr = _normalized_ip(request.META.get("REMOTE_ADDR"))
    if not remote_addr:
        return "unknown"

    forwarded = str(request.META.get("HTTP_X_FORWARDED_FOR", "") or "")
    if not forwarded or not is_trusted_proxy(remote_addr):
        return remote_addr

    chain = [_normalized_ip(value) for value in forwarded.split(",")]
    chain = [value for value in chain if value]
    chain.append(remote_addr)

    for candidate in reversed(chain):
        if not is_trusted_proxy(candidate):
            return candidate
    return chain[0] if chain else remote_addr
