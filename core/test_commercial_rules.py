from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from accounts.models import ClientCompany, ClientProfile
from catalog.models import Product
from core.models import (
    FISCAL_DOC_TYPE_FA,
    FISCAL_DOC_TYPE_FC,
    FISCAL_DOC_TYPE_NCB,
    FISCAL_ISSUE_MODE_ARCA_WSFE,
    FISCAL_STATUS_AUTHORIZED,
    FISCAL_STATUS_READY_TO_ISSUE,
    SALES_BEHAVIOR_FACTURA,
    SALES_BEHAVIOR_NOTA_CREDITO,
    SALES_BILLING_MODE_AFIP_WSFE,
    Company,
    FiscalDocument,
    FiscalDocumentItem,
    FiscalPointOfSale,
    SalesDocumentType,
    StockMovement,
)
from core.services.fiscal_documents import (
    _build_order_items_payload,
    create_local_fiscal_document_from_order,
)
from core.services.sales_documents import ensure_stock_movements_for_order_document
from orders.models import Order, OrderItem


class ElectronicInvoiceTaxRulesTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Reglas fiscales test")
        self.point_of_sale = FiscalPointOfSale.objects.create(
            company=self.company,
            number="1",
            is_default=True,
        )
        user = User.objects.create_user(username="tax-rules-client")
        profile = ClientProfile.objects.create(user=user, company_name="Cliente IVA")
        client_company = ClientCompany.objects.create(
            client_profile=profile,
            company=self.company,
        )
        self.order = Order.objects.create(
            user=user,
            company=self.company,
            client_company_ref=client_company,
            client_company=profile.company_name,
        )

    def _add_product(self, *, sku="IVA-21", iva_rate=Decimal("21.00")):
        product = Product.objects.create(
            sku=sku,
            name=f"Producto {sku}",
            cost=Decimal("60.00"),
            price=Decimal("100.00"),
            iva_rate=iva_rate,
        )
        return OrderItem.objects.create(
            order=self.order,
            product=product,
            product_sku=product.sku,
            product_name=product.name,
            quantity=2,
            unit_price_base=Decimal("100.00"),
            price_at_purchase=Decimal("100.00"),
        )

    @override_settings(
        FISCAL_AUTO_ITEM_TAX_ENABLED=True,
        FISCAL_ITEM_TAX_CALCULATION_MODE="net",
    )
    def test_electronic_invoice_adds_selected_iva_to_net_catalog_price(self):
        item = self._add_product()

        payload = _build_order_items_payload(
            self.order,
            doc_type=FISCAL_DOC_TYPE_FA,
            issue_mode=FISCAL_ISSUE_MODE_ARCA_WSFE,
        )

        self.assertEqual(item.iva_rate_snapshot, Decimal("21.00"))
        self.assertEqual(payload[0]["net_amount"], Decimal("200.00"))
        self.assertEqual(payload[0]["iva_amount"], Decimal("42.00"))
        self.assertEqual(payload[0]["total_amount"], Decimal("242.00"))

    @override_settings(FISCAL_AUTO_ITEM_TAX_ENABLED=True)
    def test_electronic_invoice_rejects_product_without_selected_iva(self):
        self._add_product(sku="IVA-MISSING", iva_rate=None)

        with self.assertRaisesMessage(ValidationError, "no tiene alicuota de IVA"):
            _build_order_items_payload(
                self.order,
                doc_type=FISCAL_DOC_TYPE_FA,
                issue_mode=FISCAL_ISSUE_MODE_ARCA_WSFE,
            )

    def test_arca_mode_rejects_types_outside_invoice_and_credit_note_a_b(self):
        with self.assertRaisesMessage(ValidationError, "solamente Factura A/B"):
            create_local_fiscal_document_from_order(
                order=self.order,
                company=self.company,
                doc_type=FISCAL_DOC_TYPE_FC,
                point_of_sale=self.point_of_sale,
                issue_mode=FISCAL_ISSUE_MODE_ARCA_WSFE,
                require_invoice_ready=False,
            )


class StockAfterCaeRulesTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Stock CAE test")
        self.point_of_sale = FiscalPointOfSale.objects.create(
            company=self.company,
            number="2",
        )
        user = User.objects.create_user(username="stock-rules-client")
        profile = ClientProfile.objects.create(user=user, company_name="Cliente Stock")
        client_company = ClientCompany.objects.create(
            client_profile=profile,
            company=self.company,
        )
        self.order = Order.objects.create(
            user=user,
            company=self.company,
            client_company_ref=client_company,
            client_company=profile.company_name,
        )
        self.tracked = Product.objects.create(
            sku="STOCK-ON",
            name="Con stock",
            cost=Decimal("50.00"),
            price=Decimal("100.00"),
            iva_rate=Decimal("21.00"),
            stock=10,
            tracks_stock=True,
        )
        self.untracked = Product.objects.create(
            sku="STOCK-OFF",
            name="Sin control de stock",
            cost=Decimal("50.00"),
            price=Decimal("100.00"),
            iva_rate=Decimal("21.00"),
            stock=20,
            tracks_stock=False,
        )
        for product in (self.tracked, self.untracked):
            OrderItem.objects.create(
                order=self.order,
                product=product,
                product_sku=product.sku,
                product_name=product.name,
                quantity=2,
                unit_price_base=product.price,
                price_at_purchase=product.price,
            )
        self.invoice_type = SalesDocumentType.objects.create(
            company=self.company,
            code="factura-electronica-test",
            name="Factura electronica test",
            document_behavior=SALES_BEHAVIOR_FACTURA,
            billing_mode=SALES_BILLING_MODE_AFIP_WSFE,
            fiscal_doc_type=FISCAL_DOC_TYPE_FA,
            generate_stock_movement=True,
        )
        self.invoice = FiscalDocument.objects.create(
            source_key="stock-cae-invoice-test",
            company=self.company,
            order=self.order,
            point_of_sale=self.point_of_sale,
            sales_document_type=self.invoice_type,
            doc_type=FISCAL_DOC_TYPE_FA,
            status=FISCAL_STATUS_READY_TO_ISSUE,
        )

    def _apply(self, document, document_type):
        return ensure_stock_movements_for_order_document(
            order=self.order,
            company=self.company,
            sales_document_type=document_type,
            fiscal_document=document,
        )

    def test_stock_changes_only_after_cae_and_only_for_opted_in_products(self):
        self.assertEqual(self._apply(self.invoice, self.invoice_type), [])
        self.tracked.refresh_from_db()
        self.assertEqual(self.tracked.stock, 10)

        self.invoice.status = FISCAL_STATUS_AUTHORIZED
        self.invoice.cae = "12345678901234"
        self.invoice.save(update_fields=["status", "cae", "updated_at"])

        self.assertEqual(len(self._apply(self.invoice, self.invoice_type)), 1)
        self.tracked.refresh_from_db()
        self.untracked.refresh_from_db()
        self.assertEqual(self.tracked.stock, 8)
        self.assertEqual(self.untracked.stock, 20)
        self.assertEqual(StockMovement.objects.count(), 1)

        self._apply(self.invoice, self.invoice_type)
        self.tracked.refresh_from_db()
        self.assertEqual(self.tracked.stock, 8)
        self.assertEqual(StockMovement.objects.count(), 1)

    def test_authorized_credit_note_replenishes_stock(self):
        self.invoice.status = FISCAL_STATUS_AUTHORIZED
        self.invoice.cae = "12345678901234"
        self.invoice.save(update_fields=["status", "cae", "updated_at"])
        self._apply(self.invoice, self.invoice_type)

        credit_type = SalesDocumentType.objects.create(
            company=self.company,
            code="nota-credito-electronica-test",
            name="Nota credito electronica test",
            document_behavior=SALES_BEHAVIOR_NOTA_CREDITO,
            billing_mode=SALES_BILLING_MODE_AFIP_WSFE,
            fiscal_doc_type=FISCAL_DOC_TYPE_NCB,
            generate_stock_movement=True,
        )
        credit_note = FiscalDocument.objects.create(
            source_key="stock-cae-credit-note-test",
            company=self.company,
            order=self.order,
            point_of_sale=self.point_of_sale,
            sales_document_type=credit_type,
            doc_type=FISCAL_DOC_TYPE_NCB,
            status=FISCAL_STATUS_AUTHORIZED,
            cae="22345678901234",
        )
        FiscalDocumentItem.objects.create(
            fiscal_document=credit_note,
            line_number=1,
            product=self.tracked,
            sku=self.tracked.sku,
            description=self.tracked.name,
            quantity=Decimal("1.000"),
            unit_price_net=Decimal("100.00"),
            net_amount=Decimal("100.00"),
            iva_rate=Decimal("21.00"),
            iva_amount=Decimal("21.00"),
            total_amount=Decimal("121.00"),
        )

        self._apply(credit_note, credit_type)
        self.tracked.refresh_from_db()
        self.untracked.refresh_from_db()
        self.assertEqual(self.tracked.stock, 9)
        self.assertEqual(self.untracked.stock, 20)
