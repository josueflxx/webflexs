from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from decimal import Decimal

from catalog.models import Category, Product, Brand, BrandRubro, BrandSubrubro, BrandSubrubroProductOrder
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
        self.product = Product.objects.create(
            sku="OPT-PGT-01",
            name="Optica Peugeot 208",
            price=Decimal("350.00"),
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
        self.assertEqual(self.subrubro.products.count(), 1)

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

