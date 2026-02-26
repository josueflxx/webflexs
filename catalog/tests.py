from decimal import Decimal
from io import BytesIO

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from openpyxl import load_workbook

from accounts.models import ClientProfile
from catalog.services.clamp_code import generarCodigo, parsearCodigo
from catalog.models import Category, ClampMeasureRequest, ClampSpecs, Product
from core.models import CatalogExcelTemplate, CatalogExcelTemplateColumn, CatalogExcelTemplateSheet
from orders.models import CartItem


class ClampCodeTests(SimpleTestCase):
    def test_parse_real_examples(self):
        parsed_abl = parsearCodigo("ABL1135400C")
        self.assertEqual(parsed_abl["prefijo"], "ABL")
        self.assertEqual(parsed_abl["tipo"], "LAMINADA")
        self.assertEqual(parsed_abl["diametro_compactado"], "1")
        self.assertEqual(parsed_abl["diametro"], "1")
        self.assertEqual(parsed_abl["ancho"], 135)
        self.assertEqual(parsed_abl["largo"], 400)
        self.assertEqual(parsed_abl["forma"], "CURVA")

        parsed_abt = parsearCodigo("ABT91685270P")
        self.assertEqual(parsed_abt["prefijo"], "ABT")
        self.assertEqual(parsed_abt["tipo"], "TREFILADA")
        self.assertEqual(parsed_abt["diametro_compactado"], "916")
        self.assertEqual(parsed_abt["diametro"], "9/16")
        self.assertEqual(parsed_abt["ancho"], 85)
        self.assertEqual(parsed_abt["largo"], 270)
        self.assertEqual(parsed_abt["forma"], "PLANA")

        parsed_semicurva = parsearCodigo("ABT3480220S")
        self.assertEqual(parsed_semicurva["diametro_compactado"], "34")
        self.assertEqual(parsed_semicurva["diametro"], "3/4")
        self.assertEqual(parsed_semicurva["ancho"], 80)
        self.assertEqual(parsed_semicurva["largo"], 220)
        self.assertEqual(parsed_semicurva["forma"], "SEMICURVA")

    def test_parse_variable_length_diameters(self):
        parsed = parsearCodigo("ABT111680220S")
        self.assertEqual(parsed["diametro_compactado"], "1116")
        self.assertEqual(parsed["diametro"], "11/16")
        self.assertEqual(parsed["ancho"], 80)
        self.assertEqual(parsed["largo"], 220)
        self.assertFalse(parsed["diametro_requiere_mapeo"])

    def test_generate_known_codes(self):
        code_1 = generarCodigo(
            tipo="ABT",
            diametro="9/16",
            ancho=85,
            largo=270,
            forma="P",
        )
        self.assertEqual(code_1, "ABT91685270P")

        code_2 = generarCodigo(
            tipo="TREFILADA",
            diametro="3/4",
            ancho=80,
            largo=220,
            forma="SEMICURVA",
        )
        self.assertEqual(code_2, "ABT3480220S")

        code_3 = generarCodigo(
            tipo="LAMINADA",
            diametro="1",
            ancho=135,
            largo=400,
            forma="CURVA",
        )
        self.assertEqual(code_3, "ABL1135400C")

    def test_generate_unknown_fraction_marks_mapping_pending(self):
        metadata = generarCodigo(
            tipo="ABT",
            diametro="1 1/8",
            ancho=90,
            largo=250,
            forma="P",
            with_metadata=True,
        )
        self.assertEqual(metadata["codigo"], "ABT11890250P")
        self.assertTrue(metadata["diametro_requiere_mapeo"])
        self.assertTrue(metadata["warnings"])

    def test_generate_unknown_fraction_in_strict_mode_raises(self):
        with self.assertRaises(ValueError):
            generarCodigo(
                tipo="ABT",
                diametro="1 1/8",
                ancho=90,
                largo=250,
                forma="P",
                strict_diameter_mapping=True,
            )


