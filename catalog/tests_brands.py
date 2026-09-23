from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from decimal import Decimal
from html.parser import HTMLParser
import json

from catalog.models import (
    Brand,
    BrandCatalogBatch,
    BrandRubro,
    BrandRubroProductOrder,
    BrandSubrubro,
    BrandSubrubroProductOrder,
    Category,
    Product,
)
from core.services.company_context import get_default_company


class CompanyScopedBrandClient(Client):
    """Select the default company for authenticated admin brand fixtures."""

    def login(self, **credentials):
        authenticated = super().login(**credentials)
        if authenticated:
            company = get_default_company()
            if company:
                session = self.session
                session["active_company_id"] = company.pk
                session.save()
        return authenticated


class BrandModelTestCase(TestCase):
    """Test case for Brand catalog models and slug generation."""

    def test_slug_auto_generation(self):
        brand = Brand.objects.create(name="Ford Motor Co")
        self.assertEqual(brand.slug, "ford-motor-co")

        # Test clash resolution in sub-levels (which don't have unique name constraints globally)
        rubro = BrandRubro.objects.create(brand=brand, name="Bujes")
        self.assertEqual(rubro.slug, "bujes")

        subrubro = BrandSubrubro.objects.create(brand_rubro=rubro, name="Bujes Armados")
        self.assertEqual(subrubro.slug, "bujes-armados")


class BrandSyncTestCase(TestCase):
    """Test case for BrandSubrubro automatic product synchronization."""

    def setUp(self):
        self.category = Category.objects.create(name="Bujes Armados General")
        
        # Matching product
        self.prod_match = Product.objects.create(
            sku="BUJ-FRD-01",
            name="Buje de goma Ford Escort",
            price=Decimal("150.00"),
            category=self.category,
            is_active=True
        )
        
        # Non-matching product (different brand)
        self.prod_other = Product.objects.create(
            sku="BUJ-CHV-01",
            name="Buje Chevrolet Corsa",
            price=Decimal("150.00"),
            category=self.category,
            is_active=True
        )

        self.brand = Brand.objects.create(name="Ford")
        self.rubro = BrandRubro.objects.create(brand=self.brand, name="Bujes")
        self.subrubro = BrandSubrubro.objects.create(brand_rubro=self.rubro, name="Bujes Armados")
        self.subrubro.helper_categories.add(self.category)

    def test_autosync_links_only_matching_products(self):
        self.assertEqual(self.subrubro.products.count(), 0)
        
        client = CompanyScopedBrandClient()
        # Create a superuser named 'josueflexs' to pass the superuser_required_for_modifications decorator check
        user = User.objects.create_superuser('josueflexs', 'admin@test.com', 'adminpass')
        client.login(username='josueflexs', password='adminpass')
        
        url = reverse('admin_brand_subrubro_sync', args=[self.subrubro.pk])
        response = client.post(url)
        
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['added_count'], 1)

        linked_products = list(self.subrubro.products.all())
        self.assertEqual(len(linked_products), 1)
        self.assertEqual(linked_products[0].id, self.prod_match.id)


