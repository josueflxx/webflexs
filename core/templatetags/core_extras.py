from django import template

register = template.Library()


@register.simple_tag
def product_detail_link(product, request, return_url=None):
    from urllib.parse import urlencode
    from django.urls import reverse
    from catalog.services.presentation import safe_catalog_return_url
    destination = safe_catalog_return_url(return_url or request.get_full_path())
    return reverse("product_detail", args=[product.sku]) + "?" + urlencode({"next": destination})

@register.filter
def get_item(dictionary, key):
    """
    Get value from dictionary by key.
    Usage: {{ active_filters|get_item:attr.slug }}
    """
    if dictionary:
        return dictionary.get(key)
    return None

@register.filter
def calculate_discount(price, discount_percentage):
    """
    Subtract discount percentage from price.
    discount_percentage is expected as 0.1 for 10%
    """
    try:
        if not discount_percentage:
            return price
        # If percentage is > 1 (e.g. 10), convert to 0.1
        if discount_percentage > 1:
            discount_percentage = discount_percentage / 100
        return price * (1 - discount_percentage)
    except:
        return price


@register.filter
def multiply(value, arg):
    """
    Multiplies the value by the argument.
    Usage: {{ value|multiply:100 }}
    """
    try:
        return float(value) * float(arg)
    except (ValueError, TypeError):
        return 0


@register.simple_tag
def querystring(request, **kwargs):
    """
    Build querystring preserving current params and overriding provided keys.
    Usage: {% querystring request page=2 order='name' %}
    """
    query = request.GET.copy()
    for key, value in kwargs.items():
        if value in (None, ''):
            query.pop(key, None)
        else:
            query[key] = value
    return query.urlencode()