class ClampMeasureRequestFlowTests(TestCase):
    def setUp(self):
        self.client_user = User.objects.create_user(username="cliente_medidas", password="secret123")
        ClientProfile.objects.create(
            user=self.client_user,
            company_name="Cliente Medidas",
            is_approved=True,
        )
        self.client.force_login(self.client_user)

        self.category = Category.objects.create(name="Abrazaderas", is_active=True)
        self.product = Product.objects.create(
            sku="ABT3480220S",
            name="ABRAZADERA TREFILADA DE 3/4 X 80 X 220 SEMICURVA",
            price=Decimal("12000.00"),
            stock=8,
            is_active=True,
            category=self.category,
        )
        self.product.categories.add(self.category)
        ClampSpecs.objects.create(
            product=self.product,
            fabrication="TREFILADA",
            diameter="3/4",
            width=80,
            length=220,
            shape="SEMICURVA",
        )

    def test_calculate_detects_existing_measure(self):
        response = self.client.post(
            reverse("catalog_clamp_request"),
            data={
                "action": "check_exists",
                "client_name": "Cliente Test",
                "clamp_type": "trefilada",
                "diameter": "3/4",
                "width_mm": "80",
                "length_mm": "220",
                "profile_type": "SEMICURVA",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["has_matches"])
        self.assertEqual(response.context["matching_products"][0].sku, "ABT3480220S")

    def test_submit_creates_request_when_measure_not_found(self):
        response = self.client.post(
            reverse("catalog_clamp_request"),
            data={
                "action": "submit_request",
                "price_list_key": "lista_2",
                "client_name": "Cliente Nuevo",
                "client_email": "cliente@demo.com",
                "quantity": "3",
                "clamp_type": "trefilada",
                "diameter": "3/4",
                "width_mm": "81",
                "length_mm": "220",
                "profile_type": "SEMICURVA",
            },
        )

        self.assertEqual(response.status_code, 302)
        request_obj = ClampMeasureRequest.objects.get()
        self.assertEqual(request_obj.client_name, "Cliente Nuevo")
        self.assertEqual(request_obj.selected_price_list, "lista_1")
        self.assertFalse(request_obj.exists_in_catalog)

    def test_non_client_non_admin_cannot_access_clamp_measure_page(self):
        user = User.objects.create_user(username="usuario_sin_perfil", password="secret123")
        self.client.force_login(user)

        response = self.client.get(reverse("catalog_clamp_request"), follow=True)

        self.assertRedirects(response, reverse("catalog"))
        self.assertContains(response, "solo para clientes aprobados o administradores")

    def test_authenticated_client_sees_confirmed_price_in_history(self):
        user = User.objects.create_user(username="cliente_medida", password="secret123")
        ClientProfile.objects.create(
            user=user,
            company_name="Cliente Medida",
            is_approved=True,
        )
        ClampMeasureRequest.objects.create(
            client_user=user,
            client_name="Cliente Medida",
            clamp_type="trefilada",
            is_zincated=False,
            diameter="3/4",
            width_mm=81,
            length_mm=220,
            profile_type="SEMICURVA",
            quantity=1,
            description="ABRAZADERA TREFILADA DE 3/4 X 81 X 220 SEMICURVA",
            generated_code="ABT3481220S",
            dollar_rate=Decimal("1300.00"),
            steel_price_usd=Decimal("1450.00"),
            supplier_discount_pct=Decimal("0.00"),
            general_increase_pct=Decimal("40.00"),
            base_cost=Decimal("1000.00"),
            selected_price_list="lista_1",
            estimated_final_price=Decimal("1400.00"),
            confirmed_price_list="lista_3",
            confirmed_price=Decimal("1600.00"),
            status=ClampMeasureRequest.STATUS_QUOTED,
            client_response_note="Precio confirmado por ventas.",
        )

        self.client.force_login(user)
        response = self.client.get(reverse("catalog_clamp_request"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Precio confirmado por ventas.")
        self.assertEqual(response.context["client_requests"][0].confirmed_price, Decimal("1600.00"))

    def test_client_can_add_completed_request_to_cart(self):
        user = User.objects.create_user(username="cliente_add_carrito", password="secret123")
        ClientProfile.objects.create(
            user=user,
            company_name="Cliente Carrito",
            is_approved=True,
        )
        clamp_request = ClampMeasureRequest.objects.create(
            client_user=user,
            client_name="Cliente Carrito",
            clamp_type="trefilada",
            is_zincated=False,
            diameter="3/4",
            width_mm=81,
            length_mm=220,
            profile_type="SEMICURVA",
            quantity=2,
            description="ABRAZADERA TREFILADA DE 3/4 X 81 X 220 SEMICURVA",
            generated_code="ABT3481220S",
            dollar_rate=Decimal("1300.00"),
            steel_price_usd=Decimal("1450.00"),
            supplier_discount_pct=Decimal("0.00"),
            general_increase_pct=Decimal("40.00"),
            base_cost=Decimal("1000.00"),
            selected_price_list="lista_1",
            estimated_final_price=Decimal("1400.00"),
            confirmed_price_list="lista_2",
            confirmed_price=Decimal("1500.00"),
            status=ClampMeasureRequest.STATUS_COMPLETED,
        )

        self.client.force_login(user)
        response = self.client.post(
            reverse("catalog_clamp_request_add_to_cart", args=[clamp_request.pk]),
            data={"quantity": "3"},
        )

        self.assertEqual(response.status_code, 302)
        cart_item = CartItem.objects.select_related("product").get(cart__user=user)
        self.assertEqual(cart_item.quantity, 3)
        self.assertEqual(cart_item.clamp_request_id, clamp_request.pk)
        self.assertFalse(cart_item.product.is_active)

        clamp_request.refresh_from_db()
        self.assertIsNotNone(clamp_request.linked_product_id)
        self.assertEqual(clamp_request.linked_product_id, cart_item.product_id)
        self.assertIsNotNone(clamp_request.added_to_cart_at)

    def test_client_cannot_add_non_completed_request_to_cart(self):
        user = User.objects.create_user(username="cliente_no_completada", password="secret123")
        ClientProfile.objects.create(
            user=user,
            company_name="Cliente No Completada",
            is_approved=True,
        )
        clamp_request = ClampMeasureRequest.objects.create(
            client_user=user,
            client_name="Cliente No Completada",
            clamp_type="trefilada",
            is_zincated=False,
            diameter="3/4",
            width_mm=81,
            length_mm=220,
            profile_type="SEMICURVA",
            quantity=1,
            description="ABRAZADERA TREFILADA DE 3/4 X 81 X 220 SEMICURVA",
            generated_code="ABT3481220S",
            dollar_rate=Decimal("1300.00"),
            steel_price_usd=Decimal("1450.00"),
            supplier_discount_pct=Decimal("0.00"),
            general_increase_pct=Decimal("40.00"),
            base_cost=Decimal("1000.00"),
            selected_price_list="lista_1",
            estimated_final_price=Decimal("1400.00"),
            status=ClampMeasureRequest.STATUS_QUOTED,
        )

        self.client.force_login(user)
        response = self.client.post(
            reverse("catalog_clamp_request_add_to_cart", args=[clamp_request.pk]),
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(CartItem.objects.filter(cart__user=user).exists())


class CatalogAdvancedSearchTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name="Abrazaderas", slug="abrazaderas", is_active=True)

        self.product_exact = Product.objects.create(
            sku="ABT3480220S",
            name="ABRAZADERA TREFILADA DE 3/4 X 80 X 220 SEMICURVA",
            description="Uso suspension trasera",
            supplier="MOVIGOM",
            price=Decimal("100.00"),
            stock=5,
            is_active=True,
            category=self.category,
        )
        self.product_exact.categories.add(self.category)
        ClampSpecs.objects.create(
            product=self.product_exact,
            fabrication="TREFILADA",
            diameter="3/4",
            width=80,
            length=220,
            shape="SEMICURVA",
        )

        self.product_other = Product.objects.create(
            sku="ABT3485220P",
            name="ABRAZADERA TREFILADA DE 3/4 X 85 X 220 PLANA",
            description="Aplicacion delantera",
            supplier="ROCES",
            price=Decimal("110.00"),
            stock=4,
            is_active=True,
            category=self.category,
        )
        self.product_other.categories.add(self.category)
        ClampSpecs.objects.create(
            product=self.product_other,
            fabrication="TREFILADA",
            diameter="3/4",
            width=85,
            length=220,
            shape="PLANA",
        )

    def test_search_parses_dimensions_and_type(self):
        response = self.client.get(
            reverse("catalog"),
            {"q": "tipo:trefilada 3/4x80x220 semicurva"},
        )

        self.assertEqual(response.status_code, 200)
        page_items = list(response.context["page_obj"].object_list)
        self.assertEqual(len(page_items), 1)
        self.assertEqual(page_items[0].sku, "ABT3480220S")

    def test_search_supports_exclusion_term(self):
        response = self.client.get(
            reverse("catalog"),
            {"q": "abrazadera -delantera"},
        )

        self.assertEqual(response.status_code, 200)
        skus = [product.sku for product in response.context["page_obj"].object_list]
        self.assertIn("ABT3480220S", skus)
        self.assertNotIn("ABT3485220P", skus)

    def test_search_relevance_prioritizes_exact_sku(self):
        response = self.client.get(
            reverse("catalog"),
            {"q": "ABT3480220S", "order": "relevance"},
        )

        self.assertEqual(response.status_code, 200)
        first_product = response.context["page_obj"].object_list[0]
        self.assertEqual(first_product.sku, "ABT3480220S")


class ProductDetailTemplateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="cliente_template", password="secret123")
        self.category = Category.objects.create(name="Plantillas", is_active=True)
        self.product = Product.objects.create(
            id=24950,
            sku="TPL-24950",
            name="Producto Template",
            price=Decimal("1000.00"),
            stock=5,
            is_active=True,
            category=self.category,
        )
        self.product.categories.add(self.category)

    def test_product_id_is_unlocalized_in_inline_js(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("product_detail", args=[self.product.sku]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "addToCart(24950)")
        self.assertContains(response, "toggleFavorite(24950, this)")


class CatalogClientExcelDownloadTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name="Categoria XLSX", slug="categoria-xlsx", is_active=True)
        self.product = Product.objects.create(
            sku="XLSX-001",
            name="Producto XLSX",
            supplier="Proveedor XLSX",
            price=Decimal("1234.56"),
            cost=Decimal("900.00"),
            stock=10,
            is_active=True,
            category=self.category,
        )
        self.product.categories.add(self.category)

        self.approved_user = User.objects.create_user(username="cliente_xlsx_ok", password="secret123")
        ClientProfile.objects.create(
            user=self.approved_user,
            company_name="Cliente XLSX",
            is_approved=True,
        )
        self.unapproved_user = User.objects.create_user(username="cliente_xlsx_no", password="secret123")
        ClientProfile.objects.create(
            user=self.unapproved_user,
            company_name="Cliente XLSX No",
            is_approved=False,
        )

        self.template = CatalogExcelTemplate.objects.create(
            name="Plantilla Cliente XLSX",
            is_active=True,
            is_client_download_enabled=True,
            client_download_label="Descargar version clientes",
        )
        self.sheet = CatalogExcelTemplateSheet.objects.create(
            template=self.template,
            name="Catalogo",
            include_header=True,
            only_active_products=True,
            sort_by="name_asc",
        )
        self.sheet.categories.add(self.category)
        CatalogExcelTemplateColumn.objects.create(sheet=self.sheet, key="sku", order=1, is_active=True)
        CatalogExcelTemplateColumn.objects.create(sheet=self.sheet, key="name", order=2, is_active=True)
        CatalogExcelTemplateColumn.objects.create(sheet=self.sheet, key="price", order=3, is_active=True)

    def test_approved_client_can_download_published_catalog_excel(self):
        self.client.force_login(self.approved_user)
        response = self.client.get(reverse("catalog_client_excel_download"))

        self.assertEqual(response.status_code, 200)
        self.assertIn("spreadsheetml.sheet", response["Content-Type"])
        workbook = load_workbook(BytesIO(response.content))
        worksheet = workbook["Catalogo"]
        self.assertEqual(worksheet["A1"].value, "SKU")
        self.assertEqual(worksheet["B2"].value, "Producto XLSX")

    def test_unapproved_client_cannot_download_catalog_excel(self):
        self.client.force_login(self.unapproved_user)
        response = self.client.get(reverse("catalog_client_excel_download"), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "solo para clientes aprobados")

    def test_download_warns_when_no_template_published(self):
        self.template.is_client_download_enabled = False
        self.template.save(update_fields=["is_client_download_enabled", "updated_at"])

        self.client.force_login(self.approved_user)
        response = self.client.get(reverse("catalog_client_excel_download"), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No hay una plantilla de Excel publicada para clientes")
