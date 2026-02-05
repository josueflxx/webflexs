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
