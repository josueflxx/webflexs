"""Conservative duplicate detection that only creates human-review records."""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from itertools import combinations

from django.db import transaction
from django.utils import timezone

from catalog.models import Product, ProductDuplicateReview


MAX_ALL_PAIRS_GROUP_SIZE = 25


def normalize_identity(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^A-Z0-9]+", "", text.upper())


def build_duplicate_plan(queryset=None):
    source = Product.objects.all() if queryset is None else queryset
    products = list(source.only("id", "sku", "name"))
    groups = {
        ProductDuplicateReview.REASON_SKU: defaultdict(list),
        ProductDuplicateReview.REASON_NAME: defaultdict(list),
    }
    for product in products:
        sku_identity = normalize_identity(product.sku)
        name_identity = normalize_identity(product.name)
        if sku_identity:
            groups[ProductDuplicateReview.REASON_SKU][sku_identity].append(product)
        if len(name_identity) >= 8:
            groups[ProductDuplicateReview.REASON_NAME][name_identity].append(product)

    candidates = {}
    for reason, identities in groups.items():
        for identity, matches in identities.items():
            if len(matches) < 2:
                continue
            ordered_matches = sorted(matches, key=lambda item: item.pk)
            pairs = (
                combinations(ordered_matches, 2)
                if len(ordered_matches) <= MAX_ALL_PAIRS_GROUP_SIZE
                else ((ordered_matches[0], candidate) for candidate in ordered_matches[1:])
            )
            for first, second in pairs:
                key = (first.pk, second.pk, reason)
                candidates[key] = {
                    "primary_product_id": first.pk,
                    "candidate_product_id": second.pk,
                    "reason": reason,
                    "confidence": 100 if reason == ProductDuplicateReview.REASON_SKU else 85,
                    "evidence": {
                        "normalized_value": identity,
                        "primary_sku": first.sku,
                        "candidate_sku": second.sku,
                        "primary_name": first.name,
                        "candidate_name": second.name,
                    },
                }
    return list(candidates.values())


def refresh_duplicate_reviews(*, apply=False, queryset=None):
    plan = build_duplicate_plan(queryset=queryset)
    result = {
        "mode": "apply" if apply else "dry_run",
        "candidates": len(plan),
        "created": 0,
        "pending_refreshed": 0,
        "reviewed_preserved": 0,
        "items": plan,
    }
    if not apply:
        return result

    with transaction.atomic():
        for item in plan:
            review, created = ProductDuplicateReview.objects.get_or_create(
                primary_product_id=item["primary_product_id"],
                candidate_product_id=item["candidate_product_id"],
                reason=item["reason"],
                defaults={
                    "confidence": item["confidence"],
                    "evidence": item["evidence"],
                },
            )
            if created:
                result["created"] += 1
            elif review.status == ProductDuplicateReview.STATUS_PENDING:
                review.confidence = item["confidence"]
                review.evidence = item["evidence"]
                review.save(update_fields=["confidence", "evidence", "updated_at"])
                result["pending_refreshed"] += 1
            else:
                result["reviewed_preserved"] += 1
    return result


def review_duplicate(review, *, status, user=None, notes=""):
    allowed = {
        ProductDuplicateReview.STATUS_NOT_DUPLICATE,
        ProductDuplicateReview.STATUS_CONFIRMED,
    }
    if status not in allowed:
        raise ValueError("Estado de revision invalido.")
    review.status = status
    review.reviewed_by = user
    review.reviewed_at = timezone.now()
    review.notes = str(notes or "")
    review.save(
        update_fields=["status", "reviewed_by", "reviewed_at", "notes", "updated_at"]
    )
    return review
