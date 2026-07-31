import base64
from datetime import date
from decimal import Decimal
import json
from urllib.parse import parse_qs, urlsplit
import uuid
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import ClientCompany, ClientProfile
from catalog.models import Product
from core.models import (
    AdminCompanyAccess,
    Company,
    FISCAL_ATTEMPT_OPERATION_AUTHORIZE,
    FISCAL_ATTEMPT_OPERATION_RECOVER,
    FISCAL_STATUS_AUTHORIZED,
    FISCAL_STATUS_READY_TO_ISSUE,
    FISCAL_STATUS_RECOVERED_AUTHORIZED,
    FISCAL_STATUS_REJECTED,
    FISCAL_STATUS_SUBMITTING,
    FISCAL_STATUS_UNCERTAIN,
    FiscalDocument,
    FiscalDocumentItem,
    FiscalEmissionAttempt,
    FiscalPointOfSale,
)
from core.services.arca_client import (
    ArcaConsultationResult,
    ArcaEmissionResult,
    ArcaTemporaryError,
)
from core.services.fiscal_documents import create_local_fiscal_document_from_order
from core.services.fiscal_emission import emit_fiscal_document_now
from core.services.fiscal_integrity import fiscal_payload_hash
from core.services.fiscal_recovery import recover_fiscal_document
from core.services.pdf_generator import generate_afip_qr_data
from orders.models import Order, OrderItem


class FiscalFixtureMixin:
    def setUp(self):
        super().setUp()
        self.company = Company.objects.create(
            name="Empresa Fiscal Readiness",
            legal_name="Emisor Historico SA",
            slug="fiscal-readiness",
            cuit="30693450239",
            email="fiscal@example.test",
            tax_condition="responsable_inscripto",
            fiscal_address="Calle Emisor 100",
            fiscal_city="San Martin",
            fiscal_province="Buenos Aires",
            postal_code="1650",
            activity_start_date=date(2020, 1, 2),
            point_of_sale_default="7",
        )
        self.homologation_pos = FiscalPointOfSale.objects.create(
            company=self.company,
            number="7",
            name="Homologacion",
            environment=FiscalPointOfSale.ENV_HOMOLOGATION,
            is_active=True,
            is_default=True,
        )
        self.production_pos = FiscalPointOfSale.objects.create(
            company=self.company,
            number="8",
            name="Produccion historica",
            environment=FiscalPointOfSale.ENV_PRODUCTION,
            is_active=True,
        )
        self._sequence = 0

    def _snapshot(self, *, point_of_sale, total="121.00"):
        return {
            "version": 2,
            "emitter": {
                "company_id": self.company.pk,
                "name": "Emisor Historico",
                "legal_name": "Emisor Historico SA",
                "cuit": "30693450239",
                "tax_condition": "responsable_inscripto",
                "tax_condition_label": "Responsable Inscripto",
                "fiscal_address": "Calle Emisor 100",
                "fiscal_city": "San Martin",
                "fiscal_province": "Buenos Aires",
                "postal_code": "1650",
                "email": "fiscal@example.test",
                "activity_start_date": "2020-01-02",
                "point_of_sale": point_of_sale.number,
                "environment": point_of_sale.environment,
            },
            "client": {
                "name": "Cliente Historico SA",
                "document_type": "cuit",
                "document_type_label": "CUIT",
                "document_number": "20123456786",
                "tax_condition": "responsable_inscripto",
                "tax_condition_label": "Responsable Inscripto",
                "fiscal_address": "Calle Cliente 200",
                "fiscal_city": "Rosario",
                "fiscal_province": "Santa Fe",
                "postal_code": "2000",
            },
            "operation": {
                "order_id": 987,
                "billing_mode": "official",
                "operator_id": 45,
                "operator_name": "Operador Historico",
                "notes": "Observacion congelada",
                "admin_notes": "",
                "discount_percentage": "0.00",
            },
            "items": [
                {
                    "line_number": 1,
                    "sku": "FISCAL-001",
                    "description": "Producto congelado",
                    "quantity": "1.000",
                    "unit_price_net": "100.00",
                    "discount_percentage": "0.00",
                    "discount_amount": "0.00",
                    "net_amount": "100.00",
                    "iva_rate": "21.00",
                    "arca_iva_id": 5,
                    "tax_treatment": "taxed",
                    "iva_amount": "21.00",
                    "total_amount": total,
                }
            ],
            "totals": {
                "subtotal_net": "100.00",
                "discount_total": "0.00",
                "tax_total": "21.00",
                "total": total,
            },
            "generation": {
                "doc_type": "FB",
                "issue_mode": "arca_wsfe",
            },
        }

    def _document(
        self,
        *,
        status=FISCAL_STATUS_READY_TO_ISSUE,
        point_of_sale=None,
        cae="",
        cae_due_date=None,
        snapshot_hash_valid=True,
        issue_mode="arca_wsfe",
        number=None,
    ):
        point_of_sale = point_of_sale or self.homologation_pos
        self._sequence += 1
        snapshot = self._snapshot(point_of_sale=point_of_sale)
        document = FiscalDocument.objects.create(
            source_key=f"test:fiscal:readiness:{self._sequence}",
            company=self.company,
            point_of_sale=point_of_sale,
            doc_type="FB",
            issue_mode=issue_mode,
            status="draft",
            subtotal_net=Decimal("100.00"),
            discount_total=Decimal("0.00"),
            tax_total=Decimal("21.00"),
            total=Decimal("121.00"),
            currency="ARS",
            exchange_rate=Decimal("1.000000"),
            fiscal_snapshot=snapshot,
            snapshot_schema_version=2,
            snapshot_hash=(
                fiscal_payload_hash(snapshot)
                if snapshot_hash_valid
                else "0" * 64
            ),
            prepared_at=timezone.now(),
            issuer_cuit_snapshot="30693450239",
            environment_snapshot=point_of_sale.environment,
            point_of_sale_number_snapshot=point_of_sale.number,
            receiver_iva_condition_id_snapshot=5,
            receiver_iva_condition_label_snapshot="Consumidor Final",
            receiver_iva_condition_source_snapshot="test",
            receiver_iva_condition_validated_at_snapshot=timezone.now(),
        )
        FiscalDocumentItem.objects.create(
            fiscal_document=document,
            line_number=1,
            sku="FISCAL-001",
            description="Producto congelado",
            quantity=Decimal("1.000"),
            unit_price_net=Decimal("100.00"),
            discount_percentage=Decimal("0.00"),
            discount_amount=Decimal("0.00"),
            net_amount=Decimal("100.00"),
            iva_rate=Decimal("21.00"),
            arca_iva_id=5,
            tax_treatment="taxed",
            iva_amount=Decimal("21.00"),
            total_amount=Decimal("121.00"),
        )
        if status == "draft":
            return document

        document.transition_to(FISCAL_STATUS_READY_TO_ISSUE)
        if status == FISCAL_STATUS_READY_TO_ISSUE:
            return document

        issued_at = timezone.now()
        document.transition_to(
            FISCAL_STATUS_SUBMITTING,
            number=number or 1,
            issued_at=issued_at,
            authorization_started_at=issued_at,
            payload_hash=fiscal_payload_hash(
                {
                    "snapshot_hash": document.snapshot_hash,
                    "number": number or 1,
                }
            ),
        )
        if status == FISCAL_STATUS_SUBMITTING:
            return document
        document.transition_to(
            status,
            cae=cae,
            cae_due_date=cae_due_date,
            resolved_at=timezone.now(),
        )
        return document


