from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from decimal import Decimal

from catalog.models import Category, Product, Brand, BrandRubro, BrandSubrubro, BrandSubrubroProductOrder


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
        
        client = Client()
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
        self.client = Client()

    def test_brands_list_page(self):
        response = self.client.get(reverse('brands_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Peugeot")

    def test_brand_detail_page(self):
        response = self.client.get(reverse('brand_detail', args=[self.brand.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Peugeot")
        self.assertContains(response, "Opticas")
        self.assertContains(response, "Opticas Delanteras")
        self.assertContains(response, "OPT-PGT-01")
