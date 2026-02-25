"""Advanced search helpers with safe fallback on non-PostgreSQL backends."""

from django.conf import settings
from django.db import DatabaseError, connection
from django.db.models import F, Q

try:
    from django.contrib.postgres.search import TrigramSimilarity
except Exception:  # pragma: no cover - backend optional
    TrigramSimilarity = None


def build_text_query(fields, term):
    """Build OR query for icontains over many fields."""
    query = Q()
    for field in fields:
        query |= Q(**{f"{field}__icontains": term})
    return query


def _can_use_trigram():
    return bool(
        getattr(settings, "FEATURE_ADVANCED_SEARCH_ENABLED", False)
        and TrigramSimilarity is not None
        and connection.vendor == "postgresql"
    )


def apply_text_search(queryset, term, fields):
    """
    Apply robust text search:
    - default: icontains on all fields
    - advanced: icontains OR trigram similarity if enabled and available
    """
    cleaned = str(term or "").strip()
    if not cleaned:
        return queryset

    base_query = build_text_query(fields, cleaned)
    if not _can_use_trigram():
        return queryset.filter(base_query)

    try:
        similarity_sum = None
        for field in fields:
            current = TrigramSimilarity(field, cleaned)
            similarity_sum = current if similarity_sum is None else similarity_sum + current
        return (
            queryset.annotate(_search_similarity=similarity_sum)
            .filter(base_query | Q(_search_similarity__gte=0.20))
            .order_by(F("_search_similarity").desc(nulls_last=True))
        )
    except DatabaseError:
        # If extension is not enabled or query fails, fallback transparently.
        return queryset.filter(base_query)