class FiscalPrintAndQrSafetyTests(FiscalFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.staff = User.objects.create_user(
            username="fiscal_print_admin",
            password="secret123",
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_login(self.staff)
        session = self.client.session
        session["active_company_id"] = self.company.pk
        session.save()

    def _print(self, document):
        return self.client.get(
            reverse("admin_fiscal_document_print", args=[document.pk])
        )

    def test_pending_document_never_looks_authorized(self):
        document = self._document(status=FISCAL_STATUS_READY_TO_ISSUE)

        response = self._print(document)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Comprobante no autorizado")
        self.assertNotContains(
            response,
            "<strong>Comprobante autorizado</strong>",
            html=True,
        )
        self.assertNotContains(response, "data:image/png;base64,")

    def test_authorized_status_without_cae_is_rendered_as_incomplete(self):
        document = self._document(
            status=FISCAL_STATUS_AUTHORIZED,
            point_of_sale=self.production_pos,
            cae="",
            cae_due_date=None,
        )

        response = self._print(document)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "AUTORIZACIÓN INCOMPLETA")
        self.assertContains(response, "Comprobante no autorizado")
        self.assertNotContains(
            response,
            "<strong>Comprobante autorizado</strong>",
            html=True,
        )

    def test_rejected_document_is_explicit_and_has_no_cae(self):
        document = self._document(
            status=FISCAL_STATUS_REJECTED,
            point_of_sale=self.production_pos,
        )

        response = self._print(document)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "COMPROBANTE RECHAZADO")
        self.assertContains(response, "Comprobante rechazado")
        self.assertContains(response, "CAE: no otorgado")
        self.assertNotContains(response, "data:image/png;base64,")

    def test_authorized_document_uses_persisted_cae_and_qr(self):
        document = self._document(
            status=FISCAL_STATUS_AUTHORIZED,
            point_of_sale=self.production_pos,
            cae="74123456789012",
            cae_due_date=date(2026, 8, 15),
            number=321,
        )

        response = self._print(document)

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "<strong>Comprobante autorizado</strong>",
            html=True,
        )
        self.assertContains(response, "74123456789012")
        self.assertContains(response, "15-08-2026")
        self.assertContains(response, "data:image/png;base64,")
        self.assertNotContains(response, "Comprobante no autorizado")

    def test_homologation_document_has_visible_no_validity_watermark(self):
        document = self._document(
            status=FISCAL_STATUS_AUTHORIZED,
            cae="74123456789012",
            cae_due_date=date(2026, 8, 15),
        )

        response = self._print(document)

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "HOMOLOGACIÓN – SIN VALIDEZ FISCAL",
        )

    def test_print_content_uses_snapshot_not_live_company_data(self):
        document = self._document(
            status=FISCAL_STATUS_AUTHORIZED,
            point_of_sale=self.production_pos,
            cae="74123456789012",
            cae_due_date=date(2026, 8, 15),
        )
        Company.objects.filter(pk=self.company.pk).update(
            legal_name="Nombre Vivo Modificado SA",
            fiscal_address="Domicilio vivo cambiado 999",
        )

        response = self._print(document)

        self.assertContains(response, "Emisor Historico SA")
        self.assertContains(response, "Calle Emisor 100")
        self.assertNotContains(response, "Nombre Vivo Modificado SA")
        self.assertNotContains(response, "Domicilio vivo cambiado 999")

    def test_qr_uses_only_persisted_fiscal_evidence(self):
        document = self._document(
            status=FISCAL_STATUS_AUTHORIZED,
            point_of_sale=self.production_pos,
            cae="74123456789012",
            cae_due_date=date(2026, 8, 15),
            number=321,
        )

        qr_url = generate_afip_qr_data(document)
        encoded = parse_qs(urlsplit(qr_url).query)["p"][0]
        payload = json.loads(base64.b64decode(encoded).decode("utf-8"))

        self.assertEqual(payload["cuit"], 30693450239)
        self.assertEqual(payload["ptoVta"], 8)
        self.assertEqual(payload["tipoCmp"], 6)
        self.assertEqual(payload["nroCmp"], 321)
        self.assertEqual(payload["importe"], 121)
        self.assertEqual(payload["nroDocRec"], 20123456786)
        self.assertEqual(payload["codAut"], 74123456789012)

    def test_qr_is_not_generated_without_complete_verified_authorization(self):
        pending = self._document(status=FISCAL_STATUS_READY_TO_ISSUE)
        incomplete = self._document(
            status=FISCAL_STATUS_AUTHORIZED,
            point_of_sale=self.production_pos,
        )
        invalid_snapshot = self._document(
            status=FISCAL_STATUS_AUTHORIZED,
            point_of_sale=self.production_pos,
            cae="74123456789012",
            cae_due_date=date(2026, 8, 15),
            snapshot_hash_valid=False,
            number=2,
        )

        self.assertEqual(generate_afip_qr_data(pending), "")
        self.assertEqual(generate_afip_qr_data(incomplete), "")
        self.assertEqual(generate_afip_qr_data(invalid_snapshot), "")


