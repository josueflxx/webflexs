from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import ClientCompany, ClientProfile, ClientTask
from core.models import AdminAuditLog, AdminCompanyAccess, Company
from core.services.company_context import SESSION_COMPANY_KEY


class ClientTaskUiTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="task_admin",
            email="task-admin@example.com",
            password="test-password",
        )
        self.operator = User.objects.create_user(
            username="task_operator",
            password="test-password",
            is_staff=True,
            first_name="Operador",
        )
        self.company = Company.objects.create(name="Empresa tareas")
        self.other_company = Company.objects.create(name="Otra empresa tareas")
        AdminCompanyAccess.objects.create(
            user=self.operator,
            company=self.company,
            is_active=True,
        )
        self.client_user = User.objects.create_user(username="client_task_user")
        self.profile = ClientProfile.objects.create(
            user=self.client_user,
            company_name="Cliente con tareas",
            commercial_observation="Prefiere contacto por la tarde.",
        )
        ClientCompany.objects.create(
            client_profile=self.profile,
            company=self.company,
            is_active=True,
        )
        ClientCompany.objects.create(
            client_profile=self.profile,
            company=self.other_company,
            is_active=True,
        )
        self.client.force_login(self.admin)
        session = self.client.session
        session[SESSION_COMPANY_KEY] = self.company.pk
        session.save()

    def _due_value(self):
        return timezone.localtime(timezone.now() + timedelta(days=1)).strftime(
            "%Y-%m-%dT%H:%M"
        )

    def _create_task(self, **overrides):
        values = {
            "client_profile": self.profile,
            "company": self.company,
            "assigned_to": self.admin,
            "created_by": self.admin,
            "title": "Seguimiento de presupuesto",
            "due_at": timezone.now() + timedelta(days=1),
        }
        values.update(overrides)
        return ClientTask.objects.create(**values)

    def test_create_task_is_scoped_and_audited(self):
        response = self.client.post(
            reverse("admin_client_task_create", args=[self.profile.pk]),
            {
                "company_id": self.company.pk,
                "title": "Confirmar entrega",
                "note": "Llamar antes de despachar.",
                "due_at": self._due_value(),
                "priority": ClientTask.PRIORITY_HIGH,
                "assigned_to": self.operator.pk,
                "scope": "all",
            },
        )

        task = ClientTask.objects.get()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(task.company, self.company)
        self.assertEqual(task.assigned_to, self.operator)
        self.assertEqual(task.priority, ClientTask.PRIORITY_HIGH)
        audit = AdminAuditLog.objects.get(action="client_task_create")
        self.assertEqual(audit.target_id, str(task.pk))
        self.assertEqual(audit.details["client_profile_id"], self.profile.pk)

    def test_title_and_due_date_are_required(self):
        response = self.client.post(
            reverse("admin_client_task_create", args=[self.profile.pk]),
            {
                "company_id": self.company.pk,
                "title": "",
                "due_at": "",
                "assigned_to": self.admin.pk,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(ClientTask.objects.exists())

    def test_status_change_requires_observation_and_is_audited(self):
        task = self._create_task()
        url = reverse(
            "admin_client_task_set_status",
            args=[self.profile.pk, task.pk],
        )

        missing_note = self.client.post(
            url,
            {
                "company_id": self.company.pk,
                "action": "complete",
                "observation": "",
            },
        )
        self.assertEqual(missing_note.status_code, 302)
        task.refresh_from_db()
        self.assertEqual(task.status, ClientTask.STATUS_PENDING)

        completed = self.client.post(
            url,
            {
                "company_id": self.company.pk,
                "action": "complete",
                "observation": "Cliente confirmo la entrega.",
            },
        )
        self.assertEqual(completed.status_code, 302)
        task.refresh_from_db()
        self.assertEqual(task.status, ClientTask.STATUS_COMPLETED)
        self.assertEqual(task.completion_note, "Cliente confirmo la entrega.")
        self.assertEqual(task.completed_by, self.admin)
        self.assertIsNotNone(task.completed_at)
        audit = AdminAuditLog.objects.get(action="client_task_status_change")
        self.assertEqual(
            audit.details["observation"],
            "Cliente confirmo la entrega.",
        )

    def test_task_from_other_company_cannot_be_modified(self):
        task = self._create_task(company=self.other_company)

        response = self.client.post(
            reverse(
                "admin_client_task_set_status",
                args=[self.profile.pk, task.pk],
            ),
            {
                "company_id": self.company.pk,
                "action": "complete",
                "observation": "No debe aplicarse.",
            },
        )

        self.assertEqual(response.status_code, 404)
        task.refresh_from_db()
        self.assertEqual(task.status, ClientTask.STATUS_PENDING)

    def test_mine_and_team_filters(self):
        mine = self._create_task(title="Tarea propia")
        team = self._create_task(
            title="Tarea del equipo",
            assigned_to=self.operator,
        )
        url = reverse("admin_client_tasks", args=[self.profile.pk])

        mine_response = self.client.get(
            url,
            {
                "company_id": self.company.pk,
                "scope": "mine",
                "status": "pending",
            },
        )
        self.assertContains(mine_response, mine.title)
        self.assertNotContains(mine_response, team.title)

        team_response = self.client.get(
            url,
            {
                "company_id": self.company.pk,
                "scope": "all",
                "status": "pending",
            },
        )
        self.assertContains(team_response, mine.title)
        self.assertContains(team_response, team.title)

    def test_overdue_property_only_applies_to_pending_tasks(self):
        task = self._create_task(due_at=timezone.now() - timedelta(minutes=1))
        self.assertTrue(task.is_overdue)
        task.status = ClientTask.STATUS_COMPLETED
        self.assertFalse(task.is_overdue)

    def test_global_inbox_is_scoped_by_company_and_assignee(self):
        mine = self._create_task(title="Recordatorio propio")
        team = self._create_task(
            title="Recordatorio del equipo",
            assigned_to=self.operator,
        )
        other_company = self._create_task(
            title="No mostrar otra empresa",
            company=self.other_company,
        )
        url = reverse("admin_client_task_inbox")

        mine_response = self.client.get(
            url,
            {
                "company_id": self.company.pk,
                "scope": "mine",
                "status": "pending",
            },
        )
        self.assertContains(mine_response, mine.title)
        self.assertNotContains(mine_response, team.title)
        self.assertNotContains(mine_response, other_company.title)

        team_response = self.client.get(
            url,
            {
                "company_id": self.company.pk,
                "scope": "all",
                "status": "pending",
            },
        )
        self.assertContains(team_response, mine.title)
        self.assertContains(team_response, team.title)
        self.assertNotContains(team_response, other_company.title)

    def test_dashboard_shows_personal_pending_and_overdue_counts(self):
        self._create_task(due_at=timezone.now() - timedelta(minutes=5))
        self._create_task(
            assigned_to=self.operator,
            due_at=timezone.now() - timedelta(minutes=5),
        )

        response = self.client.get(reverse("admin_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["my_client_tasks_count"], 1)
        self.assertEqual(response.context["my_client_tasks_overdue"], 1)
        self.assertContains(response, "Mis recordatorios")
