from django import template

register = template.Library()

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

