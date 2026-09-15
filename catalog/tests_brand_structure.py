from datetime import timedelta
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from admin_panel.forms.brand_structure_forms import BrandStructureForm
from catalog.models import (
    Brand, BrandCatalogRule, BrandRubro, BrandRubroProductOrder,
    BrandSubrubro, BrandSubrubroProductOrder, BrandStructureBatch,
    Category, CategoryBrandMapping, Product,
)
from catalog.services.brand_structure import apply_batch, prepare_batch, undo_batch
from core.services.company_context import get_default_company
from core.models import AdminCompanyAccess


class BrandStructureTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(username="josueflexs", password="test-only")
        cls.other = get_user_model().objects.create_user(username="structure-staff", is_staff=True)
        cls.ford = Brand.objects.create(name="Ford")
        cls.fiat = Brand.objects.create(name="Fiat")
        cls.inactive = Brand.objects.create(name="Inactiva", is_active=False)
        cls.rubro = BrandRubro.objects.create(brand=cls.ford, name="BUJES", order=8)
        cls.fiat_rubro = BrandRubro.objects.create(brand=cls.fiat, name="Bujes", order=12)
        cls.company = get_default_company()
        AdminCompanyAccess.objects.create(user=cls.other, company=cls.company)

    def data(self, **overrides):
        return {"scope": "selected", "brand_ids": [self.ford.pk, self.fiat.pk], "operation": "create_rubro", "names": "SOPORTES", "order": 3, "is_active": True, "observation": "Prueba de estructura", **overrides}

    def batch(self, **overrides):
        form = BrandStructureForm(self.data(**overrides))
        self.assertTrue(form.is_valid(), form.errors.as_json())
        return prepare_batch(form.cleaned_data, user=self.user)

    def apply(self, batch):
        return apply_batch(batch.pk, user=self.user)

    def login(self, user=None):
        self.client.force_login(user or self.user)
        session = self.client.session
        session["active_company_id"] = self.company.pk
        session.save()

    def test_preview_does_not_change_structure(self):
        before = BrandRubro.objects.count()
        batch = self.batch()
        self.assertEqual(batch.status, "preview")
        self.assertEqual(BrandRubro.objects.count(), before)
        self.assertEqual(batch.plan["counts"], {"create": 2, "update": 0, "skip": 0, "conflict": 0})

    def test_change_reason_is_optional_in_form_and_html(self):
        for reason in (None, "", "   "):
            with self.subTest(reason=reason):
                data = self.data(observation=reason)
                if reason is None:
                    data.pop("observation")
                form = BrandStructureForm(data)
                self.assertTrue(form.is_valid(), form.errors.as_json())
                self.assertEqual(form.cleaned_data["observation"], "")
                self.assertNotIn("required", form["observation"].as_widget())
                self.assertEqual(form["observation"].label, "Motivo del cambio (opcional)")

    def test_optional_change_reason_preserves_text_and_length_limit(self):
        form = BrandStructureForm(self.data(observation="  Unificar rubros  "))
        self.assertTrue(form.is_valid(), form.errors.as_json())
        batch = prepare_batch(form.cleaned_data, user=self.user)
        self.assertEqual(batch.observation, "Unificar rubros")
        form = BrandStructureForm(self.data(observation="a" * 301))
        self.assertFalse(form.is_valid())
        self.assertIn("observation", form.errors)

    def test_create_multiple_names_skips_existing_normalized_names(self):
        batch = self.batch(names="bujés\nSOPORTES\nPERNOS")
        self.assertEqual(batch.plan["counts"]["skip"], 2)
        self.apply(batch)
        self.assertEqual(BrandRubro.objects.filter(name__in=["SOPORTES", "PERNOS"]).count(), 4)
        self.rubro.refresh_from_db()
        self.assertEqual(self.rubro.order, 8)

    def test_all_scope_excludes_inactive_unless_requested(self):
        form = BrandStructureForm(self.data(scope="all", brand_ids=[]))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["resolved_brand_ids"], sorted(Brand.objects.filter(is_active=True).values_list("pk", flat=True)))
        form = BrandStructureForm(self.data(scope="all", include_inactive=True, brand_ids=[]))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIn(self.inactive.pk, form.cleaned_data["resolved_brand_ids"])

    def test_selected_inactive_requires_explicit_opt_in(self):
        form = BrandStructureForm(self.data(brand_ids=[self.inactive.pk]))
        self.assertFalse(form.is_valid())
        self.assertIn("brand_ids", form.errors)
        batch = self.batch(brand_ids=[self.inactive.pk], include_inactive=True)
        self.apply(batch)
        self.assertTrue(self.inactive.rubros.filter(name="SOPORTES").exists())

    def test_empty_scope_and_duplicate_names_are_rejected(self):
        for changes in ({"brand_ids": []}, {"names": "Pernos\nPÉRNOS"}, {"names": ""}, {"names": "\n".join(f"R{i}" for i in range(31))}):
            with self.subTest(changes=changes):
                self.assertFalse(BrandStructureForm(self.data(**changes)).is_valid())

    def test_plan_size_is_bounded(self):
        with patch("catalog.services.brand_structure.MAX_PLAN_ROWS", 1):
            with self.assertRaisesMessage(ValidationError, "supera"):
                self.batch()

    def test_subrubro_creation_skips_missing_parent(self):
        batch = self.batch(operation="create_subrubro", rubro_name="INEXISTENTE", names="DE GOMA", missing_parent="skip")
        self.assertEqual(batch.plan["counts"]["skip"], 2)
        with self.assertRaises(ValidationError):
            self.apply(batch)
        self.assertFalse(BrandSubrubro.objects.exists())

    def test_subrubro_creation_can_create_parents_and_undo_them(self):
        batch = self.batch(operation="create_subrubro", rubro_name="PERNOS", names="CORTOS\nLARGOS", missing_parent="create")
        self.assertEqual(batch.plan["counts"]["create"], 6)
        self.apply(batch)
        self.assertEqual(BrandSubrubro.objects.count(), 4)
        self.assertEqual(BrandRubro.objects.filter(name="PERNOS").count(), 2)
        undo_batch(batch.pk, user=self.user)
        self.assertFalse(BrandSubrubro.objects.exists())
        self.assertFalse(BrandRubro.objects.filter(name="PERNOS").exists())
        self.assertTrue(BrandStructureBatch.objects.filter(pk=batch.pk, status="undone").exists())

    def test_subrubro_creation_uses_normalized_existing_parent(self):
        batch = self.batch(operation="create_subrubro", rubro_name="bujés", names="DE GOMA", missing_parent="skip")
        self.apply(batch)
        self.assertEqual(set(BrandSubrubro.objects.values_list("brand_rubro_id", flat=True)), {self.rubro.pk, self.fiat_rubro.pk})

    def test_rename_preserves_ids_slugs_products_and_rules(self):
        product = Product.objects.create(sku="STRUCT-1", name="Buje", price=100)
        assignment = BrandRubroProductOrder.objects.create(brand_rubro=self.rubro, product=product, sort_order=7)
        rule = BrandCatalogRule.objects.create(brand=self.ford, brand_rubro=self.rubro, pattern="Buje")
        slug = self.rubro.slug
        batch = self.batch(operation="update_rubro", rubro_name="BUJES", change_name=True, new_name="BUJES Y ACCESORIOS")
        self.apply(batch)
        self.rubro.refresh_from_db()
        assignment.refresh_from_db()
        rule.refresh_from_db()
        self.assertEqual(self.rubro.name, "BUJES Y ACCESORIOS")
        self.assertEqual(self.rubro.slug, slug)
        self.assertEqual(self.rubro.order, 8)
        self.assertEqual(assignment.sort_order, 7)
        self.assertEqual(rule.brand_rubro_id, self.rubro.pk)
        undo_batch(batch.pk, user=self.user)
        self.rubro.refresh_from_db()
        self.assertEqual(self.rubro.name, "BUJES")
        self.assertTrue(BrandRubroProductOrder.objects.filter(pk=assignment.pk).exists())

    def test_edit_subrubro_preserves_helper_categories_and_products(self):
        sub = BrandSubrubro.objects.create(brand_rubro=self.rubro, name="GOMA")
        category = Category.objects.create(name="Ayudante")
        sub.helper_categories.add(category)
        product = Product.objects.create(sku="STRUCT-2", name="Goma", price=100)
        row = BrandSubrubroProductOrder.objects.create(brand_subrubro=sub, product=product, sort_order=5)
        batch = self.batch(operation="update_subrubro", rubro_name="BUJES", subrubro_name="GOMA", change_order=True, order=20, change_is_active=True, is_active=False)
        self.apply(batch)
        sub.refresh_from_db()
        self.assertEqual((sub.order, sub.is_active), (20, False))
        self.assertEqual(list(sub.helper_categories.values_list("pk", flat=True)), [category.pk])
        self.assertTrue(BrandSubrubroProductOrder.objects.filter(pk=row.pk).exists())
        self.assertEqual(batch.plan["counts"]["skip"], 1)

    def test_edit_requires_selected_fields(self):
        self.assertFalse(BrandStructureForm(self.data(operation="update_rubro", rubro_name="BUJES")).is_valid())
        self.assertFalse(BrandStructureForm(self.data(operation="update_rubro", rubro_name="BUJES", change_order=True, order="")).is_valid())

    def test_unselected_values_are_ignored(self):
        batch = self.batch(operation="update_rubro", rubro_name="BUJES", change_order=True, order=25, new_name="IGNORAR", is_active=False)
        self.apply(batch)
        self.rubro.refresh_from_db()
        self.assertEqual((self.rubro.name, self.rubro.is_active, self.rubro.order), ("BUJES", True, 25))

    def test_ambiguous_existing_names_block_whole_batch(self):
        BrandRubro.objects.create(brand=self.ford, name="bujés")
        batch = self.batch(operation="update_rubro", rubro_name="BUJES", change_order=True, order=10)
        self.assertEqual(batch.plan["counts"]["conflict"], 1)
        with self.assertRaises(ValidationError):
            self.apply(batch)
        self.fiat_rubro.refresh_from_db()
        self.assertEqual(self.fiat_rubro.order, 12)

    def test_rename_collision_blocks_all_brands(self):
        BrandRubro.objects.create(brand=self.ford, name="PERNOS")
        batch = self.batch(operation="update_rubro", rubro_name="BUJES", change_name=True, new_name="Pérnos")
        with self.assertRaises(ValidationError):
            self.apply(batch)
        self.assertFalse(BrandRubro.objects.filter(name="Pérnos").exists())

    def test_stale_preview_is_rejected(self):
        batch = self.batch()
        BrandRubro.objects.filter(pk=self.rubro.pk).update(order=99)
        with self.assertRaisesMessage(ValidationError, "cambió"):
            self.apply(batch)
        self.assertFalse(BrandRubro.objects.filter(name="SOPORTES").exists())

    def test_all_scope_is_frozen_at_preview(self):
        batch = self.batch(scope="all", brand_ids=[])
        new_brand = Brand.objects.create(name="Creada después")
        self.apply(batch)
        self.assertFalse(new_brand.rubros.exists())

    def test_expired_preview_is_rejected(self):
        batch = self.batch()
        BrandStructureBatch.objects.filter(pk=batch.pk).update(created_at=timezone.now() - timedelta(minutes=31))
        with self.assertRaisesMessage(ValidationError, "venció"):
            self.apply(batch)

    def test_deleted_brand_invalidates_preview(self):
        batch = self.batch()
        self.fiat.delete()
        with self.assertRaisesMessage(ValidationError, "selección"):
            self.apply(batch)

    def test_only_author_can_confirm_and_retry_is_idempotent(self):
        batch = self.batch()
        with self.assertRaises(ValidationError):
            apply_batch(batch.pk, user=self.other)
        self.apply(batch)
        self.apply(batch)
        self.assertEqual(BrandRubro.objects.filter(name="SOPORTES").count(), 2)

    def test_apply_is_atomic_on_validation_failure(self):
        batch = self.batch()
        original = BrandRubro.full_clean

        def fail_second(obj, *args, **kwargs):
            if obj.brand_id == self.fiat.pk:
                raise ValidationError("Falla de prueba")
            return original(obj, *args, **kwargs)

        with patch.object(BrandRubro, "full_clean", fail_second):
            with self.assertRaises(ValidationError):
                self.apply(batch)
        self.assertFalse(BrandRubro.objects.filter(name="SOPORTES").exists())
        batch.refresh_from_db()
        self.assertEqual(batch.status, "preview")

    def test_undo_created_rubros_without_use(self):
        batch = self.batch()
        self.apply(batch)
        undo_batch(batch.pk, user=self.user)
        self.assertFalse(BrandRubro.objects.filter(name="SOPORTES").exists())
        with self.assertRaises(ValidationError):
            self.apply(batch)

    def test_undo_blocks_products_and_preserves_entire_batch(self):
        batch = self.batch()
        self.apply(batch)
        rubro = self.ford.rubros.get(name="SOPORTES")
        product = Product.objects.create(sku="STRUCT-3", name="Soporte", price=100)
        BrandRubroProductOrder.objects.create(brand_rubro=rubro, product=product)
        with self.assertRaisesMessage(ValidationError, "productos"):
            undo_batch(batch.pk, user=self.user)
        self.assertEqual(BrandRubro.objects.filter(name="SOPORTES").count(), 2)
        self.assertTrue(product.brand_rubros.filter(pk=rubro.pk).exists())

    def test_undo_blocks_new_children(self):
        batch = self.batch()
        self.apply(batch)
        BrandSubrubro.objects.create(brand_rubro=self.ford.rubros.get(name="SOPORTES"), name="NUEVO")
        with self.assertRaisesMessage(ValidationError, "subrubros"):
            undo_batch(batch.pk, user=self.user)

    def test_undo_blocks_rules_and_category_mappings(self):
        batch = self.batch()
        self.apply(batch)
        rubro = self.ford.rubros.get(name="SOPORTES")
        rule = BrandCatalogRule.objects.create(brand=self.ford, brand_rubro=rubro, pattern="Soporte")
        with self.assertRaises(ValidationError):
            undo_batch(batch.pk, user=self.user)
        rule.delete()
        category = Category.objects.create(name="Vinculada")
        CategoryBrandMapping.objects.create(source_category=category, brand=self.ford, brand_rubro=rubro, observation="Prueba")
        with self.assertRaises(ValidationError):
            undo_batch(batch.pk, user=self.user)

    def test_undo_blocks_later_edits(self):
        batch = self.batch(operation="update_rubro", rubro_name="BUJES", change_order=True, order=10)
        self.apply(batch)
        BrandRubro.objects.filter(pk=self.rubro.pk).update(order=11)
        with self.assertRaisesMessage(ValidationError, "posteriores"):
            undo_batch(batch.pk, user=self.user)
        self.fiat_rubro.refresh_from_db()
        self.assertEqual(self.fiat_rubro.order, 10)

    def test_undo_blocks_new_occupant_of_old_name(self):
        batch = self.batch(operation="update_rubro", rubro_name="BUJES", change_name=True, new_name="BUJES NUEVOS")
        self.apply(batch)
        BrandRubro.objects.create(brand=self.ford, name="bujés")
        with self.assertRaisesMessage(ValidationError, "ocupado"):
            undo_batch(batch.pk, user=self.user)

    def test_image_uploaded_once_and_reused_on_regeneration(self):
        with TemporaryDirectory(prefix="flexs-structure-test-") as directory, override_settings(MEDIA_ROOT=directory):
            buffer = BytesIO()
            Image.new("RGB", (4, 4), color="red").save(buffer, format="PNG")
            upload = SimpleUploadedFile("sample.png", buffer.getvalue(), content_type="image/png")
            form = BrandStructureForm(self.data(), {"image": upload})
            self.assertTrue(form.is_valid(), form.errors)
            batch = prepare_batch(form.cleaned_data, user=self.user)
            self.assertEqual(len(list(Path(directory).rglob("*.png"))), 1)
            form = BrandStructureForm(self.data(), previous=batch)
            self.assertTrue(form.is_valid(), form.errors)
            regenerated = prepare_batch(form.cleaned_data, user=self.user, previous=batch)
            self.apply(regenerated)
            self.assertEqual(set(BrandRubro.objects.filter(name="SOPORTES").values_list("image", flat=True)), {batch.image.name})
            self.assertEqual(len(list(Path(directory).rglob("*.png"))), 1)

    def test_image_edit_requires_explicit_upload_or_removal(self):
        form = BrandStructureForm(self.data(operation="update_rubro", rubro_name="BUJES", change_image=True))
        self.assertFalse(form.is_valid())
        BrandRubro.objects.filter(pk=self.rubro.pk).update(image="existing.png")
        batch = self.batch(operation="update_rubro", rubro_name="BUJES", change_image=True, remove_image=True)
        self.apply(batch)
        self.rubro.refresh_from_db()
        self.assertFalse(self.rubro.image)
        undo_batch(batch.pk, user=self.user)
        self.rubro.refresh_from_db()
        self.assertEqual(self.rubro.image.name, "existing.png")

    def test_non_images_rejected(self):
        form = BrandStructureForm(self.data(), {"image": SimpleUploadedFile("bad.svg", b"<svg></svg>", content_type="image/svg+xml")})
        self.assertFalse(form.is_valid())

    def test_manager_and_brand_link_render_without_writes(self):
        self.login()
        self.assertContains(self.client.get(reverse("admin_brand_structure")), "Rubros y subrubros en lote")
        self.assertContains(self.client.get(reverse("admin_brand_list")), reverse("admin_brand_structure"))
        self.assertFalse(BrandStructureBatch.objects.exists())

    def test_http_preview_confirm_history_and_undo(self):
        self.login()
        response = self.client.post(reverse("admin_brand_structure"), self.data())
        batch = BrandStructureBatch.objects.get()
        detail = reverse("admin_brand_structure_batch", args=[batch.pk])
        self.assertRedirects(response, detail)
        self.assertContains(self.client.get(detail), "Vista previa por marca")
        apply_url = reverse("admin_brand_structure_apply", args=[batch.pk])
        self.client.post(apply_url)
        batch.refresh_from_db()
        self.assertEqual(batch.status, "preview")
        self.assertRedirects(self.client.post(apply_url, {"confirm_scope": "on"}), detail)
        self.assertContains(self.client.get(reverse("admin_brand_structure")), "Aplicado")
        self.assertContains(self.client.get(detail), "Deshacer este lote")
        undo_url = reverse("admin_brand_structure_undo", args=[batch.pk])
        self.client.post(undo_url)
        batch.refresh_from_db()
        self.assertEqual(batch.status, "applied")
        self.assertRedirects(self.client.post(undo_url, {"confirm_undo": "on"}), detail)
        self.assertFalse(BrandRubro.objects.filter(name="SOPORTES").exists())

    def test_http_subrubro_batch_without_reason_keeps_audit_and_undo(self):
        self.login()
        response = self.client.post(reverse("admin_brand_structure"), self.data(
            operation="create_subrubro", rubro_name="BUJES", names="BUJE ARMADO",
            missing_parent="skip", observation="",
        ))
        batch = BrandStructureBatch.objects.get()
        detail = reverse("admin_brand_structure_batch", args=[batch.pk])
        self.assertRedirects(response, detail)
        self.assertEqual(batch.observation, "")
        self.assertEqual(batch.created_by, self.user)
        self.assertIsNotNone(batch.created_at)
        self.assertRedirects(self.client.post(
            reverse("admin_brand_structure_apply", args=[batch.pk]), {"confirm_scope": "on"},
        ), detail)
        batch.refresh_from_db()
        self.assertEqual(batch.status, "applied")
        self.assertEqual(len(batch.changes), 2)
        self.assertIsNotNone(batch.applied_at)
        self.assertEqual(BrandSubrubro.objects.filter(name="BUJE ARMADO").count(), 2)
        self.assertContains(self.client.get(reverse("admin_brand_structure")), "Aplicado")
        self.assertRedirects(self.client.post(
            reverse("admin_brand_structure_undo", args=[batch.pk]), {"confirm_undo": "on"},
        ), detail)
        batch.refresh_from_db()
        self.assertEqual(batch.status, "undone")
        self.assertEqual(batch.undone_by, self.user)
        self.assertFalse(BrandSubrubro.objects.filter(name="BUJE ARMADO").exists())

    def test_state_changes_require_post_and_permissions(self):
        batch = self.batch()
        self.login()
        for route in ("admin_brand_structure_apply", "admin_brand_structure_undo"):
            self.assertEqual(self.client.get(reverse(route, args=[batch.pk])).status_code, 405)
        self.login(self.other)
        self.client.post(reverse("admin_brand_structure"), self.data())
        self.client.post(reverse("admin_brand_structure_apply", args=[batch.pk]), {"confirm_scope": "on"})
        self.assertEqual(BrandStructureBatch.objects.count(), 1)
        self.assertFalse(BrandRubro.objects.filter(name="SOPORTES").exists())

    def test_staff_can_read_but_cannot_prepare_or_undo(self):
        batch = self.batch()
        self.apply(batch)
        self.login(self.other)
        response = self.client.get(reverse("admin_brand_structure"))
        self.assertContains(response, "Tenés acceso de consulta")
        self.assertContains(self.client.get(reverse("admin_brand_structure_batch", args=[batch.pk])), "Detalle del lote original")
        self.client.post(reverse("admin_brand_structure_undo", args=[batch.pk]), {"confirm_undo": "on"})
        batch.refresh_from_db()
        self.assertEqual(batch.status, "applied")

    def test_preview_post_is_csrf_protected(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        session = client.session
        session["active_company_id"] = self.company.pk
        session.save()
        self.assertEqual(client.post(reverse("admin_brand_structure"), self.data()).status_code, 403)
        self.assertFalse(BrandStructureBatch.objects.exists())

    def test_drafts_are_private_and_regeneration_preserves_selection(self):
        batch = self.batch(brand_ids=[self.ford.pk])
        detail = reverse("admin_brand_structure_batch", args=[batch.pk])
        edit = reverse("admin_brand_structure") + f"?draft={batch.pk}"
        self.login(self.other)
        self.assertEqual(self.client.get(detail).status_code, 404)
        self.assertEqual(self.client.get(edit).status_code, 404)
        self.login()
        response = self.client.get(edit)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_brand_ids"], {str(self.ford.pk)})
        self.assertEqual(self.client.get(reverse("admin_brand_structure") + "?draft=bad").status_code, 404)

    def test_expired_and_conflicting_previews_render_without_apply_button(self):
        self.login()
        batch = self.batch()
        BrandStructureBatch.objects.filter(pk=batch.pk).update(created_at=timezone.now() - timedelta(minutes=31))
        response = self.client.get(reverse("admin_brand_structure_batch", args=[batch.pk]))
        self.assertContains(response, "venció")
        self.assertNotContains(response, "Aplicar lote en")
        BrandRubro.objects.create(brand=self.ford, name="bujés")
        batch = self.batch(names="BUJES")
        response = self.client.get(reverse("admin_brand_structure_batch", args=[batch.pk]))
        self.assertContains(response, "Conflicto")
        self.assertNotContains(response, "Aplicar lote en")
