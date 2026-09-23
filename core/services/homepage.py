"""Read-only links for the public homepage; no changes to catalog ordering."""
from urllib.parse import urlencode

from django.urls import reverse
from django.utils.text import slugify

from catalog.models import Category


HOME_FAMILIES = (
    ("abrazaderas", "Abrazaderas", "Fabricación propia · A medida"),
    ("elasticos", "Elásticos", "Para camiones y acoplados"),
    ("bujes", "Bujes", "Repuestos de suspensión"),
    ("fundicion", "Fundición", "Soportes y componentes"),
    ("pernos", "Pernos", "Unión y fijación"),
    ("buloneria", "Bulonería", "Elementos de sujeción"),
    ("suspension-neumatica", "Suspensión neumática", "Componentes y repuestos"),
    ("insertos-de-elastico", "Insertos de elástico", "Complementos de suspensión"),
    ("balancines", "Balancines", "Para el conjunto de suspensión"),
)


def home_family_links():
    # Match the public root, never an identically named branch under a brand.
    # Read all nine destinations in one query. If a family is absent/hidden,
    # use normal public search rather than an invalid category parameter.
    categories = Category.objects.filter(
        parent__isnull=True, is_active=True, visible_in_catalog=True,
    ).values("name", "public_name", "slug").order_by("public_order", "order", "id")
    by_name = {}
    for category in categories:
        for name in (category["public_name"], category["name"], category["slug"]):
            if name:
                by_name.setdefault(slugify(name), category["slug"])
    catalog_url = reverse("catalog")
    return [
        {
            "key": key,
            "name": name,
            "description": description,
            "url": catalog_url + "?" + urlencode(
                {"category": by_name[key]} if key in by_name else {"q": name}
            ),
        }
        for key, name, description in HOME_FAMILIES
    ]
