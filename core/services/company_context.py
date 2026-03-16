"""Helpers for resolving the active company context."""

from django.conf import settings
from django.db.models import Q

from core.models import AdminCompanyAccess, Company


DEFAULT_COMPANY_SLUG = getattr(settings, "DEFAULT_COMPANY_SLUG", "flexs")
DEFAULT_CLIENT_ORIGIN_COMPANY_SLUG = getattr(
    settings,
    "DEFAULT_CLIENT_ORIGIN_COMPANY_SLUG",
    DEFAULT_COMPANY_SLUG,
)
SESSION_COMPANY_KEY = "active_company_id"


def get_default_company():
    if DEFAULT_COMPANY_SLUG:
        company = Company.objects.filter(slug__iexact=DEFAULT_COMPANY_SLUG, is_active=True).first()
        if company:
            return company
    return Company.objects.filter(is_active=True).order_by("id").first()


def get_default_client_origin_company():
    if DEFAULT_CLIENT_ORIGIN_COMPANY_SLUG:
        company = Company.objects.filter(
            slug__iexact=DEFAULT_CLIENT_ORIGIN_COMPANY_SLUG,
            is_active=True,
        ).first()
        if company:
            return company
    return get_default_company()


def get_default_client_import_companies():
    companies = []
    seen_ids = set()
    configured_slugs = getattr(
        settings,
        "DEFAULT_CLIENT_IMPORT_COMPANY_SLUGS",
        [DEFAULT_CLIENT_ORIGIN_COMPANY_SLUG, DEFAULT_COMPANY_SLUG],
    ) or [DEFAULT_CLIENT_ORIGIN_COMPANY_SLUG]

    for raw_slug in configured_slugs:
        slug = str(raw_slug or "").strip()
        if not slug:
            continue
        company = Company.objects.filter(slug__iexact=slug, is_active=True).first()
        if company and company.id not in seen_ids:
            companies.append(company)
            seen_ids.add(company.id)

    if companies:
        return companies

    fallback_company = get_default_client_origin_company()
    if fallback_company:
        return [fallback_company]
    return []


def get_user_companies(user):
    if not user or not getattr(user, "is_authenticated", False):
        return Company.objects.none()
    if getattr(user, "is_staff", False):
        queryset = Company.objects.filter(is_active=True)
        if not getattr(user, "is_superuser", False):
            scoped_company_ids = list(
                AdminCompanyAccess.objects.filter(
                    user=user,
                    is_active=True,
                    company__is_active=True,
                ).values_list("company_id", flat=True)
            )
            if scoped_company_ids:
                queryset = queryset.filter(pk__in=scoped_company_ids)
        access_map = getattr(settings, "ADMIN_COMPANY_ACCESS", {}) or {}
        allowed_slugs = access_map.get(str(getattr(user, "username", "")).strip().lower())
        if allowed_slugs:
            query = Q()
            for slug in allowed_slugs:
                query |= Q(slug__iexact=slug)
            queryset = queryset.filter(query)
        elif getattr(settings, "ADMIN_COMPANY_ACCESS_REQUIRE_EXPLICIT", False) and not getattr(user, "is_superuser", False):
            return Company.objects.none()
        return queryset.order_by("name")
    profile = getattr(user, "client_profile", None)
    if not profile:
        return Company.objects.none()
    return (
        Company.objects.filter(
            client_links__client_profile=profile,
            client_links__is_active=True,
            is_active=True,
        )
        .distinct()
        .order_by("name")
    )


def user_has_company_access(user, company):
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if not company or not getattr(company, "is_active", False):
        return False
    if getattr(user, "is_staff", False):
        return get_user_companies(user).filter(pk=company.pk).exists()
    profile = getattr(user, "client_profile", None)
    if not profile:
        return False
    return profile.company_links.filter(
        company=company,
        is_active=True,
        company__is_active=True,
    ).exists()


def get_active_company(request):
    if request is None:
        return get_default_company()

    company_id = request.session.get(SESSION_COMPANY_KEY)
    if company_id:
        company = Company.objects.filter(id=company_id, is_active=True).first()
        if company and user_has_company_access(getattr(request, "user", None), company):
            return company
        request.session.pop(SESSION_COMPANY_KEY, None)

    user = getattr(request, "user", None)
    if user and getattr(user, "is_authenticated", False):
        companies = list(get_user_companies(user))
        if len(companies) == 1:
            set_active_company(request, companies[0])
            return companies[0]
        if len(companies) > 1:
            return None
        return None

    if getattr(settings, "ADMIN_COMPANY_ACCESS_REQUIRE_EXPLICIT", False):
        return None
    return get_default_company()


def set_active_company(request, company):
    if request is None:
        return
    if company is None:
        request.session.pop(SESSION_COMPANY_KEY, None)
        return
    request.session[SESSION_COMPANY_KEY] = company.pk