class FiscalIntegrityAndPermissionTests(FiscalFixtureMixin, TestCase):
    def _bare_document(self, *, source_key, **overrides):
        values = {
            "source_key": source_key,
            "company": self.company,
            "point_of_sale": self.production_pos,
            "doc_type": "FB",
            "issue_mode": "manual",
            "status": "draft",
            "issuer_cuit_snapshot": "30693450239",
            "environment_snapshot": FiscalPointOfSale.ENV_PRODUCTION,
            "point_of_sale_number_snapshot": self.production_pos.number,
        }
        values.update(overrides)
        return FiscalDocument.objects.create(**values)

    def test_duplicate_idempotency_key_is_rejected_by_database(self):
        key = uuid.uuid4()
        self._bare_document(source_key="test:idempotency:first", idempotency_key=key)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._bare_document(
                    source_key="test:idempotency:second",
                    idempotency_key=key,
                )

    def test_duplicate_fiscal_number_is_rejected_by_frozen_identity(self):
        self._bare_document(source_key="test:number:first", number=456)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._bare_document(source_key="test:number:second", number=456)

    def test_authorized_document_items_amounts_and_delete_are_immutable(self):
        document = self._document(
            status=FISCAL_STATUS_AUTHORIZED,
            point_of_sale=self.production_pos,
            cae="74123456789012",
            cae_due_date=date(2026, 8, 15),
        )
        item = document.items.get()

        document.total = Decimal("1.00")
        with self.assertRaises(ValidationError):
            document.save(update_fields=["total", "updated_at"])

        item.description = "Producto alterado"
        with self.assertRaises(ValidationError):
            item.save(update_fields=["description", "updated_at"])

        with self.assertRaises(ProtectedError):
            document.delete()

    def test_staff_without_fiscal_capability_cannot_queue_emission(self):
        document = self._document(status=FISCAL_STATUS_READY_TO_ISSUE)
        operator = User.objects.create_user(
            username="staff_without_fiscal_permission",
            password="secret123",
            is_staff=True,
        )
        AdminCompanyAccess.objects.create(user=operator, company=self.company)
        self.client.force_login(operator)
        session = self.client.session
        session["active_company_id"] = self.company.pk
        session.save()

        response = self.client.post(
            reverse("admin_fiscal_document_emit", args=[document.pk])
        )

        self.assertEqual(response.status_code, 403)
        document.refresh_from_db()
        self.assertIsNone(document.dispatch_requested_at)
        self.assertEqual(document.emission_attempts.count(), 0)


