"""Reusable backend scopes for company-owned business records.

Views must resolve objects from these querysets instead of loading a global
primary key and checking the company afterwards.  This keeps object lookup and
authorization in one database query and avoids leaking whether an object from
another company exists.
"""

from core.services.company_context import get_user_companies


def _allowed_companies(user, company=None):
    companies = get_user_companies(user)
    if company is None:
        return companies
    if companies.filter(pk=getattr(company, "pk", None)).exists():
        return companies.filter(pk=company.pk)
    return companies.none()


def orders_visible_to(user, *, company=None):
    """Return orders owned by a company the user may access."""
    from orders.models import Order

    return Order.objects.filter(company__in=_allowed_companies(user, company=company))


def clients_visible_to(user, *, company=None):
    """Return clients with an active link to an authorized company."""
    from accounts.models import ClientProfile

    companies = _allowed_companies(user, company=company)
    return ClientProfile.objects.filter(
        company_links__company__in=companies,
        company_links__is_active=True,
    ).distinct()


def client_links_fully_manageable_by(user, client):
    """Deny global profile edits when active links escape the user's scope.

    ClientProfile still contains fields shared by every linked company.  Until
    fiscal/commercial profiles are split, allowing a partial-scope operator to
    edit such a profile would change data belonging to another company.
    """
    if not user or not getattr(user, "is_authenticated", False) or not client:
        return False
    if getattr(user, "is_superuser", False):
        return True

    allowed_ids = set(get_user_companies(user).values_list("id", flat=True))
    # Inactive links still own historical documents that reference the shared
    # ClientProfile, so they remain part of the authorization boundary.
    linked_ids = set(client.company_links.values_list("company_id", flat=True))
    return bool(linked_ids) and linked_ids.issubset(allowed_ids)
