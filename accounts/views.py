"""
Accounts app views - Login, logout, and account requests.
"""
import hashlib
import math

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import PasswordResetView
from django.core.cache import cache
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from .models import AccountRequest
from core.models import Company
from core.services.company_context import (
    get_active_company,
    get_default_client_origin_company,
    get_user_companies,
    set_active_company,
    user_has_company_access,
)


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


def _build_account_request_keys(request):
    ip = _get_client_ip(request)
    return (
        f"account-request:{ip}:count",
        "account_request_last_submit_ts",
    )


def _build_password_reset_keys(request, email):
    ip = _get_client_ip(request)
    normalized_email = (email or "").strip().lower()
    email_hash = hashlib.sha256(normalized_email.encode("utf-8")).hexdigest()[:24] or "empty"
    return (
        f"password-reset:ip:{ip}:count",
        f"password-reset:email:{email_hash}:count",
    )


class SafePasswordResetView(PasswordResetView):
    """
    Public password reset with generic UX and light abuse protection.
    Django still owns token generation and email validation.
    """

    def form_valid(self, form):
        email = (form.cleaned_data.get("email") or "").strip().lower()
        ip_key, email_key = _build_password_reset_keys(self.request, email)
        window_seconds = int(getattr(settings, "PASSWORD_RESET_WINDOW_SECONDS", 60 * 60))
        max_per_ip = int(getattr(settings, "PASSWORD_RESET_MAX_REQUESTS_PER_IP", 8))
        max_per_email = int(getattr(settings, "PASSWORD_RESET_MAX_REQUESTS_PER_EMAIL", 4))

        ip_count = int(cache.get(ip_key, 0) or 0)
        email_count = int(cache.get(email_key, 0) or 0)
        if ip_count >= max_per_ip or email_count >= max_per_email:
            messages.info(
                self.request,
                "Si ya solicitaste un enlace, espera unos minutos antes de volver a intentar.",
            )
            return redirect(self.get_success_url())

        cache.set(ip_key, ip_count + 1, timeout=window_seconds)
        cache.set(email_key, email_count + 1, timeout=window_seconds)
        return super().form_valid(form)


def login_view(request):
    """User login view."""
    if request.user.is_authenticated:
        return redirect("login_redirect")

    username_prefill = ""
    remember_me_checked = True

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        remember_me_checked = request.POST.get("remember_me", "").strip().lower() in {"1", "true", "on", "yes"}
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
                return render(
                    request,
                    "accounts/login.html",
                    {
                        "username_prefill": username_prefill,
                        "remember_me_checked": remember_me_checked,
                    },
                )
            cache.delete(lock_key)

        user = authenticate(request, username=username, password=password)

        if user is not None:
            cache.delete(attempts_key)
            cache.delete(lock_key)
            login(request, user)
            if remember_me_checked:
                remember_age = max(int(getattr(settings, "REMEMBER_ME_SESSION_AGE", 60 * 60 * 24 * 30)), 300)
                request.session.set_expiry(remember_age)
                request.session["_idle_timeout_seconds"] = remember_age
            else:
                idle_timeout = max(int(getattr(settings, "SESSION_IDLE_TIMEOUT_SECONDS", 60 * 45)), 300)
                request.session.set_expiry(int(getattr(settings, "SESSION_COOKIE_AGE", 60 * 60 * 8)))
                request.session["_idle_timeout_seconds"] = idle_timeout
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

    return render(
        request,
        "accounts/login.html",
        {
            "username_prefill": username_prefill,
            "remember_me_checked": remember_me_checked,
        },
    )


@login_required
def login_redirect(request):
    """
    Redirect after login based on user role.
    Admin sees choice screen, client goes to catalog.
    """
    companies = list(get_user_companies(request.user))
    active_company = get_active_company(request)
    if request.user.is_staff and len(companies) > 1 and not active_company:
        target = reverse("admin_dashboard") if request.user.is_staff else reverse("catalog")
        return redirect(f"{reverse('select_company')}?next={target}")
    if request.user.is_staff:
        return render(request, "accounts/admin_redirect.html")
    if not active_company and companies:
        preferred_company = get_default_client_origin_company()
        if preferred_company and any(company.pk == preferred_company.pk for company in companies):
            set_active_company(request, preferred_company)
        else:
            set_active_company(request, companies[0])
    return redirect("catalog")


