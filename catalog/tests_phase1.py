from decimal import Decimal
from io import BytesIO

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from catalog.models import (
    Product,
    ProductDuplicateReview,
    ProductSupplier,
    Supplier,
    SupplierCostHistory,
)
from catalog.services.duplicate_detection import refresh_duplicate_reviews, review_duplicate
from catalog.services.product_importer import ProductImporter
from catalog.services.product_suppliers import (
    set_preferred_supplier_preserving_terms,
    upsert_product_supplier_offer,
)
from core.services.company_context import get_default_company


class ProductSupplierPhase1Tests(TestCase):
    def setUp(self):
        self.supplier = Supplier.objects.create(name="Proveedor Uno")
        self.product = Product.objects.create(
            sku="PHASE1-001",
            name="Producto fase uno",
            price=Decimal("25.00"),
            cost=Decimal("10.00"),
        )

    def test_cost_history_is_created_only_for_real_changes(self):
        offer, baseline = upsert_product_supplier_offer(
            product=self.product,
            supplier=self.supplier,
            current_cost=Decimal("10.00"),
            supplier_code="EXT 001",
            source="test",
            is_preferred=True,
        )
        self.assertIsNotNone(baseline)
        self.assertIsNone(baseline.previous_cost)

        offer, unchanged = upsert_product_supplier_offer(
            product=self.product,
            supplier=self.supplier,
            current_cost=Decimal("10.00"),
            supplier_code="EXT 001",
            source="test",
            is_preferred=True,
        )
        self.assertIsNone(unchanged)

        offer, changed = upsert_product_supplier_offer(
            product=self.product,
            supplier=self.supplier,
            current_cost=Decimal("12.50"),
            supplier_code="EXT 001",
            source="test",
            is_preferred=True,
        )
        self.assertEqual(changed.previous_cost, Decimal("10.00"))
        self.assertEqual(changed.new_cost, Decimal("12.50"))
        self.assertEqual(SupplierCostHistory.objects.filter(product_supplier=offer).count(), 2)
        self.product.refresh_from_db()
        self.assertEqual(self.product.cost, Decimal("12.50"))
        self.assertEqual(self.product.supplier_ref, self.supplier)

    def test_switching_preferred_supplier_preserves_both_offers(self):
        other = Supplier.objects.create(name="Proveedor Dos")
        first, _history = upsert_product_supplier_offer(
            product=self.product,
            supplier=self.supplier,
            current_cost="10",
            is_preferred=True,
        )
        second, _history = upsert_product_supplier_offer(
            product=self.product,
            supplier=other,
            current_cost="9.50",
            is_preferred=True,
        )
        first.refresh_from_db()
        self.assertFalse(first.is_preferred)
        self.assertTrue(second.is_preferred)
        self.assertEqual(self.product.supplier_offers.count(), 2)
        self.product.refresh_from_db()
        self.assertEqual(self.product.supplier_ref, other)
        self.assertEqual(self.product.cost, Decimal("9.50"))

    def test_promoting_existing_offer_preserves_commercial_terms(self):
        other = Supplier.objects.create(name="Proveedor Con Condiciones")
        offer, _history = upsert_product_supplier_offer(
            product=self.product,
            supplier=other,
            current_cost="10",
            currency="USD",
            supplier_code="COND-1",
            discount_percentage="10",
            bonus_percentage="5",
            tax_percentage="21",
            minimum_purchase_quantity=6,
            lead_time_days=4,
            is_preferred=False,
        )

        promoted, _history = set_preferred_supplier_preserving_terms(
            product=self.product,
            supplier=other,
            current_cost="11",
            source="test",
        )

        self.assertEqual(promoted.pk, offer.pk)
        self.assertEqual(promoted.currency, "USD")
        self.assertEqual(promoted.supplier_code, "COND-1")
        self.assertEqual(promoted.discount_percentage, Decimal("10"))
        self.assertEqual(promoted.bonus_percentage, Decimal("5"))
        self.assertEqual(promoted.tax_percentage, Decimal("21"))
        self.assertEqual(promoted.minimum_purchase_quantity, 6)
        self.assertEqual(promoted.lead_time_days, 4)

    def test_supplier_code_collision_creates_review_without_overwriting(self):
        other_product = Product.objects.create(
            sku="PHASE1-002",
            name="Otro producto",
            price=Decimal("20.00"),
            cost=Decimal("8.00"),
        )
        upsert_product_supplier_offer(
            product=self.product,
            supplier=self.supplier,
            current_cost="10",
            supplier_code="ABC-99",
            is_preferred=True,
        )
        with self.assertRaisesMessage(ValidationError, "revision de duplicado"):
            upsert_product_supplier_offer(
                product=other_product,
                supplier=self.supplier,
                current_cost="8",
                supplier_code="abc-99",
                is_preferred=True,
            )
        self.assertFalse(ProductSupplier.objects.filter(product=other_product).exists())
        review = ProductDuplicateReview.objects.get(reason=ProductDuplicateReview.REASON_SUPPLIER_CODE)
        self.assertEqual(
            (review.primary_product_id, review.candidate_product_id),
            tuple(sorted((self.product.pk, other_product.pk))),
        )


