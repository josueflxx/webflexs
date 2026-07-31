from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse

from catalog.models import (
    Brand,
    BrandAlias,
    BrandCatalogBatch,
    BrandCatalogRule,
    BrandRubro,
    BrandRubroProductOrder,
    BrandSubrubro,
    BrandSubrubroProductOrder,
    Category,
    Product,
)
from catalog.services.brand_cataloging import (
    BrandSuggestionEngine,
    assign_products_to_brand_catalog,
    brand_quality_metrics,
    remove_products_from_brand_catalog,
    undo_brand_catalog_batch,
)
from core.services.company_context import get_default_company


class BrandCatalogClient(Client):
    def login(self, **credentials):
        authenticated = super().login(**credentials)
        if authenticated:
            company = get_default_company()
            if company:
                session = self.session
                session["active_company_id"] = company.pk
                session.save()
        return authenticated


class BrandCatalogingServiceTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name="Bujes")
        self.brand = Brand.objects.create(name="Volkswagen")
        self.rubro = BrandRubro.objects.create(brand=self.brand, name="Bujes")
        self.subrubro = BrandSubrubro.objects.create(
            brand_rubro=self.rubro,
            name="Bujes armados",
        )
        self.product = Product.objects.create(
            sku="VW-001",
            name="Buje delantero para Volks Wagen",
            supplier="Proveedor Norte",
            price=Decimal("100"),
            category=self.category,
            is_active=True,
        )

    def test_alias_is_normalized_and_unique(self):
        alias = BrandAlias.objects.create(brand=self.brand, value="Vólks-Wagen")
        self.assertEqual(alias.normalized_value, "VOLKS WAGEN")
        with self.assertRaises(ValidationError):
            BrandAlias.objects.create(brand=self.brand, value="Volks Wagen")

    def test_engine_combines_alias_and_rule_suggestions(self):
        BrandAlias.objects.create(brand=self.brand, value="Volks Wagen")
        BrandCatalogRule.objects.create(
            brand=self.brand,
            brand_rubro=self.rubro,
            brand_subrubro=self.subrubro,
            source_field=BrandCatalogRule.FIELD_SKU,
            match_mode=BrandCatalogRule.MATCH_PREFIX,
            pattern="VW",
            confidence=96,
        )

        suggestions = BrandSuggestionEngine().suggest(self.product)

        self.assertEqual(suggestions[0]["brand"], self.brand)
        self.assertEqual(suggestions[0]["rubro"], self.rubro)
        self.assertEqual(suggestions[0]["subrubro"], self.subrubro)
        self.assertEqual(suggestions[0]["confidence"], 96)

    def test_helper_category_only_refines_an_already_recognized_brand(self):
        self.subrubro.helper_categories.add(self.category)
        BrandAlias.objects.create(brand=self.brand, value="Volks Wagen")
        unrelated_brand = Brand.objects.create(name="Agrale")
        unrelated_rubro = BrandRubro.objects.create(brand=unrelated_brand, name="Bujes")
        unrelated_subrubro = BrandSubrubro.objects.create(
            brand_rubro=unrelated_rubro,
            name="Bujes Agrale",
        )
        unrelated_subrubro.helper_categories.add(self.category)

        suggestions = BrandSuggestionEngine().suggest(self.product)

        self.assertEqual(suggestions[0]["brand"], self.brand)
        self.assertEqual(suggestions[0]["subrubro"], self.subrubro)
        self.assertFalse(any(item["brand"] == unrelated_brand for item in suggestions))

    def test_assignment_is_reversible_and_preserves_previous_rows(self):
        existing = BrandRubroProductOrder.objects.create(
            brand_rubro=self.rubro,
            product=self.product,
            sort_order=10,
        )

        batch = assign_products_to_brand_catalog(
            product_ids=[self.product.pk],
            brand=self.brand,
            rubro=self.rubro,
            subrubro=self.subrubro,
            observation="Clasificacion de prueba",
        )

        self.assertEqual(batch.created_rubro_row_ids, [])
        self.assertEqual(len(batch.created_subrubro_row_ids), 1)
        self.assertTrue(
            BrandSubrubroProductOrder.objects.filter(
                brand_subrubro=self.subrubro,
                product=self.product,
            ).exists()
        )

        undo_brand_catalog_batch(batch)

        self.assertTrue(BrandRubroProductOrder.objects.filter(pk=existing.pk).exists())
        self.assertFalse(
            BrandSubrubroProductOrder.objects.filter(
                brand_subrubro=self.subrubro,
                product=self.product,
            ).exists()
        )
        batch.refresh_from_db()
        self.assertEqual(batch.status, BrandCatalogBatch.STATUS_UNDONE)

    def test_observation_and_consistent_destination_are_required(self):
        other_brand = Brand.objects.create(name="Ford")
        other_rubro = BrandRubro.objects.create(brand=other_brand, name="Bujes")

        with self.assertRaisesMessage(ValueError, "observacion"):
            assign_products_to_brand_catalog(
                product_ids=[self.product.pk],
                brand=self.brand,
                rubro=self.rubro,
                observation="",
            )
        with self.assertRaisesMessage(ValueError, "no pertenece"):
            assign_products_to_brand_catalog(
                product_ids=[self.product.pk],
                brand=self.brand,
                rubro=other_rubro,
                observation="Destino inconsistente",
            )

    def test_quality_metrics_detect_coverage_and_ambiguity(self):
        second_brand = Brand.objects.create(name="Ford")
        second_rubro = BrandRubro.objects.create(brand=second_brand, name="Bujes")
        BrandRubroProductOrder.objects.create(
            brand_rubro=self.rubro,
            product=self.product,
            sort_order=10,
        )
        BrandRubroProductOrder.objects.create(
            brand_rubro=second_rubro,
            product=self.product,
            sort_order=10,
        )

        metrics = brand_quality_metrics()

        self.assertEqual(metrics["total"], 1)
        self.assertEqual(metrics["assigned"], 1)
        self.assertEqual(metrics["unassigned"], 0)
        self.assertEqual(metrics["ambiguous"], 1)

    def test_move_assignment_and_undo_restore_the_previous_brand(self):
        previous_brand = Brand.objects.create(name="Marca anterior")
        previous_rubro = BrandRubro.objects.create(
            brand=previous_brand,
            name="Bujes",
        )
        previous_subrubro = BrandSubrubro.objects.create(
            brand_rubro=previous_rubro,
            name="Bujes armados",
        )
        BrandRubroProductOrder.objects.create(
            brand_rubro=previous_rubro,
            product=self.product,
            sort_order=30,
        )
        BrandSubrubroProductOrder.objects.create(
            brand_subrubro=previous_subrubro,
            product=self.product,
            sort_order=40,
        )

        batch = assign_products_to_brand_catalog(
            product_ids=[self.product.pk],
            brand=self.brand,
            rubro=self.rubro,
            subrubro=self.subrubro,
            observation="Mover desde marca anterior",
            mode="move",
        )

        self.assertEqual(batch.operation, BrandCatalogBatch.OPERATION_MOVE)
        self.assertFalse(
            BrandRubroProductOrder.objects.filter(
                brand_rubro=previous_rubro,
                product=self.product,
            ).exists()
        )
        self.assertTrue(
            BrandSubrubroProductOrder.objects.filter(
                brand_subrubro=self.subrubro,
                product=self.product,
            ).exists()
        )

        undo_brand_catalog_batch(batch)

        self.assertFalse(
            BrandRubroProductOrder.objects.filter(
                brand_rubro=self.rubro,
                product=self.product,
            ).exists()
        )
        self.assertTrue(
            BrandRubroProductOrder.objects.filter(
                brand_rubro=previous_rubro,
                product=self.product,
                sort_order=30,
            ).exists()
        )
        self.assertTrue(
            BrandSubrubroProductOrder.objects.filter(
                brand_subrubro=previous_subrubro,
                product=self.product,
                sort_order=40,
            ).exists()
        )

    def test_remove_batch_is_reversible(self):
        BrandRubroProductOrder.objects.create(
            brand_rubro=self.rubro,
            product=self.product,
            sort_order=10,
        )
        BrandSubrubroProductOrder.objects.create(
            brand_subrubro=self.subrubro,
            product=self.product,
            sort_order=20,
        )

        batch = remove_products_from_brand_catalog(
            product_ids=[self.product.pk],
            brand=self.brand,
            rubro=self.rubro,
            subrubro=self.subrubro,
            observation="Producto fuera del subrubro",
        )

        self.assertEqual(batch.operation, BrandCatalogBatch.OPERATION_REMOVE)
        self.assertFalse(
            BrandSubrubroProductOrder.objects.filter(
                brand_subrubro=self.subrubro,
                product=self.product,
            ).exists()
        )
        self.assertTrue(
            BrandRubroProductOrder.objects.filter(
                brand_rubro=self.rubro,
                product=self.product,
            ).exists()
        )

        undo_brand_catalog_batch(batch)

        self.assertTrue(
            BrandSubrubroProductOrder.objects.filter(
                brand_subrubro=self.subrubro,
                product=self.product,
                sort_order=20,
            ).exists()
        )


class BrandCatalogingViewTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name="Suspension")
        self.brand = Brand.objects.create(name="Ford")
        self.rubro = BrandRubro.objects.create(brand=self.brand, name="Bujes")
        self.subrubro = BrandSubrubro.objects.create(
            brand_rubro=self.rubro,
            name="Bujes armados",
        )
        self.pending = Product.objects.create(
            sku="PENDING-1",
            name="Buje Ford pendiente",
            price=Decimal("100"),
            category=self.category,
            is_active=True,
        )
        self.assigned = Product.objects.create(
            sku="ASSIGNED-1",
            name="Buje Ford catalogado",
            price=Decimal("100"),
            category=self.category,
            is_active=True,
        )
        BrandRubroProductOrder.objects.create(
            brand_rubro=self.rubro,
            product=self.assigned,
            sort_order=10,
        )
        self.user = User.objects.create_superuser(
            "josueflexs",
            "admin@test.com",
            "adminpass",
        )
        self.client = BrandCatalogClient()
        self.client.login(username="josueflexs", password="adminpass")

    def test_inbox_only_shows_uncataloged_products(self):
        response = self.client.get(reverse("admin_brand_catalog_inbox"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.pending.sku)
        self.assertNotContains(response, self.assigned.sku)
        self.assertContains(response, "Productos pendientes de clasificar por marca")

    def test_post_assigns_product_and_creates_audited_batch(self):
        response = self.client.post(
            reverse("admin_brand_catalog_assign"),
            {
                "product_ids": [self.pending.pk],
                "brand_id": self.brand.pk,
                "rubro_id": self.rubro.pk,
                "subrubro_id": self.subrubro.pk,
                "observation": "Revision manual de prueba",
                "used_suggestion": "1",
            },
        )

        self.assertRedirects(response, reverse("admin_brand_catalog_inbox"))
        batch = BrandCatalogBatch.objects.get()
        self.assertEqual(batch.operation, BrandCatalogBatch.OPERATION_RULE)
        self.assertEqual(batch.created_by, self.user)
        self.assertTrue(
            BrandSubrubroProductOrder.objects.filter(
                brand_subrubro=self.subrubro,
                product=self.pending,
            ).exists()
        )

    def test_post_without_observation_does_not_change_catalog(self):
        response = self.client.post(
            reverse("admin_brand_catalog_assign"),
            {
                "product_ids": [self.pending.pk],
                "brand_id": self.brand.pk,
                "rubro_id": self.rubro.pk,
                "observation": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(BrandCatalogBatch.objects.exists())
        self.assertFalse(
            BrandRubroProductOrder.objects.filter(
                brand_rubro=self.rubro,
                product=self.pending,
            ).exists()
        )

    def test_export_generates_review_workbook(self):
        response = self.client.get(reverse("admin_brand_catalog_export"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertIn("catalogacion_marcas_pendiente", response["Content-Disposition"])
        self.assertGreater(len(response.content), 1000)
