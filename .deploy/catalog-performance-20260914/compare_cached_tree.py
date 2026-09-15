"""Read-only A/B: reuse the identical category rows in this process only."""
import json
import os
import sys
import time
from contextlib import ExitStack
from unittest.mock import patch

sys.path.insert(0, "/var/www/webflexs")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "flexs_project.settings.production")
import django
django.setup()
from django.contrib.auth.models import User
from django.db import connection, transaction
from django.test import RequestFactory, override_settings
from accounts.models import ClientCompany
from catalog import views
from catalog.models import Category
from core.models import Company

company = Company.objects.get(slug="flexs")
client_id = ClientCompany.objects.filter(company=company, is_active=True,
    client_profile__is_approved=True, client_profile__user__is_active=True
).order_by("id").values_list("client_profile__user_id", flat=True).first()
admin_id = User.objects.filter(is_superuser=True, is_active=True).order_by("id").values_list("id", flat=True).first()
clamps = Category.objects.filter(name__iexact="ABRAZADERAS", is_active=True, visible_in_catalog=True).first()
original_render = views.render
observed = {}

def capture_render(request, template, context, **kwargs):
    observed["categories"] = [(row["category"].pk, row["depth"], row["full_path"]) for row in context["category_tree_rows"]]
    observed["products"] = [(p.pk, str(p.price), str(getattr(p, "final_price", None)), str(getattr(p, "display_price", None))) for p in context["page_obj"]]
    observed["count"] = context["page_obj"].paginator.count
    observed["discount"] = str(context["discount"])
    observed["show_prices"] = context["show_prices"]
    return original_render(request, template, context, **kwargs)

with override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "isolated-catalog-ab"}}), transaction.atomic():
    with connection.cursor() as cursor:
        cursor.execute("SET TRANSACTION READ ONLY")
        cursor.execute("SET LOCAL statement_timeout = '8s'")
    rows = views.get_cached_category_tree_rows()
    # Keep analytics disabled in this diagnostic; no sessions or DB data are saved.
    with patch.object(views, "log_catalog_analytics", return_value=None), patch.object(views, "render", capture_render):
        cases = [("client_grid", client_id, {"view": "grid"}), ("admin_grid", admin_id, {"view": "grid"})]
        if clamps:
            cases.append(("client_clamps", client_id, {"view": "grid", "category": clamps.slug}))
        for label, user_id, params in cases:
            baseline = None
            for reuse in (False, True):
                observed.clear()
                request = RequestFactory().get("/catalogo/", params, HTTP_HOST="flexsrepuestos.shop")
                request.user = User.objects.get(pk=user_id)
                request.session = {"active_company_id": company.pk}
                with ExitStack() as stack:
                    if reuse:
                        stack.enter_context(patch.object(views, "get_cached_category_tree_rows", return_value=rows))
                    started = time.perf_counter()
                    response = views.catalog(request)
                    elapsed = time.perf_counter() - started
                if not reuse:
                    baseline = dict(observed)
                print(json.dumps({"case": label, "reuse_identical_rows": reuse,
                    "seconds": round(elapsed, 4), "status": response.status_code,
                    "same_products_categories_prices": observed == baseline,
                    "category_rows": len(rows)}), flush=True)