class BrandViewsTestCase(TestCase):
    """Test case for public brand catalog views."""

    def setUp(self):
        self.brand = Brand.objects.create(name="Peugeot")
        self.rubro = BrandRubro.objects.create(brand=self.brand, name="Opticas")
        self.subrubro = BrandSubrubro.objects.create(brand_rubro=self.rubro, name="Opticas Delanteras")
        self.category = Category.objects.create(name="Opticas publicadas")
        self.product = Product.objects.create(
            sku="OPT-PGT-01",
            name="Optica Peugeot 208",
            price=Decimal("350.00"),
            category=self.category,
            is_active=True
        )
        BrandSubrubroProductOrder.objects.create(
            brand_subrubro=self.subrubro,
            product=self.product,
            sort_order=10
        )
        self.client = CompanyScopedBrandClient()

    def test_brands_list_page(self):
        response = self.client.get(reverse('brands_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Peugeot")

    def test_brand_detail_landing_page(self):
        """Without parameters, the detail page should display the Rubros grid."""
        response = self.client.get(reverse('brand_detail', args=[self.brand.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Peugeot")
        self.assertContains(response, "Opticas")
        # Subrubros and products shouldn't be loaded on the landing page
        self.assertNotContains(response, "Opticas Delanteras")
        self.assertNotContains(response, "OPT-PGT-01")

    def test_brand_detail_page_with_rubro(self):
        """Specifying rubro should load subrubros and products (via auto-select of first subrubro)."""
        response = self.client.get(reverse('brand_detail', args=[self.brand.slug]), {'rubro': self.rubro.slug})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Opticas")
        self.assertContains(response, "Opticas Delanteras")
        self.assertContains(response, "OPT-PGT-01")

    def test_brand_catalog_uses_scoped_styles_and_compact_list(self):
        response = self.client.get(reverse("brand_detail", args=[self.brand.slug]), {"rubro": self.rubro.slug})
        self.assertContains(response, "core/css/brand_catalog.css")
        self.assertNotContains(response, "core/css/catalog.css")
        self.assertContains(response, 'id="brandProducts" data-view="list"')
        self.assertContains(response, 'aria-label="Presentación de productos"')
        self.assertContains(response, 'data-bc-view="grid"')
        self.assertContains(response, "Buscar en los productos mostrados")
        self.assertContains(response, "Sin imagen")
        self.assertContains(response, reverse("product_detail", args=[self.product.sku]))

    def test_brand_catalog_marks_the_displayed_subrubro(self):
        second = BrandSubrubro.objects.create(brand_rubro=self.rubro, name="Opticas Traseras")
        product = Product.objects.create(sku="OPT-PGT-02", name="Optica trasera Peugeot", price=400, category=self.category, is_active=True)
        second.products.add(product)
        response = self.client.get(reverse("brand_detail", args=[self.brand.slug]), {"subrubro": second.slug})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'href="?subrubro=opticas-traseras" aria-current="true"')
        self.assertContains(response, product.sku)
        self.assertNotContains(response, self.product.sku)

    def test_brand_catalog_private_prices_and_guest_actions_remain_hidden(self):
        from core.models import SiteSettings
        site = SiteSettings.get_settings()
        site.show_public_prices = False
        site.save()
        response = self.client.get(reverse("brand_detail", args=[self.brand.slug]), {"rubro": self.rubro.slug})
        self.assertContains(response, "Consultá el precio")
        self.assertNotContains(response, "350,00")
        self.assertNotContains(response, 'data-bc-action="cart"')
        self.assertNotContains(response, 'data-bc-action="favorite"')

    def test_brand_catalog_authenticated_actions_include_csrf_and_stock_guard(self):
        User.objects.create_superuser("josueflexs", "catalog@example.test", "test-only")
        self.client.login(username="josueflexs", password="test-only")
        response = self.client.get(reverse("brand_detail", args=[self.brand.slug]), {"rubro": self.rubro.slug})
        self.assertContains(response, 'data-bc-action="cart"')
        self.assertNotContains(response, 'data-bc-action="cart" disabled')
        self.assertContains(response, 'Por encargo')
        self.assertContains(response, 'data-bc-action="favorite"')
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertNotContains(response, 'onclick="addToCart')

    def test_brand_catalog_hides_products_that_cannot_be_added_to_cart(self):
        hidden = Product.objects.create(
            sku="OPT-PGT-PRIVATE",
            name="Optica interna sin publicar",
            price=220,
            is_active=True,
        )
        self.subrubro.products.add(hidden)
        response = self.client.get(reverse("brand_detail", args=[self.brand.slug]), {"rubro": self.rubro.slug})
        self.assertContains(response, self.product.sku)
        self.assertNotContains(response, hidden.sku)

    def test_brand_catalog_products_can_be_added_to_cart(self):
        available = Product.objects.create(
            sku="OPT-PGT-AVAILABLE",
            name="Optica Peugeot publicada con stock",
            price=275,
            category=self.category,
            stock=1,
            is_active=True,
        )
        self.subrubro.products.add(available)
        User.objects.create_superuser("josueflexs", "catalog@example.test", "test-only")
        self.client.login(username="josueflexs", password="test-only")
        response = self.client.post(
            reverse("add_to_cart"),
            data=json.dumps({"product_id": available.pk, "quantity": 1}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])


class BrandRubroAdminTestCase(TestCase):
    """Test case for BrandRubro product association, sorting, and synchronization in the admin panel."""

    def setUp(self):
        self.brand = Brand.objects.create(name="Renault")
        self.rubro = BrandRubro.objects.create(brand=self.brand, name="Frenos")
        self.category = Category.objects.create(name="Pastillas de Freno")
        
        self.product1 = Product.objects.create(
            sku="FRN-REN-01",
            name="Pastillas Renault Clio",
            price=Decimal("120.00"),
            category=self.category,
            is_active=True
        )
        self.product2 = Product.objects.create(
            sku="FRN-REN-02",
            name="Pastillas Renault Megane",
            price=Decimal("180.00"),
            category=self.category,
            is_active=True
        )
        
        self.client = CompanyScopedBrandClient()
        self.user = User.objects.create_superuser('josueflexs', 'admin@test.com', 'adminpass')
        self.client.login(username='josueflexs', password='adminpass')

    def test_add_product_to_rubro(self):
        self.assertEqual(self.rubro.products.count(), 0)
        
        url = reverse('admin_brand_rubro_add_product', args=[self.rubro.pk])
        response = self.client.post(url, {'product_id': self.product1.id}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.rubro.products.count(), 1)
        self.assertTrue(self.rubro.products.filter(id=self.product1.id).exists())

    def test_remove_product_from_rubro(self):
        # First add it
        self.rubro.products.add(self.product1)
        self.assertEqual(self.rubro.products.count(), 1)
        
        url = reverse('admin_brand_rubro_remove_product', args=[self.rubro.pk])
        response = self.client.post(url, {'product_id': self.product1.id}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.rubro.products.count(), 0)

    def test_reorder_rubro_products(self):
        from catalog.models import BrandRubroProductOrder
        
        row1 = BrandRubroProductOrder.objects.create(brand_rubro=self.rubro, product=self.product1, sort_order=10)
        row2 = BrandRubroProductOrder.objects.create(brand_rubro=self.rubro, product=self.product2, sort_order=20)
        
        url = reverse('admin_brand_rubro_products_reorder', args=[self.rubro.pk])
        # Swap their positions
        import json
        payload = {"ordered_ids": [self.product2.id, self.product1.id]}
        response = self.client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        
        self.assertEqual(response.status_code, 200)
        row1.refresh_from_db()
        row2.refresh_from_db()
        self.assertEqual(row2.sort_order, 10)
        self.assertEqual(row1.sort_order, 20)

    def test_sync_rubro_products(self):
        # Create an active subrubro and assign our helper category
        subrubro = BrandSubrubro.objects.create(brand_rubro=self.rubro, name="Pastillas")
        subrubro.helper_categories.add(self.category)
        
        self.assertEqual(self.rubro.products.count(), 0)
        
        url = reverse('admin_brand_rubro_sync', args=[self.rubro.pk])
        response = self.client.post(url)
        
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['added_count'], 2)
        self.assertEqual(self.rubro.products.count(), 2)


class ProductGridBrandAssocTestCase(TestCase):
    """Test case for AJAX single and bulk brand associations in the grid editor."""

    def setUp(self):
        self.brand = Brand.objects.create(name="Ford_Grid")
        self.rubro = BrandRubro.objects.create(brand=self.brand, name="Accesorios")
        self.subrubro = BrandSubrubro.objects.create(brand_rubro=self.rubro, name="Alfombras")
        
        self.product = Product.objects.create(
            id=6203,
            sku="ACC-FRD-01",
            name="Alfombra de Goma Ford Fiesta",
            price=Decimal("80.00"),
            is_active=True
        )
        
        self.client = CompanyScopedBrandClient()
        self.user = User.objects.create_superuser('josueflexs', 'admin@test.com', 'adminpass')
        self.client.login(username='josueflexs', password='adminpass')

    def test_ajax_add_brand_association(self):
        self.assertEqual(self.rubro.products.count(), 0)
        self.assertEqual(self.subrubro.products.count(), 0)
        
        url = reverse('admin_product_grid_add_brand_association')
        import json
        payload = {
            "product_id": self.product.id,
            "product_sku": self.product.sku,
            "rubro_id": self.rubro.id,
            "subrubro_id": self.subrubro.id
        }
        response = self.client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json"
        )
        
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'success')
        self.assertEqual(data['product_sku'], self.product.sku)
        self.assertEqual(self.rubro.products.count(), 1)
        self.assertEqual(self.subrubro.products.count(), 1)

    def test_grid_renders_technical_ids_without_localization(self):
        response = self.client.get(
            reverse('admin_product_grid_editor'),
            {'f_sku': self.product.sku},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="row-6203"')
        self.assertContains(response, 'data-product-id="6203"')
        self.assertContains(response, 'openAddAssociationPopover(6203, event)')
        self.assertNotContains(response, 'openAddAssociationPopover(6.203, event)')

    def test_ajax_add_brand_association_rejects_stale_product_row(self):
        url = reverse('admin_product_grid_add_brand_association')
        import json
        payload = {
            "product_id": self.product.id,
            "product_sku": "SKU-QUE-YA-NO-CORRESPONDE",
            "rubro_id": self.rubro.id,
            "subrubro_id": self.subrubro.id,
        }

        response = self.client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['status'], 'error')
        self.assertEqual(self.rubro.products.count(), 0)
        self.assertEqual(self.subrubro.products.count(), 0)

    def test_ajax_remove_brand_association(self):
        # Associate first
        from catalog.models import BrandRubroProductOrder, BrandSubrubroProductOrder
        BrandRubroProductOrder.objects.create(brand_rubro=self.rubro, product=self.product, sort_order=10)
        BrandSubrubroProductOrder.objects.create(brand_subrubro=self.subrubro, product=self.product, sort_order=10)
        
        self.assertEqual(self.rubro.products.count(), 1)
        self.assertEqual(self.subrubro.products.count(), 1)
        
        # Remove subrubro association
        url = reverse('admin_product_grid_remove_brand_association')
        import json
        payload = {
            "product_id": self.product.id,
            "type": "subrubro",
            "id": self.subrubro.id
        }
        response = self.client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json"
        )
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.subrubro.products.count(), 0)
        # Parent rubro should still be linked
        self.assertEqual(self.rubro.products.count(), 1)

    def test_bulk_brand_association(self):
        self.assertEqual(self.rubro.products.count(), 0)
        
        url = reverse('admin_product_grid_bulk_update')
        import json
        payload = {
            "product_ids": [self.product.id],
            "action": "brand_association",
            "brand_id": self.brand.id,
            "rubro_id": self.rubro.id,
            "subrubro_id": self.subrubro.id
        }
        response = self.client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json"
        )
        
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'success')
        self.assertEqual(self.rubro.products.count(), 1)

    def test_product_list_bulk_brand_assignment_creates_reversible_batch(self):
        response = self.client.post(
            reverse("admin_product_bulk_brand"),
            {
                "product_ids": [self.product.pk],
                "product_ids_csv": str(self.product.pk),
                "brand_id": self.brand.pk,
                "rubro_id": self.rubro.pk,
                "subrubro_id": self.subrubro.pk,
                "mode": "add",
                "observation": "Asignacion desde listado de productos",
                "select_all_pages": "false",
            },
        )

        self.assertEqual(response.status_code, 302)
        batch = BrandCatalogBatch.objects.get()
        self.assertEqual(batch.product_ids, [self.product.pk])
        self.assertTrue(
            BrandSubrubroProductOrder.objects.filter(
                brand_subrubro=self.subrubro,
                product=self.product,
            ).exists()
        )