class FiscalLocalIdempotencyTests(FiscalFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            username="fiscal_customer",
            password="secret123",
        )
        self.profile = ClientProfile.objects.create(
            user=self.user,
            company_name="Cliente Idempotente",
            document_type="cuit",
            document_number="30693450239",
            iva_condition="responsable_inscripto",
            fiscal_address="Calle Cliente 200",
            fiscal_city="Rosario",
            fiscal_province="Santa Fe",
            postal_code="2000",
        )
        self.client_company = ClientCompany.objects.create(
            client_profile=self.profile,
            company=self.company,
            is_active=True,
        )
        self.product = Product.objects.create(
            sku="IDEMP-001",
            name="Producto Idempotente",
            price=Decimal("121.00"),
            cost=Decimal("50.00"),
            stock=10,
            is_active=True,
        )
        self.order = Order.objects.create(
            user=self.user,
            company=self.company,
            status=Order.STATUS_CONFIRMED,
            subtotal=Decimal("121.00"),
            total=Decimal("121.00"),
            client_company=self.profile.company_name,
            client_company_ref=self.client_company,
        )
        OrderItem.objects.create(
            order=self.order,
            product=self.product,
            product_sku=self.product.sku,
            product_name=self.product.name,
            quantity=1,
            unit_price_base=Decimal("121.00"),
            price_at_purchase=Decimal("121.00"),
            subtotal=Decimal("121.00"),
        )

    def test_same_local_request_returns_same_document(self):
        first, first_created = create_local_fiscal_document_from_order(
            order=self.order,
            company=self.company,
            doc_type="FB",
            point_of_sale=self.homologation_pos,
            issue_mode="manual",
            require_invoice_ready=False,
        )
        second, second_created = create_local_fiscal_document_from_order(
            order=self.order,
            company=self.company,
            doc_type="FB",
            point_of_sale=self.homologation_pos,
            issue_mode="manual",
            require_invoice_ready=False,
        )

        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            FiscalDocument.objects.filter(order=self.order, doc_type="FB").count(),
            1,
        )


