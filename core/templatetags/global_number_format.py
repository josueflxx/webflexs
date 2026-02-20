from django import template
from django.template.defaultfilters import floatformat as django_floatformat

register = template.Library()


@register.filter(name="floatformat", is_safe=True)
def grouped_floatformat(value, arg=-1):
    """
    Global floatformat override:
    - keep regular floatformat behavior
    - force thousands grouping unless caller explicitly requests unlocalized output (u).
    """
    arg_text = str(arg) if arg is not None else "-1"
    if arg_text == "":
        arg_text = "-1"

    if "u" in arg_text:
        return django_floatformat(value, arg_text)

    if "g" not in arg_text:
        arg_text = f"{arg_text}g"

    return django_floatformat(value, arg_text)
