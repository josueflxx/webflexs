import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from catalog.models import Category, ClampSpecs, Product
from catalog.services.clamp_parser import ClampParser
from catalog.services.clamp_specs import sync_product_clamp_specs
from catalog.services.clamp_filters import build_clamp_filters


class ClampParserRegressionTests(SimpleTestCase):
    def test_real_shape_variants(self):
        for suffix, expected in (("CURVO", "CURVA"), ("PLANO", "PLANA"), ("SEMICURVO", "SEMICURVA"), ("S/CURVA", "SEMICURVA"), ("C", "CURVA"), ("P", "PLANA"), ("SC", "SEMICURVA"), ("S", "SEMICURVA")):
            with self.subTest(suffix=suffix):
                result = ClampParser.parse(f"ABRAZADERA TREFILADA DE 3/4 X 75 X 120 {suffix}")
                self.assertEqual(result["shape"], expected)
                self.assertEqual(result["parse_confidence"], 100)

    def test_mixed_fraction_and_unicode_separator(self):
        result = ClampParser.parse("ABRAZADERA FORJADA 1 1/8 × 80 × 220 C")
        self.assertEqual(result["diameter"], "1 1/8")
        self.assertEqual(result["width"], 80)
        self.assertEqual(result["length"], 220)
        self.assertEqual(result["parse_confidence"], 100)

    def test_ambiguous_shapes_and_missing_length_are_not_confident(self):
        result = ClampParser.parse("ABRAZADERA FORJADA 1X135X280 CURVA PLANA")
        self.assertIsNone(result["shape"])
        self.assertLess(result["parse_confidence"], 100)
        self.assertLess(ClampParser.parse("ABRAZADERA FORJADA DE 1 CURVA")["parse_confidence"], 100)

    def test_accessory_not_classified(self):
        self.assertEqual(ClampParser.parse("GUIA DE ABRAZADERA 1X135X280 C")["parse_confidence"], 0)


class ClampSpecsRegressionTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(sku="ABT3475120C", name="ABRAZADERA TREFILADA DE 3/4 X 75 X 120 CURVO", description="Repuesto para elásticos.", price=100)

    def test_name_not_hidden_by_generic_description_and_idempotent(self):
        self.assertEqual(sync_product_clamp_specs(self.product)["status"], "create")
        self.assertEqual(self.product.clamp_specs.shape, "CURVA")
        self.assertEqual(sync_product_clamp_specs(self.product)["status"], "unchanged")

    def test_manual_specs_never_overwritten(self):
        spec = ClampSpecs.objects.create(product=self.product, manual_override=True, width=999)
        self.assertEqual(sync_product_clamp_specs(self.product)["status"], "manual")
        spec.refresh_from_db()
        self.assertEqual(spec.width, 999)
        self.assertIsNone(spec.shape)

    def test_incomplete_source_preserves_known_dimensions(self):
        sync_product_clamp_specs(self.product)
        self.product.name = "ABRAZADERA TREFILADA DE 1/2"
        sync_product_clamp_specs(self.product)
        spec = ClampSpecs.objects.get(product=self.product)
        self.assertEqual(spec.diameter, "3/4")
        self.assertEqual(spec.length, 120)

    def test_conflicting_sources_require_review(self):
        self.product.description = "ABRAZADERA TREFILADA DE 3/4 X 80 X 120 CURVA"
        plan = sync_product_clamp_specs(self.product)
        self.assertTrue(plan["warnings"])
        self.assertIsNone(ClampSpecs.objects.get(product=self.product).width)

    def test_complete_edit_refreshes_specs(self):
        sync_product_clamp_specs(self.product)
        self.product.name = "ABRAZADERA TREFILADA DE 3/4 X 80 X 150 PLANO"
        sync_product_clamp_specs(self.product)
        spec = ClampSpecs.objects.get(product=self.product)
        self.assertEqual((spec.width, spec.length, spec.shape), (80, 150, "PLANA"))

    def test_grid_name_edit_syncs_specs(self):
        from core.services.company_context import get_default_company
        user = User.objects.create_superuser(username="josueflexs", email="local@example.test", password="test-only")
        self.client.force_login(user)
        session = self.client.session
        session["active_company_id"] = get_default_company().pk
        session.save()
        response = self.client.post(
            reverse("admin_product_grid_update_cell"),
            json.dumps({"product_id": self.product.pk, "field": "name", "value": "ABRAZADERA TREFILADA DE 3/4 X 80 X 150 PLANA"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        spec = ClampSpecs.objects.get(product=self.product)
        self.assertEqual((spec.width, spec.length, spec.shape), (80, 150, "PLANA"))

    def test_ordinary_product_skips_clamp_database_queries(self):
        self.product.name = "BUJE DE GOMA"
        with self.assertNumQueries(0):
            self.assertEqual(sync_product_clamp_specs(self.product)["status"], "ignored")

    def test_catalog_quick_edit_syncs_specs(self):
        from core.services.company_context import get_default_company
        user = User.objects.create_superuser(username="josueflexs", email="quick@example.test", password="test-only")
        self.client.force_login(user)
        session = self.client.session
        session["active_company_id"] = get_default_company().pk
        session.save()
        response = self.client.post(reverse("product_quick_edit", args=[self.product.pk]), {
            "sku": self.product.sku, "name": "ABRAZADERA TREFILADA DE 3/4 X 80 X 150 PLANA",
            "price": "100.00", "stock": "0", "is_active": "true",
        })
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(ClampSpecs.objects.get(product=self.product).width, 80)

    def test_command_preview_then_apply_with_backup(self):
        spec = ClampSpecs.objects.create(product=self.product, fabrication="TREFILADA", diameter="3/4", width=75, length=120)
        with TemporaryDirectory() as temp:
            path = Path(temp) / "backup.json"
            call_command("populate_clamp_specs", stdout=StringIO())
            spec.refresh_from_db()
            self.assertIsNone(spec.shape)
            with self.assertRaises(CommandError):
                call_command("populate_clamp_specs", apply=True, stdout=StringIO())
            call_command("populate_clamp_specs", apply=True, report=str(path), stdout=StringIO())
            spec.refresh_from_db()
            self.assertEqual(spec.shape, "CURVA")
            report = json.loads(path.read_text(encoding="utf-8"))
            self.assertIsNone(report["products"][0]["before"]["shape"])
            with self.assertRaises(CommandError):
                call_command("populate_clamp_specs", apply=True, report=str(path), stdout=StringIO())


class ClampCatalogFilterRegressionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.category = Category.objects.create(name="ABRAZADERAS", slug="abrazaderas", is_active=True, visible_in_catalog=True)
        for sku, diameter, width, shape in (("TEST-A", "7/16", 75, "CURVA"), ("TEST-B", "1 1/8", 80, "PLANA"), ("TEST-C", "3/4", 75, "CURVA")):
            p = Product.objects.create(sku=sku, name=f"ABRAZADERA {sku}", price=100, category=self.category, is_active=True)
            ClampSpecs.objects.create(product=p, fabrication="TREFILADA", diameter=diameter, width=width, length=120, shape=shape)

    def test_combined_filters_and_numeric_order(self):
        products, ctx, selected = build_clamp_filters(Product.objects.all(), {"width": "75", "shape": "CURVA"})
        self.assertEqual(products.count(), 2)
        self.assertEqual(ctx["clamp_options"]["diameter"], ["7/16", "3/4"])
        self.assertEqual(
            [option["value"] for option in ctx["clamp_all_options"]["diameter"]],
            ["7/16", "3/4", "1 1/8"],
        )
        self.assertEqual(selected["width"], "75")

    def test_facets_hide_unavailable_values_but_keep_selected_zero(self):
        _, ctx, _ = build_clamp_filters(Product.objects.all(), {"fabrication": "TREFILADA", "width": "75"})
        shape = next(field for field in ctx["clamp_filter_fields"] if field["name"] == "shape")
        self.assertEqual([option["value"] for option in shape["options"]], ["CURVA"])
        _, ctx, _ = build_clamp_filters(Product.objects.all(), {"width": "80"})
        shape = next(field for field in ctx["clamp_filter_fields"] if field["name"] == "shape")
        self.assertEqual([option["value"] for option in shape["options"]], ["PLANA"])
        _, ctx, _ = build_clamp_filters(Product.objects.all(), {"width": "80", "shape": "CURVA"})
        shape = next(field for field in ctx["clamp_filter_fields"] if field["name"] == "shape")
        self.assertEqual([option["value"] for option in shape["options"]], ["CURVA", "PLANA"])

    def test_facets_use_one_grouped_query(self):
        with self.assertNumQueries(1):
            build_clamp_filters(Product.objects.all(), {})

    def test_incompatible_selection_is_retained(self):
        products, ctx, _ = build_clamp_filters(Product.objects.all(), {"width": "999", "shape": "CURVA"})
        self.assertEqual(products.count(), 0)
        width = next(f for f in ctx["clamp_filter_fields"] if f["name"] == "width")
        self.assertTrue(any(o["selected"] and o["count"] == 0 for o in width["options"]))
        self.assertEqual(len(ctx["clamp_filter_fields"]), 5)

    def test_invalid_dimension_not_ignored(self):
        products, ctx, _ = build_clamp_filters(Product.objects.all(), {"length": "not-a-number"})
        self.assertEqual(products.count(), 0)
        self.assertTrue(ctx["clamp_filter_errors"])

    def test_render_search_preserves_filters_and_zero_result_controls(self):
        response = self.client.get(reverse("catalog"), {"category": "abrazaderas", "width": "999", "page": "8"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="width" value="999"')
        self.assertContains(response, 'id="technical-width"')
        self.assertContains(response, 'value="999" data-label="999" selected')
        self.assertNotIn("page=", response.context["active_filter_chips"][0]["remove_url"])
        self.assertNotContains(response, "function setupCustomSelects")
        self.assertContains(response, 'data-smart-select="off"')
        self.assertContains(response, 'id="clampFilterOptions"')
        self.assertContains(response, "Las opciones se ajustan entre sí")
