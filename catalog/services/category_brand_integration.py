from dataclasses import dataclass

from django.db.models import Q

from catalog.models import (
    Brand,
    BrandAlias,
    BrandRubroProductOrder,
    BrandSubrubroProductOrder,
    Category,
    CategoryBrandMapping,
    Product,
)


def normalize_taxonomy_text(value):
    return BrandAlias.normalize(value)


def _contains_phrase(haystack, needle):
    haystack_tokens = normalize_taxonomy_text(haystack).split()
    needle_tokens = normalize_taxonomy_text(needle).split()
    if not needle_tokens or len(needle_tokens) > len(haystack_tokens):
        return False
    width = len(needle_tokens)
    return any(
        haystack_tokens[index:index + width] == needle_tokens
        for index in range(len(haystack_tokens) - width + 1)
    )


def products_for_category(category, *, include_descendants=True, active_only=True):
    category_ids = (
        category.get_descendant_ids(include_self=True)
        if include_descendants
        else [category.pk]
    )
    products = Product.objects.filter(
        Q(category_id__in=category_ids) | Q(categories__id__in=category_ids)
    ).distinct()
    if active_only:
        products = products.filter(is_active=True)
    return products


def _brand_terms():
    brands = list(
        Brand.objects.prefetch_related("aliases", "rubros__subrubros__helper_categories")
        .order_by("order", "name")
    )
    terms = {}
    for brand in brands:
        values = [(brand.name, 100, "nombre de marca")]
        values.extend(
            (alias.value, 96, "alias configurado")
            for alias in brand.aliases.all()
            if alias.is_active
        )
        terms[brand.pk] = (brand, values)
    return terms


def _suggest_rubro(category, brand):
    if brand is None:
        return None

    path_parts = []
    node = category.parent
    while node:
        path_parts.append(normalize_taxonomy_text(node.name))
        node = node.parent
    stripped_source = normalize_taxonomy_text(category.name)
    candidates = []
    for rubro in brand.rubros.all():
        normalized_rubro = normalize_taxonomy_text(rubro.name)
        score = 0
        if normalized_rubro in path_parts:
            score = 100
        elif any(
            _contains_phrase(part, normalized_rubro) or _contains_phrase(normalized_rubro, part)
            for part in path_parts
            if part
        ):
            score = 88
        elif normalized_rubro and _contains_phrase(stripped_source, normalized_rubro):
            score = 82
        if score:
            candidates.append((score, rubro))
    candidates.sort(key=lambda item: (-item[0], item[1].order, item[1].name))
    return candidates[0][1] if candidates else None


def _suggest_subrubro(category, rubro):
    if rubro is None:
        return None
    category_ids = set(category.get_ancestor_ids(include_self=True))
    path = normalize_taxonomy_text(category.get_full_path())
    candidates = []
    for subrubro in rubro.subrubros.all():
        helper_ids = {helper.pk for helper in subrubro.helper_categories.all()}
        score = 100 if helper_ids & category_ids else 0
        normalized_name = normalize_taxonomy_text(subrubro.name)
        if not score and normalized_name and _contains_phrase(path, normalized_name):
            score = 90
        if score:
            candidates.append((score, subrubro))
    candidates.sort(key=lambda item: (-item[0], item[1].order, item[1].name))
    return candidates[0][1] if candidates else None


@dataclass
class CategoryBrandCandidate:
    category: Category
    matches: list
    suggested_brand: Brand | None
    suggested_rubro: object | None
    suggested_subrubro: object | None
    canonical_category: Category | None
    confidence: int
    reason: str
    product_count: int
    mapping: CategoryBrandMapping | None = None

    @property
    def has_conflict(self):
        return len(self.matches) > 1

    @property
    def ready(self):
        return bool(self.suggested_brand and self.suggested_rubro and not self.has_conflict)