class BrandCategoryAssociationTestCase(TestCase):
    """Test case for manual category filtering and bulk category association in brand rubro/subrubro products views."""

    def setUp(self):
        self.brand = Brand.objects.create(name="Toyota")
        self.rubro = BrandRubro.objects.create(brand=self.brand, name="Suspension")
        self.subrubro = BrandSubrubro.objects.create(brand_rubro=self.rubro, name="Bujes")
        
        # Parent category
        self.parent_cat = Category.objects.create(name="Bujes Suspension")
        # Child category
        self.child_cat = Category.objects.create(name="Bujes Delanteros", parent=self.parent_cat)
        # Unrelated category
        self.other_cat = Category.objects.create(name="Opticas")
        
        self.product1 = Product.objects.create(
            sku="TOY-BUJ-01",
            name="Buje Toyota Corolla",
            price=Decimal("120.00"),
            category=self.child_cat,
            is_active=True
        )
        self.product2 = Product.objects.create(
            sku="TOY-BUJ-02",
            name="Buje Toyota Hilux",
            price=Decimal("120.00"),
            category=self.parent_cat,
            is_active=True
        )
        self.product3 = Product.objects.create(
            sku="TOY-OPT-01",
            name="Optica Hilux",
            price=Decimal("120.00"),
            category=self.other_cat,
            is_active=True
        )
        
        self.client = CompanyScopedBrandClient()
        self.user = User.objects.create_superuser('josueflexs', 'admin@toyota.com', 'adminpass')
        self.client.login(username='josueflexs', password='adminpass')

    def test_rubro_products_filter_by_category(self):
        # Fetching brand rubro products filtering by parent_cat (should return product1 and product2, not product3)
        url = reverse('admin_brand_rubro_products', args=[self.rubro.pk])
        response = self.client.get(url, {'category_id': self.parent_cat.pk, 'ajax': '1'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        
        # Extract product IDs from results
        result_ids = [res['id'] for res in data['results']]
        self.assertIn(self.product1.id, result_ids)
        self.assertIn(self.product2.id, result_ids)
        self.assertNotIn(self.product3.id, result_ids)

    def test_subrubro_products_filter_by_category(self):
        # Fetching brand subrubro products filtering by child_cat (should return product1 only)
        url = reverse('admin_brand_subrubro_products', args=[self.subrubro.pk])
        response = self.client.get(url, {'category_id': self.child_cat.pk, 'ajax': '1'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        
        result_ids = [res['id'] for res in data['results']]
        self.assertIn(self.product1.id, result_ids)
        self.assertNotIn(self.product2.id, result_ids)
        self.assertNotIn(self.product3.id, result_ids)

    def test_bulk_add_category_to_rubro(self):
        self.assertEqual(self.rubro.products.count(), 0)
        
        url = reverse('admin_brand_rubro_bulk_add_category', args=[self.rubro.pk])
        response = self.client.post(url, {'category_id': self.parent_cat.pk}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['added_count'], 2) # product1 and product2
        self.assertEqual(self.rubro.products.count(), 2)
        
        # Verify added_products structure
        self.assertIn('added_products', data)
        self.assertEqual(len(data['added_products']), 2)
        added_ids = [p['id'] for p in data['added_products']]
        self.assertIn(self.product1.id, added_ids)
        self.assertIn(self.product2.id, added_ids)

    def test_bulk_add_category_to_subrubro(self):
        self.assertEqual(self.subrubro.products.count(), 0)
        self.assertEqual(self.rubro.products.count(), 0)
        
        url = reverse('admin_brand_subrubro_bulk_add_category', args=[self.subrubro.pk])
        response = self.client.post(url, {'category_id': self.child_cat.pk}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['added_count'], 1) # product1
        self.assertEqual(self.subrubro.products.count(), 1)
        # Check that it cascade associated it to the parent rubro too
        self.assertEqual(self.rubro.products.count(), 1)
        
        # Verify added_products structure
        self.assertIn('added_products', data)
        self.assertEqual(len(data['added_products']), 1)
        self.assertEqual(data['added_products'][0]['id'], self.product1.id)


class BrandPremiumSPAAndPaginationTestCase(TestCase):
    """Test case for paginated search results, association flags, and preview stats in brand rubro/subrubro products screens."""

    def setUp(self):
        self.brand = Brand.objects.create(name="Honda")
        self.rubro = BrandRubro.objects.create(brand=self.brand, name="Motor")
        self.subrubro = BrandSubrubro.objects.create(brand_rubro=self.rubro, name="Pistones")
        
        self.category = Category.objects.create(name="Pistones Categoria")
        
        # Create 35 products to test pagination (30 products per page)
        self.products = []
        for i in range(35):
            self.products.append(
                Product.objects.create(
                    sku=f"HON-PST-{i:02d}",
                    name=f"Piston Honda {i}",
                    price=Decimal("100.00"),
                    category=self.category,
                    is_active=True
                )
            )
            
        # Associate first 5 products to the subrubro (and rubro)
        for prod in self.products[:5]:
            self.rubro.products.add(prod)
            self.subrubro.products.add(prod)
            
        self.client = CompanyScopedBrandClient()
        self.user = User.objects.create_superuser('josueflexs', 'admin@honda.com', 'adminpass')
        self.client.login(username='josueflexs', password='adminpass')

    def test_rubro_paginated_search_results_with_association_flag(self):
        url = reverse('admin_brand_rubro_products', args=[self.rubro.pk])
        
        # Page 1 (should return 30 products, has_more=True)
        response = self.client.get(url, {'category_id': self.category.pk, 'page': '1', 'ajax': '1'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertTrue(data['has_more'])
        self.assertEqual(len(data['results']), 30)
        
        # The products in data['results'] should have the correct is_associated flag
        associated_ids = {p.id for p in self.products[:5]}
        for res_item in data['results']:
            if res_item['id'] in associated_ids:
                self.assertTrue(res_item['is_associated'])
            else:
                self.assertFalse(res_item['is_associated'])
            
        # Page 2 (should return 5 products, has_more=False)
        response = self.client.get(url, {'category_id': self.category.pk, 'page': '2', 'ajax': '1'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertFalse(data['has_more'])
        self.assertEqual(len(data['results']), 5)

    def test_subrubro_paginated_search_results_with_association_flag(self):
        url = reverse('admin_brand_subrubro_products', args=[self.subrubro.pk])
        
        # Page 1 (should return 30 products, has_more=True)
        response = self.client.get(url, {'category_id': self.category.pk, 'page': '1', 'ajax': '1'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertTrue(data['has_more'])
        self.assertEqual(len(data['results']), 30)

    def test_workspace_search_returns_commercial_metadata_and_conflicts(self):
        other_brand = Brand.objects.create(name="Otra marca")
        other_rubro = BrandRubro.objects.create(
            brand=other_brand,
            name="Motor",
        )
        BrandRubroProductOrder.objects.create(
            brand_rubro=other_rubro,
            product=self.products[10],
            sort_order=10,
        )

        response = self.client.get(
            reverse("admin_brand_rubro_products", args=[self.rubro.pk]),
            {
                "q": self.products[10].sku,
                "assignment": "other",
                "ajax": "1",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["total_count"], 1)
        result = payload["results"][0]
        self.assertEqual(result["id"], self.products[10].pk)
        self.assertIn("supplier", result)
        self.assertIn("category", result)
        self.assertIn("stock", result)
        self.assertTrue(result["has_conflict"])
        self.assertTrue(result["assignments"])

    def test_workspace_search_splits_words_and_understands_target_context(self):
        response = self.client.get(
            reverse("admin_brand_rubro_products", args=[self.rubro.pk]),
            {
                "q": "honda piston 10",
                "ajax": "1",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["total_count"], 1)
        self.assertEqual(payload["results"][0]["id"], self.products[10].pk)

    def test_workspace_search_includes_primary_and_additional_categories(self):
        bujes = Category.objects.create(name="Bujes de cabina")
        primary = Product.objects.create(
            sku="CAT-PRIMARY",
            name="Soporte principal",
            price=Decimal("100.00"),
            category=bujes,
            is_active=True,
        )
        additional = Product.objects.create(
            sku="CAT-ADDITIONAL",
            name="Soporte adicional",
            price=Decimal("100.00"),
            is_active=True,
        )
        additional.categories.add(bujes)

        response = self.client.get(
            reverse("admin_brand_rubro_products", args=[self.rubro.pk]),
            {
                "q": "honda bujes",
                "ajax": "1",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        result_ids = {item["id"] for item in response.json()["results"]}
        self.assertEqual(result_ids, {primary.pk, additional.pk})

    def test_workspace_search_prefers_strict_brand_and_category_matches(self):
        bujes = Category.objects.create(name="Bujes")
        honda_category = Category.objects.create(name="Honda", parent=bujes)
        toyota_category = Category.objects.create(name="Toyota", parent=bujes)
        honda_product = Product.objects.create(
            sku="STRICT-HONDA",
            name="Componente especial",
            price=Decimal("100.00"),
            category=honda_category,
            is_active=True,
        )
        Product.objects.create(
            sku="STRICT-TOYOTA",
            name="Componente especial",
            price=Decimal("100.00"),
            category=toyota_category,
            is_active=True,
        )

        response = self.client.get(
            reverse("admin_brand_rubro_products", args=[self.rubro.pk]),
            {
                "q": "honda bujes",
                "ajax": "1",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        result_ids = {item["id"] for item in response.json()["results"]}
        self.assertEqual(result_ids, {honda_product.pk})

    def test_workspace_context_only_query_still_filters_the_catalog(self):
        generic = Product.objects.create(
            sku="GENERIC-01",
            name="Soporte universal",
            price=Decimal("100.00"),
            is_active=True,
        )

        response = self.client.get(
            reverse("admin_brand_rubro_products", args=[self.rubro.pk]),
            {
                "q": "honda",
                "ajax": "1",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        result_ids = {item["id"] for item in response.json()["results"]}
        self.assertNotIn(generic.pk, result_ids)
        self.assertEqual(len(result_ids), 30)

    def test_workspace_bulk_assignment_preserves_optional_observation(self):
        url = reverse("admin_brand_rubro_bulk_assign", args=[self.rubro.pk])
        response = self.client.post(
            url,
            data='{"product_ids": [%d], "observation": "Revision manual"}'
            % self.products[10].pk,
            content_type="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["created_count"], 1)
        self.assertTrue(payload["can_undo"])
        self.assertEqual(BrandCatalogBatch.objects.get(pk=payload["batch_id"]).observation, "Revision manual")
        self.assertTrue(
            BrandRubroProductOrder.objects.filter(
                brand_rubro=self.rubro,
                product=self.products[10],
            ).exists()
        )

    def test_workspace_assignment_without_observation_keeps_audit_and_undo(self):
        for kind, target in (("rubro", self.rubro), ("subrubro", self.subrubro)):
            for observation in (None, "", "   "):
                with self.subTest(kind=kind, observation=observation):
                    data = {"product_ids": [self.products[0].pk, self.products[10].pk, self.products[10].pk]}
                    if observation is not None:
                        data["observation"] = observation
                    response = self.client.post(
                        reverse(f"admin_brand_{kind}_bulk_assign", args=[target.pk]),
                        data=data, content_type="application/json",
                        HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                    )
                    self.assertEqual(response.status_code, 200, response.content)
                    payload = response.json()
                    self.assertEqual(payload["created_count"], 1)
                    self.assertEqual(payload["existing_count"], 1)
                    self.assertTrue(payload["can_undo"])
                    batch = BrandCatalogBatch.objects.get(pk=payload["batch_id"])
                    self.assertEqual(batch.observation, "")
                    self.assertEqual(batch.created_by, self.user)
                    self.assertIsNotNone(batch.created_at)
                    self.assertTrue(target.products.filter(pk=self.products[10].pk).exists())
                    response = self.client.post(
                        reverse("admin_brand_catalog_batch_undo", args=[batch.pk]),
                        data={}, content_type="application/json",
                        HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                    )
                    self.assertEqual(response.status_code, 200, response.content)
                    batch.refresh_from_db()
                    self.assertEqual(batch.status, BrandCatalogBatch.STATUS_UNDONE)
                    self.assertEqual(batch.undone_by, self.user)
                    self.assertTrue(target.products.filter(pk=self.products[0].pk).exists())
                    self.assertFalse(target.products.filter(pk=self.products[10].pk).exists())

    def test_workspace_optional_observation_move_is_reversible(self):
        other_brand = Brand.objects.create(name="Otra marca")
        other_rubro = BrandRubro.objects.create(brand=other_brand, name="Motor")
        existing = BrandRubroProductOrder.objects.create(
            brand_rubro=other_rubro, product=self.products[10], sort_order=40,
        )
        response = self.client.post(
            reverse("admin_brand_subrubro_bulk_assign", args=[self.subrubro.pk]),
            data={"product_ids": [self.products[10].pk], "mode": "move"},
            content_type="application/json", HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200, response.content)
        batch = BrandCatalogBatch.objects.get(pk=response.json()["batch_id"])
        self.assertEqual(batch.operation, BrandCatalogBatch.OPERATION_MOVE)
        self.assertEqual(batch.observation, "")
        self.assertFalse(BrandRubroProductOrder.objects.filter(pk=existing.pk).exists())
        self.assertTrue(self.subrubro.products.filter(pk=self.products[10].pk).exists())
        response = self.client.post(
            reverse("admin_brand_catalog_batch_undo", args=[batch.pk]),
            data={}, content_type="application/json", HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200, response.content)
        restored = BrandRubroProductOrder.objects.get(brand_rubro=other_rubro, product=self.products[10])
        self.assertEqual(restored.sort_order, 40)
        self.assertFalse(self.subrubro.products.filter(pk=self.products[10].pk).exists())

    def test_workspace_html_assignment_observation_is_not_required(self):
        class TextareaParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.fields = {}

            def handle_starttag(self, tag, attrs):
                if tag == "textarea":
                    attrs = dict(attrs)
                    self.fields[attrs.get("id")] = attrs

        for kind, target in (("rubro", self.rubro), ("subrubro", self.subrubro)):
            with self.subTest(kind=kind):
                response = self.client.get(reverse(f"admin_brand_{kind}_products", args=[target.pk]))
                self.assertContains(response, "Observación (opcional)")
                parser = TextareaParser()
                parser.feed(response.content.decode())
                self.assertNotIn("required", parser.fields["brandAssignObservation"])
                self.assertIn("required", parser.fields["brandSyncObservation"])
                self.assertIn("required", parser.fields["brandRemoveObservation"])
                self.assertContains(response, "?v=20260831-optional-assignment")

    def test_workspace_other_operations_still_require_observation(self):
        self.subrubro.helper_categories.add(self.category)
        for route, data in (
            ("admin_brand_subrubro_bulk_remove", {"product_ids": [self.products[0].pk]}),
            ("admin_brand_subrubro_sync", {"action": "confirm", "mode": "add"}),
        ):
            with self.subTest(route=route):
                response = self.client.post(
                    reverse(route, args=[self.subrubro.pk]), data=data,
                    content_type="application/json", HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                )
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn("observacion", response.json()["error"])
                self.assertFalse(BrandCatalogBatch.objects.exists())
                self.assertEqual(self.subrubro.products.count(), 5)

    def test_sync_preview_does_not_change_data_and_confirm_creates_batch(self):
        self.subrubro.helper_categories.add(self.category)
        url = reverse("admin_brand_rubro_sync", args=[self.rubro.pk])
        response = self.client.post(
            url,
            data='{"action": "preview"}',
            content_type="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        preview = response.json()
        self.assertEqual(preview["new_count"], 30)
        self.assertEqual(self.rubro.products.count(), 5)
        self.assertFalse(BrandCatalogBatch.objects.exists())

        response = self.client.post(
            url,
            data='{"action": "confirm", "mode": "add", "observation": "Vista previa revisada"}',
            content_type="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["created_count"], 30)
        self.assertTrue(BrandCatalogBatch.objects.filter(pk=payload["batch_id"]).exists())
        self.assertEqual(self.rubro.products.count(), 35)

    def test_rubro_bulk_add_preview_stats(self):
        url = reverse('admin_brand_rubro_preview_category_bulk', args=[self.rubro.pk])
        response = self.client.get(url, {'category_id': self.category.pk})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['total_count'], 35)
        self.assertEqual(data['associated_count'], 5)
        self.assertEqual(data['new_count'], 30)

    def test_subrubro_bulk_add_preview_stats(self):
        url = reverse('admin_brand_subrubro_preview_category_bulk', args=[self.subrubro.pk])
        response = self.client.get(url, {'category_id': self.category.pk})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['total_count'], 35)
        self.assertEqual(data['associated_count'], 5)
        self.assertEqual(data['new_count'], 30)

    def test_rubro_auto_sync_returns_added_products(self):
        self.subrubro.helper_categories.add(self.category)
        url = reverse('admin_brand_rubro_sync', args=[self.rubro.pk])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['added_count'], 30)
        self.assertIn('added_products', data)
        self.assertEqual(len(data['added_products']), 30)
        self.assertIn('id', data['added_products'][0])
        self.assertIn('sku', data['added_products'][0])
        self.assertIn('name', data['added_products'][0])

    def test_subrubro_auto_sync_returns_added_products(self):
        self.subrubro.helper_categories.add(self.category)
        url = reverse('admin_brand_subrubro_sync', args=[self.subrubro.pk])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['added_count'], 30)
        self.assertIn('added_products', data)
        self.assertEqual(len(data['added_products']), 30)


class BrandGridAutofiltersTestCase(TestCase):
    """Test case for Brand/Rubro/Subrubro filters in the product grid editor."""

    def setUp(self):
        self.brand = Brand.objects.create(name="Ford_Filter")
        self.rubro = BrandRubro.objects.create(brand=self.brand, name="Filtros")
        self.subrubro = BrandSubrubro.objects.create(brand_rubro=self.rubro, name="Filtros Aire")
        
        self.product1 = Product.objects.create(
            sku="FLT-FRD-01",
            name="Filtro Aire Ford",
            price=Decimal("150.00"),
            is_active=True
        )
        self.product2 = Product.objects.create(
            sku="FLT-OTH-02",
            name="Filtro General",
            price=Decimal("100.00"),
            is_active=True
        )
        
        self.rubro.products.add(self.product1)
        self.subrubro.products.add(self.product1)
        
        self.client = CompanyScopedBrandClient()
        self.user = User.objects.create_superuser('josueflexs', 'admin@filter.com', 'adminpass')
        self.client.login(username='josueflexs', password='adminpass')

    def test_filter_by_brand(self):
        url = reverse('admin_product_grid_editor')
        response = self.client.get(url, {'f_brand': self.brand.id})
        self.assertEqual(response.status_code, 200)
        products = list(response.context['page_obj'].object_list)
        self.assertIn(self.product1, products)
        self.assertNotIn(self.product2, products)

    def test_filter_by_rubro(self):
        url = reverse('admin_product_grid_editor')
        response = self.client.get(url, {'f_brand_rubro': self.rubro.id})
        self.assertEqual(response.status_code, 200)
        products = list(response.context['page_obj'].object_list)
        self.assertIn(self.product1, products)
        self.assertNotIn(self.product2, products)

    def test_filter_by_subrubro(self):
        url = reverse('admin_product_grid_editor')
        response = self.client.get(url, {'f_brand_subrubro': self.subrubro.id})
        self.assertEqual(response.status_code, 200)
        products = list(response.context['page_obj'].object_list)
        self.assertIn(self.product1, products)
        self.assertNotIn(self.product2, products)
