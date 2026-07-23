from django.db import transaction
from django.db.models import F, Q, Value
from django.db.models.functions import Replace

from accounts.models import ClientFiscalReview, ClientProfile
from core.services.sensitive_data import sanitize_sensitive_payload


def normalize_fiscal_document(value):
    return "".join(char for char in str(value or "") if char.isdigit())


def is_valid_cuit(value):
    digits = normalize_fiscal_document(value)
    if len(digits) != 11:
        return False
    weights = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)
    check = 11 - (sum(int(digit) * weight for digit, weight in zip(digits[:10], weights)) % 11)
    if check == 11:
        check = 0
    elif check == 10:
        check = 9
    return check == int(digits[-1])


def _normalized_field(field_name):
    expression = F(field_name)
    for token in ("-", ".", " ", "/"):
        expression = Replace(expression, Value(token), Value(""))
    return expression


def find_client_profiles_by_document(value, *, company=None, exclude_profile=None):
    normalized = normalize_fiscal_document(value)
    if not normalized:
        return ClientProfile.objects.none()
    queryset = ClientProfile.objects.annotate(
        normalized_cuit=_normalized_field("cuit_dni"),
        normalized_document=_normalized_field("document_number"),
    ).filter(
        Q(normalized_cuit=normalized) | Q(normalized_document=normalized)
    )
    if company is not None:
        queryset = queryset.filter(
            company_links__company=company,
            company_links__is_active=True,
        )
    if exclude_profile is not None:
        queryset = queryset.exclude(pk=getattr(exclude_profile, "pk", exclude_profile))
    return queryset.select_related("user").distinct().order_by("company_name", "id")


@transaction.atomic
def queue_client_fiscal_review(
    *,
    company,
    document,
    reason=ClientFiscalReview.REASON_DUPLICATE,
    candidates=None,
    requested_by=None,
    lookup_payload=None,
):
    normalized = normalize_fiscal_document(document)
    if not company or not normalized:
        raise ValueError("Empresa y documento fiscal son obligatorios para la revision.")
    review, created = ClientFiscalReview.objects.select_for_update().get_or_create(
        company=company,
        normalized_document=normalized,
        reason=reason,
        status=ClientFiscalReview.STATUS_PENDING,
        defaults={
            "requested_by": requested_by if getattr(requested_by, "is_authenticated", False) else None,
            "lookup_payload": sanitize_sensitive_payload(lookup_payload or {}),
        },
    )
    if not created:
        update_fields = []
        if lookup_payload:
            review.lookup_payload = sanitize_sensitive_payload(lookup_payload)
            update_fields.append("lookup_payload")
        if requested_by and getattr(requested_by, "is_authenticated", False):
            review.requested_by = requested_by
            update_fields.append("requested_by")
        if update_fields:
            review.save(update_fields=update_fields + ["updated_at"])
    if candidates is not None:
        review.candidate_profiles.add(*list(candidates))
    return review, created
