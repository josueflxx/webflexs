"""Advanced search helpers with safe fallback on non-PostgreSQL backends."""

import re

from django.conf import settings
from django.db import DatabaseError, connection
from django.db.models import F, Q, Value
from django.db.models.functions import Lower, Replace

try:
    from django.contrib.postgres.search import TrigramSimilarity
except Exception:  # pragma: no cover - backend optional
    TrigramSimilarity = None


SEARCH_TOKEN_PATTERN = re.compile(r'"([^"]+)"|(\S+)')
TOKEN_EDGE_TRIM_PATTERN = re.compile(r"^[,;|]+|[,;|]+$")
SEARCH_ACTION_LABEL_PATTERN = re.compile(r'(?i)^buscar\s+"(.+)"$')
COMPACT_SEARCH_PATTERN = re.compile(r"[^a-z0-9]+")
COMPACT_SEARCH_REPLACE_CHARS = (
    " ",
    "-",
    "/",
    ".",
    ",",
    "_",
    "(",
    ")",
    "[",
    "]",
    "{",
    "}",
    ":",
    ";",
    "|",
    "\\",
)


def build_text_query(fields, term):
    """Build OR query for icontains over many fields."""
    query = Q()
    for field in fields:
        query |= Q(**{f"{field}__icontains": term})
    return query


def sanitize_search_token(value):
    """
    Normalize free-text search token:
    - trim spaces and trailing punctuation noise
    - normalize unicode multiply sign used in dimensions
    """
    cleaned = str(value or "").strip()
    if not cleaned:
        return ""
    search_action_match = SEARCH_ACTION_LABEL_PATTERN.fullmatch(cleaned)
    if search_action_match:
        cleaned = search_action_match.group(1).strip()
        if not cleaned:
            return ""
    cleaned = TOKEN_EDGE_TRIM_PATTERN.sub("", cleaned).strip()
    cleaned = cleaned.replace("×", "x")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


def compact_search_token(value):
    """Normalize text for compact matching across punctuation and spaces."""
    cleaned = sanitize_search_token(value).lower()
    if not cleaned:
        return ""
    return COMPACT_SEARCH_PATTERN.sub("", cleaned)


def extract_search_tokens(raw_query):
    tokens = []
    for match in SEARCH_TOKEN_PATTERN.finditer(str(raw_query or "")):
        token = sanitize_search_token(match.group(1) or match.group(2) or "")
        if token:
            tokens.append((token, bool(match.group(1))))
    return tokens


def parse_text_search_query(
    raw_query,
    *,
    max_include=8,
    max_exclude=8,
    max_phrases=4,
):
    """
    Generic parser used by admin/catalog-like search bars.
    Supports:
    - "quoted phrases"
    - exclusions via -term
    """
    raw = sanitize_search_token(raw_query)
    parsed = {
        "raw": raw,
        "phrases": [],
        "include_terms": [],
        "exclude_terms": [],
    }
    if not raw:
        return parsed

    for token, is_phrase in extract_search_tokens(raw):
        cleaned = sanitize_search_token(token)
        if not cleaned:
            continue

        is_exclusion = cleaned.startswith("-") and len(cleaned) > 1
        if is_exclusion:
            cleaned = sanitize_search_token(cleaned[1:])
            if not cleaned:
                continue

        if is_phrase:
            parsed["phrases"].append(cleaned)
        elif is_exclusion:
            parsed["exclude_terms"].append(cleaned)
        else:
            parsed["include_terms"].append(cleaned)

    parsed["include_terms"] = parsed["include_terms"][:max_include]
    parsed["exclude_terms"] = parsed["exclude_terms"][:max_exclude]
    parsed["phrases"] = parsed["phrases"][:max_phrases]
    return parsed


def apply_parsed_text_search(
    queryset,
    parsed_query,
    fields,
    *,
    order_by_similarity=False,
    similarity_threshold=0.20,
):
    """
    Apply parsed query over a set of fields:
    - all include terms and phrases must match (AND across terms, OR across fields)
    - exclude terms are removed
    """
    if not parsed_query or not parsed_query.get("raw"):
        return queryset

    result = queryset
    for phrase in parsed_query.get("phrases", []):
        result = apply_text_search(
            result,
            phrase,
            fields,
            order_by_similarity=order_by_similarity,
            similarity_threshold=similarity_threshold,
        )
    for term in parsed_query.get("include_terms", []):
        result = apply_text_search(
            result,
            term,
            fields,
            order_by_similarity=order_by_similarity,
            similarity_threshold=similarity_threshold,
        )
    for term in parsed_query.get("exclude_terms", []):
        result = result.exclude(build_text_query(fields, term))
    return result


def _can_use_trigram():
    return bool(
        getattr(settings, "FEATURE_ADVANCED_SEARCH_ENABLED", False)
        and TrigramSimilarity is not None
        and connection.vendor == "postgresql"
    )


def apply_text_search(
    queryset,
    term,
    fields,
    *,
    order_by_similarity=True,
    similarity_threshold=0.20,
):
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
        result = (
            queryset.annotate(_search_similarity=similarity_sum)
            .filter(base_query | Q(_search_similarity__gte=similarity_threshold))
        )
        if order_by_similarity:
            result = result.order_by(F("_search_similarity").desc(nulls_last=True))
        return result
    except DatabaseError:
        # If extension is not enabled or query fails, fallback transparently.
        return queryset.filter(base_query)


def build_compact_text_expression(field_name):
    """Build a DB expression that removes common separators from a text field."""
    expression = Lower(F(field_name))
    for char in COMPACT_SEARCH_REPLACE_CHARS:
        expression = Replace(expression, Value(char), Value(""))
    return expression


def apply_compact_text_search(queryset, term, fields):
    """
    Apply compact matching over fields, ignoring common separators like spaces,
    hyphens and slashes.
    """
    compact_term = compact_search_token(term)
    if not compact_term:
        return queryset.none()

    annotations = {}
    query = Q()
    for index, field in enumerate(fields):
        alias = f"_compact_{index}"
        annotations[alias] = build_compact_text_expression(field)
        query |= Q(**{f"{alias}__contains": compact_term})
    return queryset.annotate(**annotations).filter(query)
