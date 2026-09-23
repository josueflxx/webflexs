from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from catalog import views
from catalog.models import Category, Product
from catalog import tests_pricing
from core.services import catalog_excel_jobs as jobs


class CategoryTreeCacheTests(TestCase):
    def setUp(self):
        cache.clear()
        self.root = Category.objects.create(name="Root", slug="tree-root")
        self.child = Category.objects.create(name="Child", slug="tree-child", parent=self.root)
        self.empty = Category.objects.create(name="Empty", slug="tree-empty")
        self.product = Product.objects.create(sku="TREE-1", name="Tree", category=self.child, price=1)
        self.product.categories.add(self.child)

    def ids(self):
        return {row["category"].pk for row in views.get_cached_category_tree_rows()}

    def test_warm_tree_reuses_rows_with_three_simple_queries(self):
        self.assertEqual(self.ids(), {self.root.pk, self.child.pk})
        with patch.object(views, "build_category_tree_rows", side_effect=AssertionError("rebuilt")):
            with self.assertNumQueries(3):
                self.assertEqual(self.ids(), {self.root.pk, self.child.pk})

    def test_bulk_product_visibility_updates_are_immediate(self):
        self.ids()
        Product.objects.filter(pk=self.product.pk).update(is_active=False)
        self.assertEqual(self.ids(), set())
        Product.objects.filter(pk=self.product.pk).update(is_active=True, is_sellable=False)
        self.assertEqual(self.ids(), set())
        Product.objects.filter(pk=self.product.pk).update(is_sellable=True)
        self.assertEqual(self.ids(), {self.root.pk, self.child.pk})

    def test_bulk_through_changes_and_primary_reassignment_are_immediate(self):
        self.ids()
        Product.objects.filter(pk=self.product.pk).update(category=None)
        Product.categories.through.objects.filter(product_id=self.product.pk).delete()
        self.assertEqual(self.ids(), set())
        Product.categories.through.objects.bulk_create([
            Product.categories.through(product_id=self.product.pk, category_id=self.empty.pk),
        ])
        self.assertEqual(self.ids(), {self.empty.pk})

    def test_bulk_category_rename_move_hide_and_delete_invalidate(self):
        self.ids()
        Category.objects.filter(pk=self.child.pk).update(public_name="Renamed", parent=self.empty)
        rows = views.get_cached_category_tree_rows()
        self.assertEqual({row["category"].pk for row in rows}, {self.empty.pk, self.child.pk})
        self.assertEqual(rows[-1]["full_path"], "Empty > Renamed")
        Category.objects.filter(pk=self.empty.pk).update(visible_in_catalog=False)
        self.assertEqual(self.ids(), set())
        self.product.delete()
        self.assertEqual(self.ids(), set())

    def test_hidden_primary_and_public_secondary_and_hidden_ancestor(self):
        Category.objects.filter(pk=self.root.pk).update(visible_in_catalog=False)
        self.product.categories.add(self.empty)
        self.assertEqual(self.ids(), {self.empty.pk})


class AsyncCatalogExcelTests(TestCase):
    def setUp(self):
        tests_pricing.CurrentCatalogPricingTests.setUp(self)
        self.url = reverse("catalog_client_excel_download")

    def test_request_only_queues_once_and_status_is_private(self):
        with patch("core.services.catalog_excel_exporter.build_catalog_workbook", side_effect=AssertionError("HTTP generated Excel")):
            first = self.client.get(self.url)
            self.assertEqual(first.status_code, 202)
            self.assertContains(first, "segundo plano", status_code=202)
            poll = self.client.get(self.url, {"status": "1"})
        self.assertEqual(poll.json(), {"status": "queued"})
        self.assertIn("no-store", poll["Cache-Control"])
        self.export_publisher.assert_called_once()

    def test_worker_publishes_valid_file_and_download_rechecks_permissions(self):
        self.client.get(self.url, {"status": "1"})
        spec, token = self.export_publisher.call_args.args
        jobs.generate_export(spec, token)
        self.assertTrue(jobs.export_ready(spec))
        self.assertEqual(self.client.get(self.url, {"status": "1"}).json(), {"status": "ready"})
        response = self.client.get(self.url)
        self.assertTrue(response.streaming)
        self.assertEqual(tests_pricing.CurrentCatalogPricingTests.excel_price(self, response), Decimal("8773.30"))
        self.link.is_active = False
        self.link.save(update_fields=["is_active"])
        self.assertNotEqual(self.client.get(self.url).status_code, 200)
        self.assertNotEqual(self.client.get(self.url, {"status": "1"}).status_code, 200)

    def test_discount_and_source_changes_cannot_reuse_another_file(self):
        self.client.get(self.url, {"status": "1"})
        spec, token = self.export_publisher.call_args.args
        jobs.generate_export(spec, token)
        changed_discount = dict(spec, discount="25.00")
        self.assertNotEqual(jobs.export_path(spec), jobs.export_path(changed_discount))
        self.product.price = 9000
        self.product.save(update_fields=["price", "updated_at"])
        self.assertEqual(self.client.get(self.url, {"status": "1"}).json()["status"], "queued")
        self.assertEqual(self.export_publisher.call_count, 2)

    def test_worker_does_not_publish_if_source_changes_during_generation(self):
        from core.services.catalog_excel_exporter import build_catalog_workbook

        self.client.get(self.url, {"status": "1"})
        spec, token = self.export_publisher.call_args.args
        def changed_source(*args, **kwargs):
            workbook, stats = build_catalog_workbook(*args, **kwargs)
            self.product.price = 9900
            self.product.save(update_fields=["price", "updated_at"])
            return workbook, stats
        with patch("core.services.catalog_excel_exporter.build_catalog_workbook", side_effect=changed_source):
            jobs.generate_export(spec, token)
        self.assertFalse(jobs.export_ready(spec))

    def test_queue_failure_never_falls_back_to_http_generation(self):
        self.export_publisher.side_effect = RuntimeError("broker unavailable")
        with patch("core.services.catalog_excel_exporter.build_catalog_workbook", side_effect=AssertionError("inline")):
            response = self.client.get(self.url, {"status": "1"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "failed")
        self.assertNotIn("broker unavailable", response.content.decode())

    def test_duplicate_worker_and_failed_job_are_safe(self):
        self.client.get(self.url, {"status": "1"})
        spec, token = self.export_publisher.call_args.args
        with patch("core.services.catalog_excel_exporter.build_catalog_workbook", side_effect=RuntimeError("test failure")):
            with self.assertRaises(RuntimeError):
                jobs.generate_export(spec, token)
        self.assertFalse(jobs.export_ready(spec))
        self.assertEqual(self.client.get(self.url, {"status": "1"}).status_code, 503)
        cache.set(f"catalog_excel_job_v1:{jobs.export_key(spec)}:running:{token}", True)
        with patch("core.services.catalog_excel_exporter.build_catalog_workbook", side_effect=AssertionError("duplicate")):
            jobs.generate_export(spec, token)
