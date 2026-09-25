"""
Tests for pricing rules:
1. ProductImporter imports cost (column 17) and auto-calculates price = cost * 2 if price is missing or 0.
2. Product.save auto-calculates price = cost * 2 if cost is set and price is 0 or unset.
3. Quick edit in catalog accepts cost and calculates price = cost * 2.
4. Grid editor cell update for cost calculates price = cost * 2.
5. Catalog, product detail, and brand detail display net prices (sin IVA) with caption 'Precio Neto + IVA'.
6. Client discounts are preserved on net prices.
"""
from decimal import Decimal
import io
import json
import pandas as pd

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from accounts.models import ClientCompany, ClientProfile
from catalog.models import Category, PriceList, PriceListItem, Product, Brand, BrandRubro, BrandSubrubro
from catalog.services.product_importer import ProductImporter
from core.models import Company


class PricingCostRulesTests(TestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser(
            username="admin-test",
            password="testpassword",
            email="admin@example.com",
        )
        self.company = Company.objects.create(name="Flexs Company", slug="flexs-company")
        self.category = Category.objects.create(
            name="Suspension",
            slug="suspension",
            is_active=True,
            visible_in_catalog=True,
        )
        self.product = Product.objects.create(
            sku="TEST-COST-001",
            name="Buje de goma",
            cost=Decimal("1500.00"),
            price=Decimal("3000.00"),
            stock=10,
            category=self.category,
            is_active=True,
        )
        self.product.categories.add(self.category)

    def test_product_save_auto_calculates_price_from_cost_if_zero(self):
        prod = Product(
            sku="TEST-AUTO-PRICE",
            name="Abrazadera U",
            cost=Decimal("250.00"),
            price=Decimal("0.00"),
            category=self.category,
        )
        prod.save()
        self.assertEqual(prod.price, Decimal("500.00"))

    def test_importer_parses_cost_and_calculates_price_if_empty(self):
        # Create an Excel buffer with cost and empty price
        df = pd.DataFrame([
            {
                "codigo": "IMP-001",
                "nombre": "Articulo Importado",
                "costo": "1200.50",
                "precio": "",
                "stock": "5",
            }
        ])
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df.to_excel(writer, index=False)
        buffer.seek(0)

        importer = ProductImporter(buffer)
        result = importer.run(dry_run=False)
        self.assertEqual(result.errors, 0)
        self.assertEqual(result.created, 1)

        created_prod = Product.objects.get(sku="IMP-001")
        self.assertEqual(created_prod.cost, Decimal("1200.50"))
        self.assertEqual(created_prod.price, Decimal("2401.00"))

    def test_catalog_quick_edit_cost_calculates_price(self):
        self.client.force_login(self.superuser)
        session = self.client.session
        session["active_company_id"] = self.company.pk
        session.save()
        url = reverse("product_quick_edit", args=[self.product.pk])

        # Edit only cost (or cost and empty price)
        response = self.client.post(url, {
            "sku": self.product.sku,
            "name": self.product.name,
            "cost": "2000.00",
            "price": "",
            "stock": "15",
            "is_active": "on",
        }, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["product"]["cost"], 2000.0)
        self.assertEqual(data["product"]["price"], 4000.0)

        self.product.refresh_from_db()
        self.assertEqual(self.product.cost, Decimal("2000.00"))
        self.assertEqual(self.product.price, Decimal("4000.00"))

    def test_admin_grid_update_cell_cost_recalculates_price(self):
        self.client.force_login(self.superuser)
        session = self.client.session
        session["active_company_id"] = self.company.pk
        session.save()
        url = reverse("admin_product_grid_update_cell")

        payload = {
            "product_id": self.product.pk,
            "field": "cost",
            "value": "3500.00",
        }
        response = self.client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["cost"], 3500.0)
        self.assertEqual(data["price"], 7000.0)

        self.product.refresh_from_db()
        self.assertEqual(self.product.cost, Decimal("3500.00"))
        self.assertEqual(self.product.price, Decimal("7000.00"))

    def test_catalog_and_detail_display_net_price_and_caption(self):
        # Test anonymous or client view
        client_user = User.objects.create_user(username="client-user", password="password")
        profile = ClientProfile.objects.create(user=client_user, company_name="Cliente S.A.", is_approved=True)
        ClientCompany.objects.create(client_profile=profile, company=self.company)
        self.client.force_login(client_user)
        session = self.client.session
        session["active_company_id"] = self.company.pk
        session.save()

        # Catalog listing
        cat_resp = self.client.get(reverse("catalog"))
        self.assertEqual(cat_resp.status_code, 200)
        self.assertContains(cat_resp, "Precio Neto + IVA")
        # Ensure net price 3000.00 is rendered, not 3630.00 (with IVA)
        self.assertContains(cat_resp, "3.000,00")
        self.assertNotContains(cat_resp, "3.630,00")

        # Product detail
        detail_resp = self.client.get(reverse("product_detail", args=[self.product.sku]))
        self.assertEqual(detail_resp.status_code, 200)
        self.assertContains(detail_resp, "Precio Neto + IVA")
        self.assertEqual(detail_resp.context["base_price"], Decimal("3000.00"))
        self.assertEqual(detail_resp.context["final_price"], Decimal("3000.00"))

    def test_client_discount_applied_on_net_price(self):
        client_user = User.objects.create_user(username="client-discount", password="password")
        profile = ClientProfile.objects.create(user=client_user, company_name="Cliente Con Descuento", is_approved=True)
        link = ClientCompany.objects.create(
            client_profile=profile,
            company=self.company,
            discount_percentage=Decimal("20.00"),
        )
        self.client.force_login(client_user)

        session = self.client.session
        session["active_company_id"] = self.company.pk
        session.save()

        # Net price is 3000.00. 20% discount makes it 2400.00 net.
        detail_resp = self.client.get(reverse("product_detail", args=[self.product.sku]))
        self.assertEqual(detail_resp.status_code, 200)
        self.assertEqual(detail_resp.context["base_price"], Decimal("3000.00"))
        self.assertEqual(detail_resp.context["final_price"], Decimal("2400.00"))
        self.assertContains(detail_resp, "Precio Neto + IVA")
