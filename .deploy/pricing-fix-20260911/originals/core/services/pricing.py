"""Pricing helpers for price lists and discounts."""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from accounts.models import ClientCategoryCompanyRule
from catalog.models import PriceListItem


DECIMAL_ZERO = Decimal("0")
MONEY_QUANT = Decimal("0.01")


@dataclass
class PricingResult:
    product_id: int
    base_price: Decimal
    final_price: Decimal
    discount_percentage: Decimal
    price_list_id: int | None


def _to_decimal(value):
    if value is None:
        return DECIMAL_ZERO
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _clamp_discount(value):
    if value is None:
        return DECIMAL_ZERO
    value = _to_decimal(value)
    if value < 0:
        return DECIMAL_ZERO
    if value > 100:
        return Decimal("100")
    return value


def resolve_pricing_context(user=None, company=None):
    client_profile = getattr(user, "client_profile", None) if user else None
    client_company = None
    client_category = None
    if client_profile and company:
        client_company = client_profile.get_company_link(company)
        client_category = client_profile.get_effective_client_category(company=company)
    return client_profile, client_company, client_category


def resolve_effective_price_list(company=None, client_company=None, client_category=None):
    if client_company and client_company.price_list_id:
        return client_company.price_list
    if company and client_category:
        rule = ClientCategoryCompanyRule.objects.filter(
            company=company,
            client_category=client_category,
            is_active=True,
        ).select_related("price_list").first()
        if rule and rule.price_list_id:
            return rule.price_list
    if company and getattr(company, "default_price_list_id", None):
        return company.default_price_list
    return None


def resolve_effective_discount_percentage(
    client_profile=None,
    company=None,
    client_company=None,
    client_category=None,
):
    if client_company and client_company.discount_percentage and client_company.discount_percentage != 0:
        return _clamp_discount(client_company.discount_percentage)
    if company and client_category:
        rule = ClientCategoryCompanyRule.objects.filter(
            company=company,
            client_category=client_category,
            is_active=True,
        ).first()
        if rule and rule.discount_percentage is not None and rule.discount_percentage != 0:
            return _clamp_discount(rule.discount_percentage)
    if client_category and getattr(client_category, "discount_percentage", None):
        if client_category.discount_percentage and client_category.discount_percentage != 0:
            return _clamp_discount(client_category.discount_percentage)
    if client_profile and getattr(client_profile, "discount", None):
        if client_profile.discount and client_profile.discount != 0:
            return _clamp_discount(client_profile.discount)
    return DECIMAL_ZERO


def build_price_list_item_map(price_list, product_ids):
    if not price_list or not product_ids:
        return {}
    items = PriceListItem.objects.filter(
        price_list=price_list,
        product_id__in=product_ids,
    ).only("product_id", "price")
    return {item.product_id: item for item in items}


def get_base_price_for_product(product, price_list=None, item_map=None):
    if price_list and item_map and product.id in item_map:
        return item_map[product.id].price
    if price_list:
        item = (
            PriceListItem.objects.filter(price_list=price_list, product_id=product.id)
            .only("price")
            .first()
        )
        if item:
            return item.price
    return product.price


def calculate_final_price(base_price, discount_percentage):
    discount_percentage = _clamp_discount(discount_percentage)
    if not base_price:
        return DECIMAL_ZERO
    factor = Decimal("1") - (discount_percentage / Decimal("100"))
    return (base_price * factor).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def get_product_pricing(product, user=None, company=None, price_list=None, item_map=None, context=None):
    if context:
        client_profile, client_company, client_category = context
    else:
        client_profile, client_company, client_category = resolve_pricing_context(user, company)
    if price_list is None:
        price_list = resolve_effective_price_list(company, client_company, client_category)
    discount_percentage = resolve_effective_discount_percentage(
        client_profile=client_profile,
        company=company,
        client_company=client_company,
        client_category=client_category,
    )
    base_price = get_base_price_for_product(product, price_list=price_list, item_map=item_map)
    final_price = calculate_final_price(base_price, discount_percentage)
    return PricingResult(
        product_id=product.id,
        base_price=base_price,
        final_price=final_price,
        discount_percentage=discount_percentage,
        price_list_id=price_list.pk if price_list else None,
    )


def calculate_cart_pricing(cart, user=None, company=None):
    if not cart:
        return {
            "subtotal": DECIMAL_ZERO,
            "discount_percentage": DECIMAL_ZERO,
            "discount_amount": DECIMAL_ZERO,
            "total": DECIMAL_ZERO,
            "price_list": None,
            "item_map": {},
        }
    client_profile, client_company, client_category = resolve_pricing_context(user, company)
    price_list = resolve_effective_price_list(company, client_company, client_category)
    discount_percentage = resolve_effective_discount_percentage(
        client_profile=client_profile,
        company=company,
        client_company=client_company,
        client_category=client_category,
    )
    items = list(cart.items.select_related("product", "clamp_request"))
    product_ids = [item.product_id for item in items if item.product_id]
    item_map = build_price_list_item_map(price_list, product_ids)

    subtotal = DECIMAL_ZERO
    for item in items:
        if not item.product_id:
            item.unit_price = DECIMAL_ZERO
            item.subtotal = DECIMAL_ZERO
            continue
        base_price = get_base_price_for_product(item.product, price_list=price_list, item_map=item_map)
        item.unit_price = base_price
        item.subtotal = (base_price * item.quantity).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
        subtotal += item.subtotal

    discount_amount = (subtotal * (discount_percentage / Decimal("100"))).quantize(
        MONEY_QUANT, rounding=ROUND_HALF_UP
    )
    total = (subtotal - discount_amount).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)

    return {
        "subtotal": subtotal,
        "discount_percentage": discount_percentage,
        "discount_amount": discount_amount,
        "total": total,
        "price_list": price_list,
        "item_map": item_map,
        "items": items,
    }