def detect_category_brand_candidates(*, categories=None):
    categories = list(
        categories
        if categories is not None
        else Category.objects.select_related("parent").order_by("order", "name")
    )
    mappings = {
        mapping.source_category_id: mapping
        for mapping in CategoryBrandMapping.objects.select_related(
            "brand", "brand_rubro", "brand_subrubro", "canonical_category", "last_batch"
        )
    }
    brand_terms = _brand_terms()
    results = []

    for category in categories:
        matches = []
        for brand, values in brand_terms.values():
            best = None
            for term, confidence, reason in values:
                if _contains_phrase(category.name, term):
                    candidate = {
                        "brand": brand,
                        "term": term,
                        "confidence": confidence,
                        "reason": reason,
                    }
                    if best is None or candidate["confidence"] > best["confidence"]:
                        best = candidate
            if best:
                matches.append(best)

        mapping = mappings.get(category.pk)
        if not matches and not mapping:
            continue

        matches.sort(key=lambda item: (-item["confidence"], item["brand"].name))
        suggested_brand = mapping.brand if mapping else (matches[0]["brand"] if len(matches) == 1 else None)
        suggested_rubro = mapping.brand_rubro if mapping else _suggest_rubro(category, suggested_brand)
        suggested_subrubro = (
            mapping.brand_subrubro
            if mapping
            else _suggest_subrubro(category, suggested_rubro)
        )
        canonical_category = mapping.canonical_category if mapping else category.parent
        product_count = products_for_category(
            category,
            include_descendants=mapping.include_descendants if mapping else True,
        ).count()
        results.append(
            CategoryBrandCandidate(
                category=category,
                matches=matches,
                suggested_brand=suggested_brand,
                suggested_rubro=suggested_rubro,
                suggested_subrubro=suggested_subrubro,
                canonical_category=canonical_category,
                confidence=100 if mapping else (matches[0]["confidence"] if len(matches) == 1 else 0),
                reason="vinculo confirmado" if mapping else (matches[0]["reason"] if len(matches) == 1 else "multiples marcas detectadas"),
                product_count=product_count,
                mapping=mapping,
            )
        )

    return results


def category_brand_integration_metrics(candidates=None):
    candidates = list(candidates if candidates is not None else detect_category_brand_candidates())
    affected_category_ids = set()
    for candidate in candidates:
        include_descendants = (
            candidate.mapping.include_descendants if candidate.mapping else True
        )
        if include_descendants:
            affected_category_ids.update(
                candidate.category.get_descendant_ids(include_self=True)
            )
        else:
            affected_category_ids.add(candidate.category.pk)
    affected_products = Product.objects.filter(
        Q(category_id__in=affected_category_ids)
        | Q(categories__id__in=affected_category_ids),
        is_active=True,
    ).values("pk").distinct().count()
    return {
        "candidate_categories": len(candidates),
        "affected_products": affected_products,
        "mapped_categories": sum(1 for candidate in candidates if candidate.mapping),
        "ready_categories": sum(1 for candidate in candidates if candidate.ready and not candidate.mapping),
        "conflicts": sum(1 for candidate in candidates if candidate.has_conflict and not candidate.mapping),
    }


def mapping_assignment_stats(mapping):
    products = products_for_category(
        mapping.source_category,
        include_descendants=mapping.include_descendants,
    )
    product_ids = set(products.values_list("pk", flat=True))
    if mapping.brand_subrubro_id:
        assigned_ids = set(
            BrandSubrubroProductOrder.objects.filter(
                brand_subrubro=mapping.brand_subrubro,
                product_id__in=product_ids,
            ).values_list("product_id", flat=True)
        )
    else:
        assigned_ids = set(
            BrandRubroProductOrder.objects.filter(
                brand_rubro=mapping.brand_rubro,
                product_id__in=product_ids,
            ).values_list("product_id", flat=True)
        )
    return {
        "product_ids": sorted(product_ids),
        "pending_ids": sorted(product_ids - assigned_ids),
        "total": len(product_ids),
        "assigned": len(assigned_ids),
        "pending": len(product_ids - assigned_ids),
    }