class ProductDuplicatePhase1Tests(TestCase):
    def setUp(self):
        self.first = Product.objects.create(
            sku="DUP-001",
            name="Filtro de aceite premium",
            price=10,
        )
        self.second = Product.objects.create(
            sku="dup 001",
            name="Filtro de aceite premium",
            price=11,
        )

    def test_scan_only_creates_review_records_and_preserves_decisions(self):
        original = list(Product.objects.order_by("id").values("id", "sku", "name", "price"))
        dry_run = refresh_duplicate_reviews(apply=False)
        self.assertEqual(dry_run["candidates"], 2)
        self.assertEqual(ProductDuplicateReview.objects.count(), 0)

        applied = refresh_duplicate_reviews(apply=True)
        self.assertEqual(applied["created"], 2)
        review = ProductDuplicateReview.objects.get(reason=ProductDuplicateReview.REASON_SKU)
        review_duplicate(
            review,
            status=ProductDuplicateReview.STATUS_NOT_DUPLICATE,
            notes="Codigos validos diferentes.",
        )
        refreshed = refresh_duplicate_reviews(apply=True)
        self.assertEqual(refreshed["reviewed_preserved"], 1)
        review.refresh_from_db()
        self.assertEqual(review.status, ProductDuplicateReview.STATUS_NOT_DUPLICATE)
        self.assertEqual(
            list(Product.objects.order_by("id").values("id", "sku", "name", "price")),
            original,
        )


class ProductImporterPhase1Tests(TestCase):
    def test_importer_creates_offer_and_appends_cost_history(self):
        source = BytesIO()
        source.name = "products.xlsx"
        importer = ProductImporter(source)
        row = {
            "__row_number": 2,
            "sku": "IMP-P1-001",
            "nombre": "Producto importado",
            "proveedor": "Proveedor Importado",
            "codigo_proveedor": "PROV-001",
            "precio": "20",
            "costo": "10",
            "stock": "3",
        }
        result = importer.process_row(row, dry_run=False)
        self.assertTrue(result.success, result.errors)
        product = Product.objects.get(sku="IMP-P1-001")
        offer = ProductSupplier.objects.get(product=product)
        self.assertEqual(offer.supplier_code, "PROV-001")
        self.assertEqual(offer.cost_history.count(), 1)

        importer = ProductImporter(source)
        changed_row = {**row, "costo": "12", "__row_number": 3}
        result = importer.process_row(changed_row, dry_run=False)
        self.assertTrue(result.success, result.errors)
        offer.refresh_from_db()
        self.assertEqual(offer.current_cost, Decimal("12.0000"))
        self.assertEqual(offer.cost_history.count(), 2)
        self.assertEqual(offer.cost_history.first().source_row, 3)