class FiscalEmissionRecoveryTests(FiscalFixtureMixin, TestCase):
    def _ready_document(self):
        return self._document(status=FISCAL_STATUS_READY_TO_ISSUE)

    def _emission_patches(self):
        return (
            patch(
                "core.services.fiscal_emission.is_company_fiscal_ready",
                return_value=(True, []),
            ),
            patch(
                "core.services.fiscal_emission.sync_sales_document_type_counter"
            ),
            patch(
                "core.services.fiscal_emission.sync_fiscal_document_account_movement"
            ),
            patch(
                "core.services.fiscal_emission.ensure_stock_movements_for_order_document"
            ),
        )

    def test_pre_dispatch_failure_can_return_to_ready_without_second_document(self):
        class PredispatchFailureClient:
            def __init__(self, **kwargs):
                pass

            def fetch_last_authorized_number(self, **kwargs):
                return 0

            def emit_fiscal_document(self, **kwargs):
                raise ArcaTemporaryError(
                    "fallo antes del transporte",
                    possibly_sent=False,
                )

        document = self._ready_document()
        contexts = self._emission_patches()
        with contexts[0], contexts[1], contexts[2], contexts[3]:
            outcome = emit_fiscal_document_now(
                fiscal_document=document,
                client_factory=PredispatchFailureClient,
            )

        self.assertEqual(outcome.state, FISCAL_STATUS_READY_TO_ISSUE)
        document.refresh_from_db()
        attempt = document.emission_attempts.get()
        self.assertFalse(attempt.request_may_have_been_sent)
        self.assertIsNone(document.number)
        self.assertEqual(FiscalDocument.objects.count(), 1)

    def test_persistence_failure_after_simulated_authorization_keeps_boundary(self):
        class AuthorizedClient:
            def __init__(self, **kwargs):
                pass

            def fetch_last_authorized_number(self, **kwargs):
                return 0

            def emit_fiscal_document(self, *, mark_dispatched, **kwargs):
                mark_dispatched()
                return ArcaEmissionResult(
                    state="authorized",
                    cae="74123456789012",
                    cae_due_date=date(2026, 8, 15),
                    response_payload={"Resultado": "A"},
                )

        document = self._ready_document()
        contexts = self._emission_patches()
        with (
            contexts[0],
            contexts[1],
            contexts[2],
            contexts[3],
            patch.object(
                FiscalEmissionAttempt,
                "finalize",
                side_effect=RuntimeError("fallo persistiendo resultado simulado"),
            ),
        ):
            with self.assertRaises(RuntimeError):
                emit_fiscal_document_now(
                    fiscal_document=document,
                    client_factory=AuthorizedClient,
                )

        document.refresh_from_db()
        attempt = document.emission_attempts.get(
            operation=FISCAL_ATTEMPT_OPERATION_AUTHORIZE
        )
        document.series.refresh_from_db()
        self.assertEqual(document.status, FISCAL_STATUS_SUBMITTING)
        self.assertTrue(attempt.request_may_have_been_sent)
        self.assertEqual(attempt.result_status, "pending")
        self.assertIsNotNone(document.series.blocked_at)
        self.assertEqual(document.series.blocked_by_document_id, document.pk)

    def test_uncertain_result_is_query_only_and_recovers_without_reemission(self):
        class UncertainClient:
            def __init__(self, **kwargs):
                pass

            def fetch_last_authorized_number(self, **kwargs):
                return 0

            def emit_fiscal_document(self, *, mark_dispatched, **kwargs):
                mark_dispatched()
                return ArcaEmissionResult(
                    state="uncertain",
                    error_code="timeout",
                    error_message="resultado simulado incierto",
                )

        class RecoveryClient:
            def __init__(self, **kwargs):
                pass

            def consult_fiscal_document(self, **kwargs):
                return ArcaConsultationResult(
                    state="authorized",
                    cae="74123456789012",
                    cae_due_date=date(2026, 8, 15),
                    response_payload={"Resultado": "A"},
                )

        document = self._ready_document()
        contexts = self._emission_patches()
        with contexts[0], contexts[1], contexts[2], contexts[3]:
            emission = emit_fiscal_document_now(
                fiscal_document=document,
                client_factory=UncertainClient,
            )
            with self.assertRaises(ValidationError):
                emit_fiscal_document_now(
                    fiscal_document=emission.document,
                    client_factory=UncertainClient,
                )

        self.assertEqual(emission.state, FISCAL_STATUS_UNCERTAIN)
        self.assertEqual(
            FiscalEmissionAttempt.objects.filter(
                fiscal_document=document,
                operation=FISCAL_ATTEMPT_OPERATION_AUTHORIZE,
            ).count(),
            1,
        )

        with (
            patch(
                "core.services.fiscal_recovery.sync_sales_document_type_counter"
            ),
            patch(
                "core.services.fiscal_recovery.sync_fiscal_document_account_movement"
            ),
            patch(
                "core.services.fiscal_recovery.ensure_stock_movements_for_order_document"
            ),
        ):
            recovery = recover_fiscal_document(
                fiscal_document=emission.document,
                client_factory=RecoveryClient,
            )

        self.assertEqual(recovery.state, FISCAL_STATUS_RECOVERED_AUTHORIZED)
        recovery.document.refresh_from_db()
        self.assertEqual(recovery.document.cae, "74123456789012")
        self.assertEqual(
            FiscalEmissionAttempt.objects.filter(
                fiscal_document=document,
                operation=FISCAL_ATTEMPT_OPERATION_RECOVER,
            ).count(),
            1,
        )
        self.assertEqual(
            FiscalEmissionAttempt.objects.filter(
                fiscal_document=document,
                operation=FISCAL_ATTEMPT_OPERATION_AUTHORIZE,
            ).count(),
            1,
        )