def logout_view(request):
    """User logout view."""
    if request.user.is_authenticated and request.user.is_staff:
        from core.models import UserActivity

        UserActivity.objects.filter(user=request.user).update(is_online=False)

    logout(request)
    messages.info(request, "Has cerrado sesion.")
    return redirect("home")


@login_required
def select_company(request):
    """
    Mandatory company selection for multi-company users.
    """
    companies = list(get_user_companies(request.user))
    if not companies:
        messages.error(request, "No tenes empresas habilitadas para operar.")
        return redirect("home")

    next_url = request.GET.get("next", "").strip()
    if next_url and not url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        next_url = ""

    default_next = "admin_dashboard" if request.user.is_staff else "catalog"
    redirect_target = next_url or reverse(default_next)

    if len(companies) == 1:
        set_active_company(request, companies[0])
        return redirect(redirect_target)

    active_company = get_active_company(request)
    if request.method == "POST":
        post_next = request.POST.get("next", "").strip()
        if post_next:
            next_url = post_next
            if not url_has_allowed_host_and_scheme(
                next_url,
                allowed_hosts={request.get_host()},
                require_https=request.is_secure(),
            ):
                next_url = ""
        company_id = request.POST.get("company_id", "").strip()
        company = None
        if company_id.isdigit():
            company = Company.objects.filter(pk=int(company_id), is_active=True).first()
        if not company or not user_has_company_access(request.user, company):
            messages.error(request, "Selecciona una empresa valida.")
        else:
            set_active_company(request, company)
            return redirect(next_url or redirect_target)

    return render(
        request,
        "accounts/select_company.html",
        {
            "companies": companies,
            "active_company": active_company,
            "next_url": next_url or redirect_target,
        },
    )


def account_request(request):
    """Account request form for new B2B clients."""
    if request.user.is_authenticated:
        return redirect("catalog")

    if request.method == "POST":
        honeypot_name = getattr(settings, "ACCOUNT_REQUEST_HONEYPOT_FIELD", "website")
        honeypot_value = request.POST.get(honeypot_name, "").strip()
        if honeypot_value:
            messages.success(
                request,
                "Solicitud enviada. Nos pondremos en contacto pronto para activar tu cuenta.",
            )
            return redirect("home")

        submissions_key, last_submit_session_key = _build_account_request_keys(request)
        current_ts = int(timezone.now().timestamp())
        min_interval = int(getattr(settings, "ACCOUNT_REQUEST_MIN_INTERVAL_SECONDS", 0))
        last_submit_ts = int(request.session.get(last_submit_session_key, 0) or 0)
        if min_interval and last_submit_ts and current_ts - last_submit_ts < min_interval:
            messages.error(
                request,
                "Espera unos segundos antes de volver a enviar la solicitud.",
            )
            return render(
                request,
                "accounts/account_request.html",
                {"honeypot_field_name": honeypot_name},
            )

        max_submissions = int(getattr(settings, "ACCOUNT_REQUEST_MAX_SUBMISSIONS", 5))
        window_seconds = int(getattr(settings, "ACCOUNT_REQUEST_WINDOW_SECONDS", 3600))
        submissions_count = int(cache.get(submissions_key, 0))
        if submissions_count >= max_submissions:
            messages.error(
                request,
                "Se alcanzó el límite de solicitudes para esta conexión. Intenta nuevamente más tarde.",
            )
            return render(
                request,
                "accounts/account_request.html",
                {"honeypot_field_name": honeypot_name},
            )

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
            cache.set(submissions_key, submissions_count + 1, timeout=window_seconds)
            request.session[last_submit_session_key] = current_ts
            messages.success(
                request,
                "Solicitud enviada. Nos pondremos en contacto pronto para activar tu cuenta.",
            )
            return redirect("home")

    return render(
        request,
        "accounts/account_request.html",
        {"honeypot_field_name": getattr(settings, "ACCOUNT_REQUEST_HONEYPOT_FIELD", "website")},
    )
