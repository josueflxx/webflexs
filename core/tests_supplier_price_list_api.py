from decimal import Decimal
import tempfile

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from catalog.models import (
    Product,
    ProductSupplier,
    Supplier,
    SupplierCostHistory,
    SupplierPriceListBatch,
)
from core.models import Company


@override_settings(
    FEATURE_EXTERNAL_EDITOR_ENABLED=True,
    FEATURE_EXTERNAL_EDITOR_WRITES=True,
)
class ExternalEditorSupplierPriceListApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="supplier_import_admin",
            password="secret123",
            is_staff=True,
            is_superuser=True,
        )
        self.company = Company.objects.create(name="Empresa listas", slug="empresa-listas")
        self.supplier = Supplier.objects.create(name="Proveedor listas")
        self.product = Product.objects.create(
            sku="LISTA-001",
            name="Producto de lista",
            supplier=self.supplier.name,
            supplier_ref=self.supplier,
            cost=Decimal("100.00"),
            price=Decimal("150.00"),
        )
        ProductSupplier.objects.create(
            product=self.product,
            supplier=self.supplier,
            supplier_code="PROV-001",
            current_cost=Decimal("100.00"),
            final_cost=Decimal("100.00"),
            is_preferred=True,
        )
        self.client.force_login(self.user)

    def test_upload_preview_apply_preserve_margin_and_rollback(self):
        source = SimpleUploadedFile(
            "lista.csv",
            b"codigo;descripcion;precio\nPROV-001;Producto proveedor;120,00\n",
            content_type="text/csv",
        )
        with tempfile.TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=media_root):
            uploaded = self.client.post(
                reverse("api_v1:editor_supplier_lists"),
                {
                    "companyId": self.company.pk,
                    "supplierId": self.supplier.pk,
                    "file": source,
                },
            )
            self.assertEqual(uploaded.status_code, 201, uploaded.content)
            batch_id = uploaded.json()["batch"]["id"]

            preview = self.client.post(
                reverse("api_v1:editor_supplier_list_preview", kwargs={"batch_id": batch_id}),
                {
                    "sheetName": "CSV",
                    "headerRow": 1,
                    "defaultCurrency": "ARS",
                    "mapping": {
                        "supplier_code": "codigo",
                        "description": "descripcion",
                        "cost": "precio",
                    },
                },
                content_type="application/json",
            )
            self.assertEqual(preview.status_code, 200, preview.content)
            detail = self.client.get(
                reverse("api_v1:editor_supplier_list_detail", kwargs={"batch_id": batch_id})
            )
            self.assertEqual(detail.status_code, 200)
            self.assertEqual(detail.json()["rows"][0]["decision"], "apply")
            report = self.client.get(
                reverse("api_v1:editor_supplier_list_report", kwargs={"batch_id": batch_id})
            )
            self.assertEqual(report.status_code, 200)
            self.assertIn("PROV-001", report.content.decode("utf-8-sig"))

            applied = self.client.post(
                reverse("api_v1:editor_supplier_list_apply", kwargs={"batch_id": batch_id}),
                {
                    "confirmation": f"APLICAR LISTA {batch_id}",
                    "pricingMode": "preserve_margin",
                },
                content_type="application/json",
            )
            self.assertEqual(applied.status_code, 200, applied.content)
            self.product.refresh_from_db()
            self.assertEqual(self.product.cost, Decimal("120.00"))
            self.assertEqual(self.product.price, Decimal("180.00"))
            self.assertTrue(
                SupplierCostHistory.objects.filter(
                    product_supplier__product=self.product,
                    previous_cost=Decimal("100.00"),
                    new_cost=Decimal("120.00"),
                ).exists()
            )

            rolled_back = self.client.post(
                reverse("api_v1:editor_supplier_list_rollback", kwargs={"batch_id": batch_id}),
                {"confirmation": f"REVERTIR LISTA {batch_id}"},
                content_type="application/json",
            )
            self.assertEqual(rolled_back.status_code, 200, rolled_back.content)
            self.product.refresh_from_db()
            self.assertEqual(self.product.cost, Decimal("100.00"))
            self.assertEqual(self.product.price, Decimal("150.00"))
            self.assertEqual(
                SupplierPriceListBatch.objects.get(pk=batch_id).status,
                SupplierPriceListBatch.STATUS_ROLLED_BACK,
            )

    def test_preview_requires_distinct_code_and_cost_columns(self):
        source = SimpleUploadedFile(
            "columnas_obligatorias.csv",
            b"codigo;descripcion;precio\nPROV-001;Producto proveedor;120\n",
            content_type="text/csv",
        )
        with tempfile.TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=media_root):
            uploaded = self.client.post(
                reverse("api_v1:editor_supplier_lists"),
                {
                    "companyId": self.company.pk,
                    "supplierId": self.supplier.pk,
                    "file": source,
                },
            )
            self.assertEqual(uploaded.status_code, 201, uploaded.content)
            batch_id = uploaded.json()["batch"]["id"]
            preview_url = reverse(
                "api_v1:editor_supplier_list_preview", kwargs={"batch_id": batch_id}
            )

            missing_code = self.client.post(
                preview_url,
                {"mapping": {"description": "descripcion", "cost": "precio"}},
                content_type="application/json",
            )
            self.assertEqual(missing_code.status_code, 400)
            self.assertIn("codigo", str(missing_code.json()["detail"]).lower())

            same_column = self.client.post(
                preview_url,
                {"mapping": {"supplier_code": "codigo", "cost": "codigo"}},
                content_type="application/json",
            )
            self.assertEqual(same_column.status_code, 400)
            self.assertIn("diferentes", str(same_column.json()["detail"]).lower())

    def test_coordinate_mapping_uses_excel_letters_and_keeps_first_data_row(self):
        source = SimpleUploadedFile(
            "sin_encabezados.csv",
            b"PROV-001;120,00\n",
            content_type="text/csv",
        )
        with tempfile.TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=media_root):
            uploaded = self.client.post(
                reverse("api_v1:editor_supplier_lists"),
                {
                    "companyId": self.company.pk,
                    "supplierId": self.supplier.pk,
                    "file": source,
                },
            )
            self.assertEqual(uploaded.status_code, 201, uploaded.content)
            payload = uploaded.json()
            inspection = payload["inspection"]
            self.assertEqual(inspection["mode"], "coordinates")
            self.assertEqual(
                [column["letter"] for column in inspection["columns"]],
                ["A", "B"],
            )

            preview = self.client.post(
                reverse(
                    "api_v1:editor_supplier_list_preview",
                    kwargs={"batch_id": payload["batch"]["id"]},
                ),
                {
                    "sheetName": "CSV",
                    "headerRow": 1,
                    "mapping": {
                        "supplier_code": inspection["columns"][0]["key"],
                        "cost": inspection["columns"][1]["key"],
                    },
                },
                content_type="application/json",
            )
            self.assertEqual(preview.status_code, 200, preview.content)
            detail = self.client.get(
                reverse(
                    "api_v1:editor_supplier_list_detail",
                    kwargs={"batch_id": payload["batch"]["id"]},
                )
            )
            self.assertEqual(detail.status_code, 200)
            row = detail.json()["rows"][0]
            self.assertEqual(row["rowNumber"], 1)
            self.assertEqual(Decimal(row["proposedCost"]), Decimal("120.00"))

    def test_apply_requires_resolving_unmatched_rows(self):
        source = SimpleUploadedFile(
            "desconocido.csv",
            b"codigo;precio\nNO-EXISTE;99\n",
            content_type="text/csv",
        )
        with tempfile.TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=media_root):
            uploaded = self.client.post(
                reverse("api_v1:editor_supplier_lists"),
                {
                    "companyId": self.company.pk,
                    "supplierId": self.supplier.pk,
                    "file": source,
                },
            )
            batch_id = uploaded.json()["batch"]["id"]
            preview = self.client.post(
                reverse("api_v1:editor_supplier_list_preview", kwargs={"batch_id": batch_id}),
                {
                    "sheetName": "CSV",
                    "headerRow": 1,
                    "mapping": {"supplier_code": "codigo", "cost": "precio"},
                },
                content_type="application/json",
            )
            self.assertEqual(preview.status_code, 200)
            blocked = self.client.post(
                reverse("api_v1:editor_supplier_list_apply", kwargs={"batch_id": batch_id}),
                {"confirmation": f"APLICAR LISTA {batch_id}", "pricingMode": "cost_only"},
                content_type="application/json",
            )
            self.assertEqual(blocked.status_code, 400)

            resolved = self.client.post(
                reverse("api_v1:editor_supplier_list_decisions", kwargs={"batch_id": batch_id}),
                {"resolveReviews": "skip"},
                content_type="application/json",
            )
            self.assertEqual(resolved.status_code, 200)
            applied = self.client.post(
                reverse("api_v1:editor_supplier_list_apply", kwargs={"batch_id": batch_id}),
                {"confirmation": f"APLICAR LISTA {batch_id}", "pricingMode": "cost_only"},
                content_type="application/json",
            )
            self.assertEqual(applied.status_code, 200, applied.content)
