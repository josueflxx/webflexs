import shutil
import tempfile
from decimal import Decimal
from io import BytesIO

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from openpyxl import Workbook

from catalog.models import (
    Product,
    ProductSupplier,
    Supplier,
    SupplierCostHistory,
    SupplierPriceListBatch,
    SupplierPriceListRow,
)
from catalog.services.product_suppliers import upsert_product_supplier_offer
from catalog.services.supplier_price_lists import (
    apply_supplier_price_list,
    generate_supplier_price_list_preview,
    update_row_decisions,
)
from core.models import AdminCapabilityProfile, AdminCompanyAccess, Company, ImportExecution
from core.services.authorization import CAP_MANAGE_PRODUCTS, CAP_RUN_IMPORTS


class SupplierPriceListPhase2Tests(TestCase):
    def setUp(self):
        self.media_root = tempfile.mkdtemp(prefix="flexs_phase2_tests_")
        self.media_override = override_settings(MEDIA_ROOT=self.media_root)
        self.media_override.enable()
        self.company, _created = Company.objects.get_or_create(name="Empresa Phase 2")
        self.user = User.objects.create_superuser("phase2_admin", "phase2@example.com", "secret")
        self.supplier = Supplier.objects.create(name="Proveedor Phase 2")
        self.first = Product.objects.create(
            sku="INT-001", name="Producto con codigo", price="200", cost="100"
        )
        self.second = Product.objects.create(
            sku="INT-002", name="Producto por SKU", price="160", cost="70"
        )
        self.description_match = Product.objects.create(
            sku="INT-003", name="Producto solo descripcion", price="120", cost="40"
        )
        self.missing = Product.objects.create(
            sku="INT-004", name="Producto ausente", price="90", cost="30"
        )
        self.first_offer, _history = upsert_product_supplier_offer(
            product=self.first,
            supplier=self.supplier,
            current_cost="100",
            supplier_code="ABC-001",
            discount_percentage="10",
            bonus_percentage="5",
            tax_percentage="21",
            is_preferred=True,
            source="test_setup",
        )
        self.missing_offer, _history = upsert_product_supplier_offer(
            product=self.missing,
            supplier=self.supplier,
            current_cost="30",
            supplier_code="MISS-004",
            is_preferred=False,
            source="test_setup",
        )

    def tearDown(self):
        self.media_override.disable()
        shutil.rmtree(self.media_root, ignore_errors=True)

    def _batch(self, content=None, name="lista.csv", digest="a" * 64):
        content = content or (
            "codigo;sku;descripcion;costo\n"
            "ABC-001;;Producto con codigo;120,00\n"
            ";INT-002;Producto por SKU;80\n"
            ";;Producto solo descripcion;50\n"
            "DESCONOCIDO;;No existe;10\n"
        ).encode("utf-8")
        return SupplierPriceListBatch.objects.create(
            supplier=self.supplier,
            company=self.company,
            source_file=SimpleUploadedFile(name, content, content_type="text/csv"),
            original_filename=name,
            file_sha256=digest,
            file_size=len(content),
            created_by=self.user,
        )

    @staticmethod
    def _mapping():
        return {
            "supplier_code": "codigo",
            "internal_sku": "sku",
            "description": "descripcion",
            "cost": "costo",
        }

    def test_preview_matches_safely_compares_and_never_creates_products(self):
        product_count = Product.objects.count()
        batch = generate_supplier_price_list_preview(self._batch(), mapping=self._mapping())

        self.assertEqual(Product.objects.count(), product_count)
        source_rows = batch.rows.filter(row_type=SupplierPriceListRow.TYPE_SOURCE)
        self.assertEqual(source_rows.count(), 4)
        coded = source_rows.get(supplier_code="ABC-001")
        self.assertEqual(coded.matched_product, self.first)
        self.assertEqual(coded.match_method, "supplier_code_exact")
        self.assertEqual(coded.change_type, SupplierPriceListRow.CHANGE_INCREASE)
        self.assertEqual(coded.decision, SupplierPriceListRow.DECISION_APPLY)
        self.assertEqual(coded.discount_percentage, Decimal("10"))
        self.assertEqual(coded.bonus_percentage, Decimal("5"))
        self.assertEqual(coded.tax_percentage, Decimal("21"))

        sku_row = source_rows.get(normalized_data__internal_sku="INT-002")
        self.assertEqual(sku_row.matched_product, self.second)
        self.assertEqual(sku_row.change_type, SupplierPriceListRow.CHANGE_NEW_RELATION)

        description_row = source_rows.get(matched_product=self.description_match)
        self.assertEqual(description_row.match_method, "description_exact_review")
        self.assertEqual(description_row.decision, SupplierPriceListRow.DECISION_REVIEW)

        unmatched = source_rows.get(supplier_code="DESCONOCIDO")
        self.assertEqual(unmatched.change_type, SupplierPriceListRow.CHANGE_UNMATCHED)
        self.assertIsNone(unmatched.matched_product)
        absent = batch.rows.get(row_type=SupplierPriceListRow.TYPE_ABSENT)
        self.assertEqual(absent.matched_product, self.missing)
        self.assertEqual(absent.decision, SupplierPriceListRow.DECISION_REVIEW)
        self.assertEqual(batch.summary["absent_rows"], 1)

    def test_apply_requires_resolved_reviews_and_writes_history_execution(self):
        batch = generate_supplier_price_list_preview(self._batch(), mapping=self._mapping())
        with self.assertRaisesMessage(ValidationError, "filas en revision"):
            apply_supplier_price_list(batch, user=self.user)

        decisions = {}
        for row in batch.rows.all():
            if row.decision != SupplierPriceListRow.DECISION_REVIEW:
                continue
            decisions[row.pk] = (
                SupplierPriceListRow.DECISION_APPLY
                if row.matched_product_id == self.description_match.pk
                else SupplierPriceListRow.DECISION_SKIP
            )
        update_row_decisions(batch, decisions)
        apply_supplier_price_list(batch, user=self.user)

        batch.refresh_from_db()
        self.assertEqual(batch.status, SupplierPriceListBatch.STATUS_APPLIED)
        self.assertIsNotNone(batch.import_execution_id)
        self.assertEqual(batch.import_execution.status, ImportExecution.STATUS_COMPLETED)
        self.first_offer.refresh_from_db()
        self.assertEqual(self.first_offer.current_cost, Decimal("120"))
        self.assertEqual(self.first_offer.discount_percentage, Decimal("10"))
        self.assertTrue(
            SupplierCostHistory.objects.filter(
                product_supplier=self.first_offer,
                import_execution=batch.import_execution,
                new_cost=Decimal("120"),
            ).exists()
        )
        self.assertTrue(ProductSupplier.objects.filter(product=self.second, supplier=self.supplier).exists())
        self.assertTrue(
            ProductSupplier.objects.filter(product=self.description_match, supplier=self.supplier).exists()
        )
        self.missing_offer.refresh_from_db()
        self.assertEqual(self.missing_offer.status, ProductSupplier.STATUS_ACTIVE)
        self.assertTrue(self.missing_offer.is_available)

    def test_stale_cost_aborts_the_whole_transaction(self):
        batch = generate_supplier_price_list_preview(self._batch(), mapping=self._mapping())
        review_decisions = {
            row.pk: SupplierPriceListRow.DECISION_SKIP
            for row in batch.rows.filter(decision=SupplierPriceListRow.DECISION_REVIEW)
        }
        update_row_decisions(batch, review_decisions)
        ProductSupplier.objects.filter(pk=self.first_offer.pk).update(current_cost=Decimal("111"))

        with self.assertRaisesMessage(ValidationError, "cambio desde la previsualizacion"):
            apply_supplier_price_list(batch, user=self.user)

        self.assertFalse(ProductSupplier.objects.filter(product=self.second, supplier=self.supplier).exists())
        batch.refresh_from_db()
        self.assertEqual(batch.status, SupplierPriceListBatch.STATUS_PREVIEWED)
        self.assertIsNone(batch.import_execution_id)

    def test_same_file_cannot_be_previewed_after_an_applied_batch(self):
        batch = generate_supplier_price_list_preview(self._batch(), mapping=self._mapping())
        update_row_decisions(
            batch,
            {
                row.pk: SupplierPriceListRow.DECISION_SKIP
                for row in batch.rows.filter(decision=SupplierPriceListRow.DECISION_REVIEW)
            },
        )
        apply_supplier_price_list(batch, user=self.user)
        duplicate = self._batch(digest=batch.file_sha256)
        with self.assertRaisesMessage(ValidationError, "mismo archivo"):
            generate_supplier_price_list_preview(duplicate, mapping=self._mapping())

    def test_xlsx_is_read_and_text_codes_keep_leading_zeroes(self):
        zero_product = Product.objects.create(
            sku="INT-007", name="Producto codigo cero", price="20", cost="10"
        )
        upsert_product_supplier_offer(
            product=zero_product,
            supplier=self.supplier,
            current_cost="10",
            supplier_code="0007",
            is_preferred=False,
            source="test_setup",
        )
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = "Precios"
        worksheet.append(["Codigo", "Descripcion", "Costo"])
        worksheet.append(["0007", "Producto codigo cero", 12.5])
        output = BytesIO()
        workbook.save(output)
        content = output.getvalue()
        batch = self._batch(content=content, name="lista.xlsx", digest="b" * 64)

        generate_supplier_price_list_preview(
            batch,
            mapping={
                "supplier_code": "Codigo",
                "description": "Descripcion",
                "cost": "Costo",
            },
            sheet_name="Precios",
        )

        row = batch.rows.get(row_type=SupplierPriceListRow.TYPE_SOURCE)
        self.assertEqual(row.supplier_code, "0007")
        self.assertEqual(row.matched_product, zero_product)
        self.assertEqual(row.proposed_cost, Decimal("12.5000"))

    def test_duplicate_product_rows_cannot_be_marked_for_application(self):
        content = (
            "codigo;sku;descripcion;costo\n"
            ";INT-002;Producto por SKU;80\n"
            ";INT-002;Producto por SKU;82\n"
        ).encode("utf-8")
        batch = generate_supplier_price_list_preview(
            self._batch(content=content, digest="c" * 64),
            mapping=self._mapping(),
        )
        duplicate = batch.rows.get(match_method="duplicate_source_product")
        self.assertEqual(duplicate.decision, SupplierPriceListRow.DECISION_REVIEW)
        with self.assertRaisesMessage(ValidationError, "decision no es valida"):
            update_row_decisions(
                batch,
                {duplicate.pk: SupplierPriceListRow.DECISION_APPLY},
            )

    def test_signature_detects_commercial_term_tampering(self):
        batch = generate_supplier_price_list_preview(self._batch(), mapping=self._mapping())
        update_row_decisions(
            batch,
            {
                row.pk: SupplierPriceListRow.DECISION_SKIP
                for row in batch.rows.filter(decision=SupplierPriceListRow.DECISION_REVIEW)
            },
        )
        coded = batch.rows.get(supplier_code="ABC-001")
        SupplierPriceListRow.objects.filter(pk=coded.pk).update(discount_percentage=Decimal("99"))
        with self.assertRaisesMessage(ValidationError, "previsualizacion cambio"):
            apply_supplier_price_list(batch, user=self.user)
        self.first_offer.refresh_from_db()
        self.assertEqual(self.first_offer.current_cost, Decimal("100"))

    def test_apply_view_requires_exact_confirmation(self):
        batch = generate_supplier_price_list_preview(self._batch(), mapping=self._mapping())
        update_row_decisions(
            batch,
            {
                row.pk: SupplierPriceListRow.DECISION_SKIP
                for row in batch.rows.filter(decision=SupplierPriceListRow.DECISION_REVIEW)
            },
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["active_company_id"] = self.company.pk
        session.save()
        url = reverse("admin_supplier_price_list_apply", kwargs={"batch_id": batch.pk})

        response = self.client.post(url, {"confirmation": "APLICAR"})
        self.assertEqual(response.status_code, 302)
        batch.refresh_from_db()
        self.assertEqual(batch.status, SupplierPriceListBatch.STATUS_PREVIEWED)

        response = self.client.post(url, {"confirmation": f"APLICAR LISTA {batch.pk}"})
        self.assertEqual(response.status_code, 302)
        batch.refresh_from_db()
        self.assertEqual(batch.status, SupplierPriceListBatch.STATUS_APPLIED)


class SupplierPriceListPermissionTests(TestCase):
    def setUp(self):
        self.company, _created = Company.objects.get_or_create(name="Empresa permisos Phase 2")
        self.user = User.objects.create_user("phase2_operator", password="secret", is_staff=True)
        AdminCompanyAccess.objects.create(user=self.user, company=self.company)
        self.profile = AdminCapabilityProfile.objects.create(
            user=self.user,
            capabilities=[CAP_RUN_IMPORTS],
            is_configured=True,
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["active_company_id"] = self.company.pk
        session.save()

    def test_both_capabilities_are_required(self):
        url = reverse("admin_supplier_price_list_batches")
        denied = self.client.get(url)
        self.assertEqual(denied.status_code, 302)

        self.profile.capabilities = [CAP_RUN_IMPORTS, CAP_MANAGE_PRODUCTS]
        self.profile.save(update_fields=["capabilities"])
        allowed = self.client.get(url)
        self.assertEqual(allowed.status_code, 200)
