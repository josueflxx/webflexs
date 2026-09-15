from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from accounts.models import ClientCompany, ClientProfile
from catalog.models import Product
from core.models import (
    DOCUMENT_SITUATION_APPROVED,
    DOCUMENT_SITUATION_OBSERVED,
    DocumentSeries,
    FISCAL_DOC_TYPE_FA,
    FISCAL_DOC_TYPE_FC,
    FISCAL_DOC_TYPE_NCB,
    FISCAL_ISSUE_MODE_ARCA_WSFE,
    FISCAL_STATUS_AUTHORIZED,
    FISCAL_STATUS_READY_TO_ISSUE,
    FISCAL_STATUS_SUBMITTING,
    SALES_BEHAVIOR_FACTURA,
    SALES_BEHAVIOR_NOTA_CREDITO,
    SALES_BEHAVIOR_PEDIDO,
    SALES_BEHAVIOR_REMITO,
    SALES_BILLING_MODE_INTERNAL_DOCUMENT,
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
from core.services.documents import ensure_document_for_order
from core.services.sales_documents import (
    apply_sales_document_type_to_internal_document,
    build_internal_document_display_items,
    update_document_situation,
)
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

        with patch(
            "core.services.fiscal_documents.get_vat_rate_parameter",
            return_value=SimpleNamespace(arca_id=5),
        ):
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

        self.invoice.transition_to(FISCAL_STATUS_SUBMITTING)
        self.invoice.transition_to(
            FISCAL_STATUS_AUTHORIZED,
            cae="12345678901234",
        )

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
        self.invoice.transition_to(FISCAL_STATUS_SUBMITTING)
        self.invoice.transition_to(
            FISCAL_STATUS_AUTHORIZED,
            cae="12345678901234",
        )
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
            status="draft",
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
        credit_note.transition_to(FISCAL_STATUS_READY_TO_ISSUE)
        credit_note.transition_to(FISCAL_STATUS_SUBMITTING)
        credit_note.transition_to(
            FISCAL_STATUS_AUTHORIZED,
            cae="22345678901234",
        )

        self._apply(credit_note, credit_type)
        self.tracked.refresh_from_db()
        self.untracked.refresh_from_db()
        self.assertEqual(self.tracked.stock, 9)
        self.assertEqual(self.untracked.stock, 20)


class ConfigurableMovementRulesTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Reglas de movimiento test")
        self.operator = User.objects.create_user(
            username="movement-rules-operator",
            is_staff=True,
        )
        self.client_user = User.objects.create_user(username="movement-rules-client")
        self.profile = ClientProfile.objects.create(
            user=self.client_user,
            company_name="Cliente reglas",
        )
        self.client_company = ClientCompany.objects.create(
            client_profile=self.profile,
            company=self.company,
        )
        self.order = Order.objects.create(
            user=self.client_user,
            company=self.company,
            client_company_ref=self.client_company,
            client_company=self.profile.company_name,
            status=Order.STATUS_SHIPPED,
        )

    def _type(self, **overrides):
        values = {
            "company": self.company,
            "code": "movement-rules-type",
            "name": "Movimiento configurable",
            "letter": "M",
            "document_behavior": SALES_BEHAVIOR_PEDIDO,
            "billing_mode": SALES_BILLING_MODE_INTERNAL_DOCUMENT,
            "internal_doc_type": DocumentSeries.DOC_PED,
            "generate_stock_movement": False,
            "generate_account_movement": False,
            "use_document_situation": True,
            "currency_code": "USD",
            "default_exchange_rate": Decimal("1200.000000"),
        }
        values.update(overrides)
        return SalesDocumentType.objects.create(**values)

    def test_document_freezes_rules_and_type_changes_only_affect_future_documents(self):
        document_type = self._type()
        document = ensure_document_for_order(
            self.order,
            doc_type=DocumentSeries.DOC_PED,
            sales_document_type=document_type,
            actor=self.operator,
        )

        self.assertEqual(document.sales_rules_snapshot["currency_code"], "USD")
        self.assertEqual(document.sales_rules_snapshot["default_exchange_rate"], "1200.000000")
        self.assertTrue(document.sales_rules_snapshot["use_document_situation"])
        frozen_version = document.sales_rules_version

        document_type.currency_code = "ARS"
        document_type.default_exchange_rate = Decimal("1.000000")
        document_type.use_document_situation = False
        document_type.save()
        document_type.refresh_from_db()
        document.refresh_from_db()

        self.assertGreater(document_type.rules_version, frozen_version)
        self.assertEqual(document.sales_rules_version, frozen_version)
        self.assertEqual(document.sales_rules_snapshot["currency_code"], "USD")
        self.assertTrue(document.sales_rules_snapshot["use_document_situation"])

        document_type.letter = "N"
        with self.assertRaisesMessage(ValidationError, "identidad de un tipo"):
            document_type.save()

    def test_situation_requires_observation_when_document_is_observed(self):
        document_type = self._type(code="movement-situation-type")
        document = ensure_document_for_order(
            self.order,
            doc_type=DocumentSeries.DOC_PED,
            sales_document_type=document_type,
            actor=self.operator,
        )

        with self.assertRaisesMessage(ValidationError, "Debes indicar una observacion"):
            update_document_situation(
                document=document,
                situation=DOCUMENT_SITUATION_OBSERVED,
                actor=self.operator,
            )

        update_document_situation(
            document=document,
            situation=DOCUMENT_SITUATION_APPROVED,
            actor=self.operator,
        )
        document.refresh_from_db()
        self.assertEqual(document.commercial_situation, DOCUMENT_SITUATION_APPROVED)
        self.assertEqual(document.commercial_situation_updated_by, self.operator)

    def test_disabled_stock_rule_stays_disabled_for_existing_document(self):
        product = Product.objects.create(
            sku="RULE-STOCK-1",
            name="Producto reglas stock",
            price=Decimal("100.00"),
            stock=8,
            tracks_stock=True,
        )
        OrderItem.objects.create(
            order=self.order,
            product=product,
            product_sku=product.sku,
            product_name=product.name,
            quantity=2,
            unit_price_base=product.price,
            price_at_purchase=product.price,
        )
        document_type = self._type(
            code="movement-stock-freeze-type",
            document_behavior=SALES_BEHAVIOR_REMITO,
            internal_doc_type=DocumentSeries.DOC_REM,
            generate_stock_movement=False,
        )
        document = ensure_document_for_order(
            self.order,
            doc_type=DocumentSeries.DOC_REM,
            sales_document_type=document_type,
            actor=self.operator,
        )
        self.assertFalse(StockMovement.objects.filter(internal_document=document).exists())

        document_type.generate_stock_movement = True
        document_type.save()
        apply_sales_document_type_to_internal_document(
            document=document,
            sales_document_type=document_type,
            actor=self.operator,
        )
        product.refresh_from_db()

        self.assertEqual(product.stock, 8)
        self.assertFalse(StockMovement.objects.filter(internal_document=document).exists())

    def test_internal_print_groups_equal_products_using_frozen_rule(self):
        product = Product.objects.create(
            sku="RULE-GROUP-1",
            name="Producto repetido",
            price=Decimal("125.00"),
        )
        for quantity in (1, 2):
            OrderItem.objects.create(
                order=self.order,
                product=product,
                product_sku=product.sku,
                product_name=product.name,
                quantity=quantity,
                unit_price_base=product.price,
                price_at_purchase=product.price,
            )
        document_type = self._type(
            code="movement-group-type",
            group_equal_products=True,
        )
        document = ensure_document_for_order(
            self.order,
            doc_type=DocumentSeries.DOC_PED,
            sales_document_type=document_type,
            actor=self.operator,
        )

        document_type.group_equal_products = False
        document_type.save()
        rows = build_internal_document_display_items(document)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["quantity"], Decimal("3"))
        self.assertEqual(rows[0]["subtotal"], Decimal("375.00"))
