"""Bounded read-only request profiling with an isolated per-process cache."""
import functools
import json
import os
import re
import sys
import time
from collections import defaultdict
from contextlib import ExitStack
from unittest.mock import patch

sys.path.insert(0, "/var/www/webflexs")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "flexs_project.settings.production")
import django
django.setup()

from django.conf import settings
from django.contrib.auth.models import AnonymousUser, User
from django.db import connection, transaction
from django.test import RequestFactory, override_settings
from accounts.models import ClientCompany
from catalog import views
from catalog.models import Category
from core.models import Company
from core import context_processors

print(json.dumps({"configured_cache_backend": settings.CACHES["default"]["BACKEND"]}), flush=True)
company = Company.objects.get(slug="flexs")
admin = User.objects.filter(is_superuser=True, is_active=True).order_by("id").first()
link = ClientCompany.objects.filter(company=company, is_active=True, client_profile__is_approved=True,
    client_profile__user__is_active=True).select_related("client_profile__user").order_by("id").first()
client = link.client_profile.user if link else AnonymousUser()
clamps = Category.objects.filter(name__iexact="ABRAZADERAS", is_active=True, visible_in_catalog=True).first()
cases = [
    ("admin_grid_cold", admin, {"view": "grid"}),
    ("admin_grid_warm", admin, {"view": "grid"}),
    ("client_grid_warm", client, {"view": "grid"}),
    ("client_search_sku", client, {"view": "grid", "q": "BA04001"}),
]
if clamps:
    cases.append(("client_clamps", client, {"view": "grid", "category": clamps.slug}))

with override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "isolated-catalog-audit"}}):
    for name, user, params in cases:
        groups = defaultdict(lambda: {"count": 0, "seconds": 0.0})
        phases = defaultdict(lambda: {"calls": 0, "seconds": 0.0, "queries": 0})
        trace = []
        sql_seconds = [0.0]
        query_count = [0]

        def execute(executor, sql, parameters, many, context):
            started = time.perf_counter()
            try:
                return executor(sql, parameters, many, context)
            finally:
                elapsed = time.perf_counter() - started
                normalized = re.sub(r"\s+", " ", sql).strip()
                groups[normalized]["count"] += 1
                groups[normalized]["seconds"] += elapsed
                sql_seconds[0] += elapsed
                query_count[0] += 1
                if trace:
                    phases[trace[-1]]["queries"] += 1

        def traced(label, function):
            @functools.wraps(function)
            def inner(*args, **kwargs):
                started = time.perf_counter()
                trace.append(label)
                try:
                    return function(*args, **kwargs)
                finally:
                    trace.pop()
                    phases[label]["calls"] += 1
                    phases[label]["seconds"] += time.perf_counter() - started
            return inner

        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
                cursor.execute("SET LOCAL statement_timeout = '8s'")
            request = RequestFactory().get("/catalogo/", params, HTTP_HOST="flexsrepuestos.shop")
            request.user = user
            request.session = {"active_company_id": company.pk}
            with ExitStack() as stack:
                # Search/category analytics are writes, so disable only those in this diagnostic process.
                stack.enter_context(patch.object(views, "log_catalog_analytics", return_value=None))
                for module, attribute in (
                    (views, "get_cached_category_tree_rows"),
                    (views, "get_public_category_ids_with_products"),
                    (views, "get_product_pricing"),
                    (views, "render"),
                    (context_processors, "site_settings"),
                    (context_processors, "active_admins"),
                    (context_processors, "active_company_context"),
                    (context_processors, "latest_catalog_excel_source_change"),
                ):
                    stack.enter_context(patch.object(module, attribute, traced(attribute, getattr(module, attribute))))
                stack.enter_context(connection.execute_wrapper(execute))
                started = time.perf_counter()
                response = views.catalog(request)
                elapsed = time.perf_counter() - started
            ordered = sorted(groups.items(), key=lambda entry: entry[1]["seconds"], reverse=True)
            print(json.dumps({
                "case": name, "status": response.status_code, "seconds": round(elapsed, 4),
                "queries": query_count[0], "sql_seconds": round(sql_seconds[0], 4), "html_bytes": len(response.content),
                "phases": {key: {"calls": value["calls"], "seconds": round(value["seconds"], 4), "direct_queries": value["queries"]} for key, value in phases.items()},
                "top_sql": [{"count": value["count"], "seconds": round(value["seconds"], 4), "shape": key[:320]} for key, value in ordered[:7]],
            }), flush=True)
