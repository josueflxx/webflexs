from types import SimpleNamespace
from unittest.mock import patch

from django.contrib import admin
from django.test import RequestFactory, SimpleTestCase

from core.admin import (
    FiscalDocumentAdmin,
    FiscalDocumentItemAdmin,
    FiscalDocumentItemInline,
    FiscalDocumentSeriesAdmin,
    FiscalEmissionAttemptAdmin,
    FiscalEmissionAttemptInline,
    FiscalMutationAuditAdmin,
    FiscalPointOfSaleAdmin,
    FiscalSeriesReconciliationAdmin,
)
from core.models import (
    FiscalDocument,
    FiscalDocumentItem,
    FiscalDocumentSeries,
    FiscalEmissionAttempt,
    FiscalMutationAudit,
    FiscalPointOfSale,
    FiscalSeriesReconciliation,
)


class FiscalAdminImmutabilityTests(SimpleTestCase):
    def setUp(self):
        self.request = RequestFactory().get("/admin/")
        self.request.user = SimpleNamespace(
            is_active=True,
            is_staff=True,
            is_superuser=True,
            has_perm=lambda *args, **kwargs: True,
        )

    def assert_model_admin_is_read_only(self, model, admin_class):
        model_admin = admin_class(model, admin.site)

        self.assertFalse(model_admin.has_add_permission(self.request))
        self.assertFalse(model_admin.has_change_permission(self.request))
        self.assertFalse(model_admin.has_delete_permission(self.request))
        self.assertSetEqual(
            set(model_admin.get_readonly_fields(self.request)),
            {field.name for field in model._meta.concrete_fields},
        )

    def test_fiscal_evidence_admins_are_fully_read_only(self):
        read_only_admins = (
            (FiscalDocument, FiscalDocumentAdmin),
            (FiscalDocumentItem, FiscalDocumentItemAdmin),
            (FiscalEmissionAttempt, FiscalEmissionAttemptAdmin),
            (FiscalSeriesReconciliation, FiscalSeriesReconciliationAdmin),
            (FiscalMutationAudit, FiscalMutationAuditAdmin),
        )

        for model, admin_class in read_only_admins:
            with self.subTest(model=model.__name__):
                self.assert_model_admin_is_read_only(model, admin_class)

    def test_fiscal_evidence_models_are_registered(self):
        for model in (
            FiscalDocument,
            FiscalDocumentItem,
            FiscalEmissionAttempt,
            FiscalSeriesReconciliation,
            FiscalMutationAudit,
        ):
            with self.subTest(model=model.__name__):
                self.assertIn(model, admin.site._registry)

    def test_fiscal_document_inlines_are_fully_read_only(self):
        inline_definitions = (
            (FiscalDocumentItem, FiscalDocumentItemInline),
            (FiscalEmissionAttempt, FiscalEmissionAttemptInline),
        )

        for model, inline_class in inline_definitions:
            with self.subTest(model=model.__name__):
                inline = inline_class(FiscalDocument, admin.site)
                self.assertFalse(inline.has_add_permission(self.request))
                self.assertFalse(inline.has_change_permission(self.request))
                self.assertFalse(inline.has_delete_permission(self.request))
                self.assertFalse(inline.can_delete)
                self.assertEqual(inline.max_num, 0)
                self.assertSetEqual(
                    set(inline.get_readonly_fields(self.request)),
                    {field.name for field in model._meta.concrete_fields},
                )

    def test_point_of_sale_identity_is_editable_only_before_fiscal_usage(self):
        model_admin = FiscalPointOfSaleAdmin(FiscalPointOfSale, admin.site)
        point_of_sale = FiscalPointOfSale()

        with patch.object(model_admin, "_has_fiscal_usage", return_value=False):
            initial_fields = set(model_admin.get_readonly_fields(self.request, point_of_sale))
        with patch.object(model_admin, "_has_fiscal_usage", return_value=True):
            used_fields = set(model_admin.get_readonly_fields(self.request, point_of_sale))

        self.assertTrue(set(model_admin.IDENTITY_FIELDS).isdisjoint(initial_fields))
        self.assertTrue(set(model_admin.IDENTITY_FIELDS).issubset(used_fields))
        self.assertIn("created_at", initial_fields)
        self.assertIn("updated_at", initial_fields)

    def test_series_identity_and_number_freeze_after_fiscal_usage(self):
        model_admin = FiscalDocumentSeriesAdmin(FiscalDocumentSeries, admin.site)
        series = FiscalDocumentSeries()

        with patch.object(model_admin, "_has_fiscal_usage", return_value=False):
            initial_fields = set(model_admin.get_readonly_fields(self.request, series))
        with patch.object(model_admin, "_has_fiscal_usage", return_value=True):
            used_fields = set(model_admin.get_readonly_fields(self.request, series))

        identity_fields = set(model_admin.IDENTITY_AND_NUMBER_FIELDS)
        system_fields = set(model_admin.SYSTEM_MANAGED_FIELDS)
        self.assertTrue(identity_fields.isdisjoint(initial_fields))
        self.assertTrue(identity_fields.issubset(used_fields))
        self.assertTrue(system_fields.issubset(initial_fields))
        self.assertTrue(system_fields.issubset(used_fields))
