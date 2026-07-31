import base64
import hashlib
import json
import os
from io import BytesIO
from pathlib import Path
import shutil
import subprocess
import tempfile
from urllib.parse import parse_qs, urlsplit

import cv2
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from pypdf import PdfReader

from accounts.models import ClientCompany, ClientProfile
from catalog.models import Product
from core.models import (
    FISCAL_STATUS_AUTHORIZED,
    FISCAL_STATUS_READY_TO_ISSUE,
    FISCAL_STATUS_REJECTED,
    FISCAL_STATUS_SUBMITTING,
    FISCAL_STATUS_UNCERTAIN,
    FiscalDocument,
)
from core.services.fiscal_documents import create_local_fiscal_document_from_order
from core.services.pdf_generator import generate_afip_qr_data
from core.test_fiscal_readiness import FiscalFixtureMixin
from orders.models import Order, OrderItem


class FiscalPdfBinaryTests(FiscalFixtureMixin, TestCase):
    """Validate the final WeasyPrint bytes, not only template context."""

    simulated_cae = "74123456789012"

    def setUp(self):
        super().setUp()
        self.staff = User.objects.create_user(
            username="fiscal_pdf_binary_admin",
            password="local-test-only",
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_login(self.staff)
        session = self.client.session
        session["active_company_id"] = self.company.pk
        session.save()

    def _pdf(self, document, artifact_name=None):
        response = self.client.get(
            reverse("admin_fiscal_document_print", args=[document.pk]),
            {"copy": "original", "format": "pdf"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        pdf_bytes = bytes(response.content)
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
        self.assertGreater(len(pdf_bytes), 100)

        artifact_dir = os.environ.get("ARCA_PDF_ARTIFACT_DIR", "").strip()
        if artifact_name and artifact_dir:
            output_dir = Path(artifact_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / f"{artifact_name}.pdf").write_bytes(pdf_bytes)
        return pdf_bytes

    def _pdf_evidence(self, pdf_bytes):
        reader = PdfReader(BytesIO(pdf_bytes))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        return {
            "sha256": hashlib.sha256(pdf_bytes).hexdigest(),
            "text": text,
            "pages": len(reader.pages),
        }

    def _decode_qr_from_rendered_pdf(self, pdf_bytes):
        pdftoppm = (
            os.environ.get("PDFTOPPM_BINARY", "").strip()
            or shutil.which("pdftoppm")
        )
        self.assertTrue(pdftoppm, "Poppler pdftoppm is required for fiscal QR tests.")

        with tempfile.TemporaryDirectory(prefix="arca-pdf-qr-") as temp_dir:
            temp_path = Path(temp_dir)
            pdf_path = temp_path / "document.pdf"
            image_prefix = temp_path / "page"
            pdf_path.write_bytes(pdf_bytes)
            completed = subprocess.run(
                [
                    pdftoppm,
                    "-png",
                    "-r",
                    "300",
                    "-f",
                    "1",
                    "-singlefile",
                    str(pdf_path),
                    str(image_prefix),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(
                completed.returncode,
                0,
                completed.stderr or completed.stdout,
            )
            image = cv2.imread(str(image_prefix.with_suffix(".png")))
            self.assertIsNotNone(image)
            decoded, _points, _straight = cv2.QRCodeDetector().detectAndDecode(image)
            return decoded

    def _assert_not_authorized(self, document, expected_status, artifact_name):
        pdf_bytes = self._pdf(document, artifact_name)
        evidence = self._pdf_evidence(pdf_bytes)
        self.assertIn(expected_status, evidence["text"])
        self.assertIn("Comprobante no autorizado", evidence["text"])
        self.assertIn("CAE: no otorgado", evidence["text"])
        self.assertNotIn(self.simulated_cae, evidence["text"])
        self.assertEqual(self._decode_qr_from_rendered_pdf(pdf_bytes), "")
        return evidence

    def test_draft_pending_rejected_and_uncertain_pdfs_fail_closed(self):
        draft = self._document(status="draft")
        ready = self._document(status=FISCAL_STATUS_READY_TO_ISSUE)
        rejected = self._document(
            status=FISCAL_STATUS_REJECTED,
            point_of_sale=self.production_pos,
            number=1,
        )
        uncertain = self._document(
            status=FISCAL_STATUS_UNCERTAIN,
            point_of_sale=self.production_pos,
            number=2,
        )

        self._assert_not_authorized(draft, "Borrador", "case-a-draft")
        self._assert_not_authorized(
            ready,
            "Listo para emitir",
            "case-b-ready-to-issue",
        )

        rejected_pdf = self._pdf(rejected, "case-c-rejected")
        rejected_evidence = self._pdf_evidence(rejected_pdf)
        self.assertIn("COMPROBANTE RECHAZADO", rejected_evidence["text"])
        self.assertIn("Comprobante rechazado", rejected_evidence["text"])
        self.assertIn("CAE: no otorgado", rejected_evidence["text"])
        self.assertEqual(self._decode_qr_from_rendered_pdf(rejected_pdf), "")

        self._assert_not_authorized(
            uncertain,
            "Resultado incierto",
            "case-d-uncertain",
        )

    def test_authorized_pdf_uses_persisted_fiscal_evidence_and_decodable_qr(self):
        document = self._document(
            status=FISCAL_STATUS_AUTHORIZED,
            point_of_sale=self.production_pos,
            cae=self.simulated_cae,
            cae_due_date=timezone.datetime(2026, 8, 15).date(),
            number=321,
        )

        pdf_bytes = self._pdf(document, "case-e-authorized")
        evidence = self._pdf_evidence(pdf_bytes)
        expected_qr = generate_afip_qr_data(document)
        decoded_qr = self._decode_qr_from_rendered_pdf(pdf_bytes)

        self.assertIn("Comprobante autorizado", evidence["text"])
        self.assertIn(self.simulated_cae, evidence["text"])
        self.assertIn("15-08-2026", evidence["text"])
        self.assertIn("00008-00000321", evidence["text"])
        self.assertIn("Cliente Historico SA", evidence["text"])
        self.assertIn("Producto congelado", evidence["text"])
        self.assertIn("121,00", evidence["text"])
        self.assertEqual(decoded_qr, expected_qr)

        encoded = parse_qs(urlsplit(decoded_qr).query)["p"][0]
        payload = json.loads(base64.b64decode(encoded).decode("utf-8"))
        self.assertEqual(
            payload,
            {
                "ver": 1,
                "fecha": document.issued_at.date().isoformat(),
                "cuit": 30693450239,
                "ptoVta": 8,
                "tipoCmp": 6,
                "nroCmp": 321,
                "importe": 121,
                "moneda": "PES",
                "ctz": 1,
                "tipoDocRec": 80,
                "nroDocRec": 20123456786,
                "tipoCodAut": "E",
                "codAut": 74123456789012,
            },
        )

    def test_homologation_watermark_is_present_in_real_pdf_and_qr_decodes(self):
        document = self._document(
            status=FISCAL_STATUS_AUTHORIZED,
            point_of_sale=self.homologation_pos,
            cae=self.simulated_cae,
            cae_due_date=timezone.datetime(2026, 8, 15).date(),
            number=654,
        )

        pdf_bytes = self._pdf(document, "case-f-homologation")
        evidence = self._pdf_evidence(pdf_bytes)

        self.assertRegex(
            evidence["text"],
            r"HOMOLOGACIÓN – SIN VALIDEZ\s+FISCAL",
        )
        self.assertEqual(
            self._decode_qr_from_rendered_pdf(pdf_bytes),
            generate_afip_qr_data(document),
        )

    def _create_live_order(self):
        user = User.objects.create_user(
            username="historical_pdf_client",
            password="local-test-only",
        )
        profile = ClientProfile.objects.create(
            user=user,
            company_name="Cliente Historico SA",
            document_type="cuit",
            document_number="20123456786",
            iva_condition="responsable_inscripto",
            fiscal_address="Calle Cliente 200",
            fiscal_city="Rosario",
            fiscal_province="Santa Fe",
            postal_code="2000",
        )
        client_company = ClientCompany.objects.create(
            client_profile=profile,
            company=self.company,
            is_active=True,
        )
        product = Product.objects.create(
            sku="FISCAL-001",
            name="Producto congelado",
            price="100.00",
            cost="50.00",
            stock=10,
            is_active=True,
        )
        order = Order.objects.create(
            user=user,
            company=self.company,
            status=Order.STATUS_CONFIRMED,
            subtotal="100.00",
            total="100.00",
            client_company=profile.company_name,
            client_cuit=profile.document_number,
            client_address=profile.fiscal_address,
            client_company_ref=client_company,
        )
        OrderItem.objects.create(
            order=order,
            product=product,
            product_sku=product.sku,
            product_name=product.name,
            quantity=1,
            unit_price_base="100.00",
            price_at_purchase="100.00",
            subtotal="100.00",
        )
        return profile, product, order

    def test_authorized_pdf_remains_bound_to_historical_snapshot(self):
        profile, product, order = self._create_live_order()
        document, created = create_local_fiscal_document_from_order(
            order=order,
            company=self.company,
            doc_type="FB",
            point_of_sale=self.homologation_pos,
            issue_mode="manual",
            actor=self.staff,
            require_invoice_ready=False,
        )
        self.assertTrue(created)
        original_snapshot = document.fiscal_snapshot
        document.transition_to(
            FISCAL_STATUS_SUBMITTING,
            number=777,
            issued_at=timezone.now(),
            authorization_started_at=timezone.now(),
        )
        document.transition_to(
            FISCAL_STATUS_AUTHORIZED,
            cae=self.simulated_cae,
            cae_due_date=timezone.datetime(2026, 8, 15).date(),
            resolved_at=timezone.now(),
        )

        first_pdf = self._pdf(document, "snapshot-before-live-mutations")
        first = self._pdf_evidence(first_pdf)
        first_qr = self._decode_qr_from_rendered_pdf(first_pdf)

        ClientProfile.objects.filter(pk=profile.pk).update(
            company_name="Cliente Vivo Modificado SRL",
            fiscal_address="Domicilio vivo 999",
            iva_condition="consumidor_final",
        )
        Product.objects.filter(pk=product.pk).update(
            name="Producto vivo modificado",
            price="9999.99",
        )
        type(self.company).objects.filter(pk=self.company.pk).update(
            legal_name="Emisor Vivo Modificado SA",
            fiscal_address="Domicilio emisor vivo 999",
        )
        Order.objects.filter(pk=order.pk).update(
            client_company="Cliente vivo en pedido",
            client_address="Domicilio vivo en pedido",
            total="9999.99",
        )

        document.refresh_from_db()
        second_pdf = self._pdf(document, "snapshot-after-live-mutations")
        second = self._pdf_evidence(second_pdf)
        second_qr = self._decode_qr_from_rendered_pdf(second_pdf)

        self.assertEqual(document.fiscal_snapshot, original_snapshot)
        self.assertEqual(first["text"], second["text"])
        self.assertEqual(first["pages"], second["pages"])
        self.assertEqual(first_qr, second_qr)
        self.assertEqual(first_qr, generate_afip_qr_data(document))
        self.assertIn("Cliente Historico SA", second["text"])
        self.assertIn("Producto congelado", second["text"])
        self.assertIn("Emisor Historico SA", second["text"])
        self.assertNotIn("Cliente Vivo Modificado", second["text"])
        self.assertNotIn("Producto vivo modificado", second["text"])
        self.assertNotIn("Emisor Vivo Modificado", second["text"])
        self.assertNotIn("9999,99", second["text"])
