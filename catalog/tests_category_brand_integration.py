from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from catalog.models import (
    Brand,
    BrandCatalogBatch,
    BrandRubro,
    BrandRubroProductOrder,
    Category,
    CategoryBrandMapping,
    Product,
)
from catalog.services.category_brand_integration import (
    detect_category_brand_candidates,
    products_for_category,
)
from core.services.company_context import get_default_company


class CategoryBrandIntegrationServiceTests(TestCase):
    def setUp(self):
        self.family = Category.objects.create(name="Bujes")
        self.ford_category = Category.objects.create(name="FORD", parent=self.family)
        self.bedford_category = Category.objects.create(name="BEDFORD", parent=self.family)
        self.brand = Brand.objects.create(name="Ford")
        self.rubro = BrandRubro.objects.create(brand=self.brand, name="Bujes")

    def test_brand_matching_uses_complete_tokens(self):
        candidates = detect_category_brand_candidates()
        by_category = {candidate.category.pk: candidate for candidate in candidates}

        self.assertIn(self.ford_category.pk, by_category)
        self.assertEqual(by_category[self.ford_category.pk].suggested_brand, self.brand)
        self.assertNotIn(self.bedford_category.pk, by_category)

    def test_category_products_include_primary_and_additional_assignments(self):
        primary = Product.objects.create(
            sku="FORD-PRIMARY",
            name="Buje Ford principal",
            price=Decimal("10.00"),
            category=self.ford_category,
        )
        additional = Product.objects.create(
            sku="FORD-ADDITIONAL",
            name="Buje Ford adicional",
            price=Decimal("12.00"),
        )
        additional.categories.add(self.ford_category)

        ids = set(products_for_category(self.ford_category).values_list("pk", flat=True))

        self.assertEqual(ids, {primary.pk, additional.pk})

    def test_ambiguous_brand_candidate_does_not_crash(self):
        Brand.objects.create(name="Ford Trucks")
        ambiguous_category = Category.objects.create(
            name="Ford Trucks",
            parent=self.family,
        )

        candidates = detect_category_brand_candidates()
        candidate = next(
            item for item in candidates if item.category.pk == ambiguous_category.pk
        )

        self.assertTrue(candidate.has_conflict)
        self.assertIsNone(candidate.suggested_brand)
        self.assertIsNone(candidate.suggested_rubro)


class CategoryBrandIntegrationViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="josueflexs",
            email="taxonomy@example.com",
            password="test-pass-123",
        )
        self.client.force_login(self.user)
        company = get_default_company()
        session = self.client.session
        session["active_company_id"] = company.pk
        session.save()
        self.family = Category.objects.create(name="Armado")
        self.source = Category.objects.create(name="FORD", parent=self.family)
        self.brand = Brand.objects.create(name="Ford")
        self.rubro = BrandRubro.objects.create(brand=self.brand, name="Armado")
        self.product = Product.objects.create(
            sku="ARM-FORD-01",
            name="Buje armado Ford",
            price=Decimal("100.00"),
            category=self.source,
        )

    def test_integration_and_category_pages_render_the_shared_classification(self):
        response = self.client.get(reverse("admin_brand_category_integration"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Integracion de categorias y marcas")
        self.assertContains(response, "Armado")
        self.assertContains(response, "FORD")

        response = self.client.get(reverse("admin_category_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sugerida: Ford")

    def test_confirm_and_apply_preserves_category_and_creates_reversible_batch(self):
        response = self.client.post(
            reverse("admin_brand_category_mapping_save"),
            {
                "source_category": self.source.pk,
                "canonical_category": self.family.pk,
                "brand": self.brand.pk,
                "brand_rubro": self.rubro.pk,
                "brand_subrubro": "",
                "include_descendants": "on",
                "observation": "Migracion controlada de la categoria Ford",
                "action": "save_apply",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            CategoryBrandMapping.objects.filter(source_category=self.source).exists(),
            f"Redirect inesperado: {getattr(response, 'url', '')}",
        )
        mapping = CategoryBrandMapping.objects.get(source_category=self.source)
        self.assertIsNotNone(mapping.last_batch_id)
        self.assertEqual(mapping.last_batch.status, BrandCatalogBatch.STATUS_APPLIED)
        self.assertTrue(
            BrandRubroProductOrder.objects.filter(
                brand_rubro=self.rubro,
                product=self.product,
            ).exists()
        )
        self.product.refresh_from_db()
        self.assertEqual(self.product.category, self.source)

    def test_observation_is_optional_when_confirming_and_applying(self):
        page = self.client.get(reverse("admin_brand_category_integration"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Observacion (opcional)")
        self.assertNotContains(
            page,
            'name="observation" maxlength="300" required',
        )

        response = self.client.post(
            reverse("admin_brand_category_mapping_save"),
            {
                "source_category": self.source.pk,
                "canonical_category": self.family.pk,
                "brand": self.brand.pk,
                "brand_rubro": self.rubro.pk,
                "brand_subrubro": "",
                "include_descendants": "on",
                "observation": "",
                "action": "save_apply",
            },
        )

        self.assertEqual(response.status_code, 302)
        mapping = CategoryBrandMapping.objects.get(source_category=self.source)
        self.assertEqual(mapping.observation, "")
        self.assertIsNotNone(mapping.last_batch_id)
        self.assertEqual(mapping.last_batch.observation, "")