class ProductDuplicateViewPhase1Tests(TestCase):
    def test_superuser_can_open_duplicate_review_queue(self):
        user = User.objects.create_superuser("phase1admin", "phase1@example.com", "secret")
        self.client.force_login(user)
        company = get_default_company()
        session = self.client.session
        session["active_company_id"] = company.pk
        session.save()
        response = self.client.get(reverse("admin_product_duplicate_reviews"))
        self.assertEqual(response.status_code, 200, getattr(response, "url", ""))
        self.assertContains(response, "Nunca fusiona")

    def test_staff_without_capability_is_redirected(self):
        user = User.objects.create_user("phase1staff", password="secret", is_staff=True)
        self.client.force_login(user)
        response = self.client.get(reverse("admin_product_duplicate_reviews"))
        self.assertEqual(response.status_code, 302)


class ProductSupplierViewPhase1Tests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            "phase1offers",
            "offers@example.com",
            "secret",
        )
        self.product = Product.objects.create(
            sku="OFFERS-001",
            name="Producto con ofertas",
            price=20,
            cost=10,
        )
        self.supplier = Supplier.objects.create(name="Proveedor Alternativo")
        self.client.force_login(self.user)
        session = self.client.session
        session["active_company_id"] = get_default_company().pk
        session.save()

    def _payload(self, cost):
        return {
            "supplier_id": self.supplier.pk,
            "supplier_code": "ALT-001",
            "supplier_description": "Descripcion externa",
            "current_cost": cost,
            "currency": "ARS",
            "discount_percentage": "0",
            "bonus_percentage": "0",
            "tax_percentage": "0",
            "minimum_purchase_quantity": "1",
            "lead_time_days": "2",
            "status": "active",
            "is_available": "on",
            "is_preferred": "on",
            "change_reason": "Lista de prueba",
        }

    def test_offer_screen_creates_relation_and_cost_differences(self):
        response = self.client.post(
            reverse("admin_product_supplier_offer_save", args=[self.product.pk]),
            self._payload("10"),
        )
        self.assertEqual(response.status_code, 302)
        offer = ProductSupplier.objects.get(product=self.product, supplier=self.supplier)
        baseline = offer.cost_history.get()
        self.assertEqual(baseline.difference_amount, Decimal("0"))
        self.assertIsNone(baseline.difference_percentage)

        response = self.client.post(
            reverse("admin_product_supplier_offer_save", args=[self.product.pk]),
            self._payload("12"),
        )
        self.assertEqual(response.status_code, 302)
        change = offer.cost_history.order_by("-created_at", "-id").first()
        self.assertEqual(change.difference_amount, Decimal("2"))
        self.assertEqual(change.difference_percentage, Decimal("20"))

        response = self.client.get(reverse("admin_product_edit", args=[self.product.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Proveedores y costos")
        self.assertContains(response, "ALT-001")


class ProductSupplierSearchPhase1Tests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            "phase1search",
            "search@example.com",
            "secret",
        )
        self.product = Product.objects.create(
            sku="INTERNAL-SEARCH-001",
            name="Producto buscable por proveedor",
            price=20,
            cost=10,
        )
        supplier = Supplier.objects.create(name="Proveedor Busqueda")
        upsert_product_supplier_offer(
            product=self.product,
            supplier=supplier,
            current_cost=10,
            supplier_code="EXTERNAL-UNIQUE-7788",
            supplier_description="Descripcion externa unica",
            is_preferred=True,
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["active_company_id"] = get_default_company().pk
        session.save()

    def test_supplier_code_is_searchable_without_replacing_internal_identity(self):
        product_list = self.client.get(
            reverse("admin_product_list"),
            {"q": "EXTERNAL-UNIQUE-7788"},
        )
        self.assertEqual(product_list.status_code, 200)
        self.assertContains(product_list, self.product.sku)

        global_search = self.client.get(
            reverse("admin_global_search"),
            {"q": "EXTERNAL-UNIQUE-7788", "type": "products"},
        )
        self.assertEqual(global_search.status_code, 200)
        self.assertContains(global_search, self.product.sku)

        api_response = self.client.get(
            reverse("api_v1:catalog_products"),
            {"q": "EXTERNAL-UNIQUE-7788"},
        )
        self.assertEqual(api_response.status_code, 200)
        self.assertContains(api_response, self.product.sku)

        self.product.refresh_from_db()
        self.assertEqual(self.product.sku, "INTERNAL-SEARCH-001")
        self.assertEqual(self.product.name, "Producto buscable por proveedor")
