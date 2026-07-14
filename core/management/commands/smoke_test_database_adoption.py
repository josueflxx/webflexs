import json
import sqlite3
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client

from core.services.data_integrity import sha256_file


class Command(BaseCommand):
    help = "Ejecuta smoke tests de solo lectura contra una copia SQLite candidata."
    requires_system_checks = "__all__"

    def add_arguments(self, parser):
        parser.add_argument("--output", required=True, help="Archivo JSON de resultados.")

    @staticmethod
    def _authenticated_client(user, company_id=None):
        client = Client(raise_request_exception=False)
        session = client.session
        session[SESSION_KEY] = str(user.pk)
        session[BACKEND_SESSION_KEY] = settings.AUTHENTICATION_BACKENDS[0]
        session[HASH_SESSION_KEY] = user.get_session_auth_hash()
        if company_id is not None:
            session["active_company_id"] = company_id
        session.save()
        # Signed-cookie sessions receive a new encoded key after every save.
        client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key
        return client

    @staticmethod
    def _request(client, path, actor, expected_statuses=(200,)):
        try:
            response = client.get(path)
            return {
                "actor": actor,
                "path": path,
                "status_code": response.status_code,
                "expected_statuses": list(expected_statuses),
                "redirect_url": response.get("Location", ""),
                "passed": response.status_code in expected_statuses,
            }
        except Exception as exc:
            return {
                "actor": actor,
                "path": path,
                "status_code": 500,
                "passed": False,
                "error": str(exc),
            }

    def handle(self, *args, **options):
        if not getattr(settings, "INTEGRITY_PREVIEW_MODE", False):
            raise CommandError("Este comando requiere flexs_project.settings.integrity_preview.")
        database_path = Path(settings.DATABASES["default"]["NAME"]).resolve()
        if database_path == (Path(settings.BASE_DIR) / "db.sqlite3").resolve():
            raise CommandError("Se rehusa validar directamente db.sqlite3.")

        hash_before = sha256_file(database_path)
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA query_only")
            query_only = int(cursor.fetchone()[0])
            cursor.execute("PRAGMA integrity_check")
            integrity_check = [str(row[0]) for row in cursor.fetchall()]
            cursor.execute("PRAGMA foreign_key_check")
            foreign_key_violations = len(cursor.fetchall())
        if query_only != 1:
            raise CommandError("La conexion de validacion no esta en modo query_only.")

        executor = MigrationExecutor(connection)
        pending_migrations = [
            f"{migration.app_label}.{migration.name}"
            for migration, _backwards in executor.migration_plan(
                executor.loader.graph.leaf_nodes()
            )
        ]

        model_labels = [
            "catalog.Product",
            "catalog.Category",
            "catalog.Supplier",
            "catalog.ProductSupplier",
            "catalog.SupplierCostHistory",
            "catalog.ProductDuplicateReview",
            "accounts.ClientProfile",
            "orders.Order",
            "orders.OrderRequest",
            "core.StockMovement",
        ]
        model_counts = {}
        for label in model_labels:
            model = apps.get_model(label)
            model_counts[label] = model.objects.count()

        requests = []
        anonymous = Client(raise_request_exception=False)
        anonymous_paths = ["/", "/catalogo/", "/accounts/login/"]
        if settings.FEATURE_API_V1_ENABLED:
            anonymous_paths.append("/api/v1/health/")
        for path in anonymous_paths:
            expected = (200, 403) if path == "/api/v1/health/" else (200,)
            requests.append(self._request(anonymous, path, "anonymous", expected))

        User = apps.get_model(settings.AUTH_USER_MODEL)
        Company = apps.get_model("core.Company")
        company_id = Company.objects.order_by("id").values_list("id", flat=True).first()
        staff_user = User.objects.filter(is_active=True, is_staff=True).order_by("id").first()
        if staff_user is not None:
            staff_client = self._authenticated_client(staff_user, company_id)
            for path in (
                "/admin-panel/",
                "/admin-panel/productos/",
                "/admin-panel/categorias/",
                "/admin-panel/proveedores/",
                "/admin-panel/pedidos/",
                "/admin-panel/abrazaderas-a-medida/",
                "/admin-panel/importar/",
            ):
                requests.append(self._request(staff_client, path, "staff", (200,)))

        superuser = User.objects.filter(is_active=True, is_superuser=True).order_by("id").first()
        if superuser is not None:
            superuser_client = self._authenticated_client(superuser, company_id)
            requests.append(
                self._request(
                    superuser_client,
                    "/admin-panel/productos/duplicados/",
                    "superuser",
                    (200,),
                )
            )
            product_id = apps.get_model("catalog.Product").objects.order_by("id").values_list(
                "id", flat=True
            ).first()
            if product_id is not None:
                requests.append(
                    self._request(
                        superuser_client,
                        f"/admin-panel/productos/{product_id}/editar/",
                        "superuser",
                        (200,),
                    )
                )

        client_user = User.objects.filter(is_active=True, is_staff=False).order_by("id").first()
        if client_user is not None:
            client = self._authenticated_client(client_user, company_id)
            for path in ("/catalogo/", "/pedidos/portal/", "/pedidos/pedidos/"):
                requests.append(self._request(client, path, "client", (200, 302)))

        connection.close()
        hash_after = sha256_file(database_path)
        all_requests_passed = all(row["passed"] for row in requests)
        result = {
            "database": {
                "path": str(database_path),
                "sha256_before": hash_before,
                "sha256_after": hash_after,
                "hash_unchanged": hash_before == hash_after,
                "query_only": query_only == 1,
                "integrity_check": integrity_check,
                "foreign_key_violations": foreign_key_violations,
            },
            "pending_migrations": pending_migrations,
            "model_counts": model_counts,
            "requests": requests,
            "summary": {
                "request_checks": len(requests),
                "request_failures": sum(1 for row in requests if not row["passed"]),
                "all_requests_passed": all_requests_passed,
                "application_smoke_passed": (
                    integrity_check == ["ok"]
                    and not pending_migrations
                    and all_requests_passed
                    and hash_before == hash_after
                ),
            },
        }
        output_path = Path(options["output"]).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.stdout.write(json.dumps(result, ensure_ascii=False, indent=2))
        if not result["summary"]["application_smoke_passed"]:
            raise CommandError("La copia no supero todos los smoke tests de aplicacion.")
        self.stdout.write(self.style.SUCCESS("Smoke tests de adopcion superados en modo solo lectura."))
