"""Read-only verification for the category product block deployment."""

import hashlib
import json
import os
import sys
from pathlib import Path

os.environ["DJANGO_SETTINGS_MODULE"] = "flexs_project.settings.production"
sys.path.insert(0, "/var/www/webflexs")

import django

django.setup()

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, transaction
from django.test import RequestFactory
from django.urls import resolve, reverse

from catalog.models import Category, CategoryProductOrder, Product


CATEGORY_ID = 788


def fingerprint():
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")

        category_rows = list(
            Category.objects.filter(pk=CATEGORY_ID)
            .values_list("id", "name", "parent_id", "order", "public_order", "is_active", "visible_in_catalog")
        )
        child_rows = list(
            Category.objects.filter(parent_id=CATEGORY_ID)
            .order_by("id")
            .values_list("id", "name", "order", "public_order", "is_active", "visible_in_catalog")
        )
        order_rows = list(
            CategoryProductOrder.objects.filter(category_id=CATEGORY_ID)
            .order_by("product_id")
            .values_list("product_id", "block_label", "block_order", "sort_order")
        )
        link_rows = list(
            Product.categories.through.objects.filter(category_id=CATEGORY_ID)
            .order_by("product_id")
            .values_list("product_id", flat=True)
        )
        payload = {
            "category": category_rows,
            "children": child_rows,
            "orders": order_rows,
            "links": link_rows,
        }
        encoded = json.dumps(payload, cls=DjangoJSONEncoder, sort_keys=True, separators=(",", ":")).encode()
        print(json.dumps({
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "children": len(child_rows),
            "orders": len(order_rows),
            "links": len(link_rows),
        }, sort_keys=True))


def smoke():
    from core.models import Company

    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")

        category = Category.objects.get(pk=CATEGORY_ID)
        user = get_user_model().objects.get(
            username=getattr(settings, "ADMIN_PRIMARY_SUPERADMIN_USERNAME", "josueflexs")
        )
        company = Company.objects.get(slug="flexs", is_active=True)
        path = reverse("admin_category_products", args=[category.pk])
        request = RequestFactory().get(path, HTTP_HOST="flexsrepuestos.shop", secure=True)
        request.user = user
        request.session = {"active_company_id": company.pk}
        request._messages = FallbackStorage(request)
        request.resolver_match = resolve(path)
        response = request.resolver_match.func(request, **request.resolver_match.kwargs)
        assert response.status_code == 200, response.status_code
        html = response.content.decode()
        for marker in (
            'id="blockSelectionStatus"',
            'id="jumpToProductSelection"',
            'id="saveBlockAssignments"',
            "Guardar orden de bloques",
            "conversionsJsonInput",
        ):
            assert marker in html, marker

        view_source = Path("/var/www/webflexs/admin_panel/views/products.py").read_text(encoding="utf-8")
        service_source = Path("/var/www/webflexs/catalog/services/category_block_conversion.py").read_text(encoding="utf-8")
        assert "convert_category_blocks_to_subcategories(category, conversions=conversions)" in view_source
        assert '"before": before_snapshot' in service_source
        assert '"after": after_snapshot' in service_source

    print("READ_ONLY_BLOCK_SMOKE_OK: category 788 rendered with selection, save, conversion and rollback fixes")


{"fingerprint": fingerprint, "smoke": smoke}[sys.argv[1]]()
