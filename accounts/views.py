"""
Accounts app views - Login, logout, and account requests.
"""
import math

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from .models import AccountRequest


def _get_client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "") or "unknown"


def _build_login_keys(request, username):
    normalized_username = (username or "").strip().lower() or "_"
    ip = _get_client_ip(request)
    key_base = f"auth-login:{normalized_username}:{ip}"
    return f"{key_base}:attempts", f"{key_base}:lock"


def _get_lock_remaining_seconds(lock_payload):
    try:
        return max(int(lock_payload.get("until_ts", 0) - timezone.now().timestamp()), 0)
    except Exception:
        return 0


def login_view(request):
    """User login view."""
    if request.user.is_authenticated:
        return redirect("login_redirect")

    username_prefill = ""

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        username_prefill = username
        attempts_key, lock_key = _build_login_keys(request, username)

        lock_payload = cache.get(lock_key)
        if lock_payload:
            remaining_seconds = _get_lock_remaining_seconds(lock_payload)
            if remaining_seconds > 0:
                remaining_minutes = max(math.ceil(remaining_seconds / 60), 1)
                messages.error(
                    request,
                    f"Demasiados intentos fallidos. Espera {remaining_minutes} minuto(s) antes de reintentar.",
                )
                return render(request, "accounts/login.html", {"username_prefill": username_prefill})
            cache.delete(lock_key)

        user = authenticate(request, username=username, password=password)

        if user is not None:
            cache.delete(attempts_key)
            cache.delete(lock_key)
            login(request, user)
            messages.success(request, f"Bienvenido, {user.first_name or user.username}!")

            next_url = request.GET.get("next", "")
            if next_url and url_has_allowed_host_and_scheme(
                next_url,
                allowed_hosts={request.get_host()},
                require_https=request.is_secure(),
            ):
                return redirect(next_url)

            return redirect("login_redirect")

        max_attempts = int(getattr(settings, "LOGIN_MAX_FAILED_ATTEMPTS", 5))
        lock_seconds = int(getattr(settings, "LOGIN_LOCKOUT_SECONDS", 900))
        attempt_window_seconds = int(getattr(settings, "LOGIN_ATTEMPT_WINDOW_SECONDS", 900))
        attempts = int(cache.get(attempts_key, 0)) + 1
        cache.set(attempts_key, attempts, timeout=attempt_window_seconds)

        if attempts >= max_attempts:
            cache.set(
                lock_key,
                {"until_ts": int(timezone.now().timestamp()) + lock_seconds},
                timeout=lock_seconds,
            )
            messages.error(
                request,
                f"Demasiados intentos fallidos. Intenta nuevamente en {max(math.ceil(lock_seconds / 60), 1)} minuto(s).",
            )
        else:
            remaining_attempts = max_attempts - attempts
            messages.error(
                request,
                f"Usuario o contrasena incorrectos. Intentos restantes antes de bloqueo: {remaining_attempts}.",
            )

    return render(request, "accounts/login.html", {"username_prefill": username_prefill})


@login_required
def login_redirect(request):
    """
    Redirect after login based on user role.
    Admin sees choice screen, client goes to catalog.
    """
    if request.user.is_staff:
        return render(request, "accounts/admin_redirect.html")
    return redirect("catalog")


def logout_view(request):
    """User logout view."""
    if request.user.is_authenticated and request.user.is_staff:
        from core.models import UserActivity

        UserActivity.objects.filter(user=request.user).update(is_online=False)

    logout(request)
    messages.info(request, "Has cerrado sesion.")
    return redirect("home")


def account_request(request):
    """Account request form for new B2B clients."""
    if request.user.is_authenticated:
        return redirect("catalog")

    if request.method == "POST":
        company_name = request.POST.get("company_name", "").strip()
        contact_name = request.POST.get("contact_name", "").strip()
        cuit_dni = request.POST.get("cuit_dni", "").strip()
        email = request.POST.get("email", "").strip()
        phone = request.POST.get("phone", "").strip()
        province = request.POST.get("province", "").strip()
        address = request.POST.get("address", "").strip()
        message = request.POST.get("message", "").strip()

        errors = []
        if not company_name:
            errors.append("El nombre de la empresa es requerido.")
        if not contact_name:
            errors.append("El nombre de contacto es requerido.")
        if not email:
            errors.append("El email es requerido.")
        if not phone:
            errors.append("El telefono es requerido.")

        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            AccountRequest.objects.create(
                company_name=company_name,
                contact_name=contact_name,
                cuit_dni=cuit_dni,
                email=email,
                phone=phone,
                province=province,
                address=address,
                message=message,
            )
            messages.success(
                request,
                "Solicitud enviada. Nos pondremos en contacto pronto para activar tu cuenta.",
            )
            return redirect("home")

    return render(request, "accounts/account_request.html")
