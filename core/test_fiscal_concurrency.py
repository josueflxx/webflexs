from contextlib import ExitStack
from datetime import date
from decimal import Decimal
from queue import Queue
from threading import Barrier, BrokenBarrierError, Event, Lock, Thread
from unittest import skipUnless
from unittest.mock import patch
import uuid

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import (
    IntegrityError,
    close_old_connections,
    connection,
    transaction,
)
from django.test import Client, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import ClientCompany, ClientProfile
from catalog.models import Product
from core.models import (
    FISCAL_ATTEMPT_OPERATION_AUTHORIZE,
    FISCAL_ATTEMPT_OPERATION_RECOVER,
    FISCAL_STATUS_AUTHORIZED,
    FISCAL_STATUS_RECOVERED_AUTHORIZED,
    FISCAL_STATUS_SUBMITTING,
    FISCAL_STATUS_UNCERTAIN,
    FiscalDocument,
    FiscalEmissionAttempt,
)
from core.services.arca_client import ArcaConsultationResult, ArcaEmissionResult
from core.services.fiscal_documents import create_local_fiscal_document_from_order
from core.services.fiscal_emission import emit_fiscal_document_now
from core.services.fiscal_integrity import fiscal_payload_hash
from core.services.fiscal_recovery import recover_fiscal_document
from core.test_fiscal_readiness import FiscalFixtureMixin
from orders.models import Order, OrderItem


POSTGRESQL_CONCURRENCY_REASON = (
    "requires PostgreSQL row locks, partial unique constraints, and independent "
    "backend connections; SQLite cannot validate these semantics"
)


@skipUnless(connection.vendor == "postgresql", POSTGRESQL_CONCURRENCY_REASON)
class PostgreSQLFiscalConcurrencyTests(FiscalFixtureMixin, TransactionTestCase):
    """Exercise fiscal races with real PostgreSQL sessions and row locks."""

    barrier_timeout = 10
    thread_timeout = 20
    simulated_cae = "74123456789012"

    def _run_concurrently(self, operations, *, completion_event=None):
        start_barrier = Barrier(len(operations) + 1, timeout=self.barrier_timeout)
        results = Queue()
        errors = Queue()
        backend_pids = Queue()

        def worker(index, operation):
            close_old_connections()
            try:
                connection.ensure_connection()
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    backend_pids.put((index, cursor.fetchone()[0]))
                start_barrier.wait()
                results.put((index, operation()))
            except Exception as exc:  # assertions are performed in the main thread
                errors.put((index, exc))
            finally:
                if completion_event is not None:
                    completion_event.set()
                close_old_connections()

        threads = [
            Thread(
                target=worker,
                args=(index, operation),
                name=f"fiscal-pg-worker-{index}",
                daemon=True,
            )
            for index, operation in enumerate(operations)
        ]
        for thread in threads:
            thread.start()
        try:
            start_barrier.wait()
        except BrokenBarrierError as exc:
            self.fail(f"The concurrency start barrier broke: {exc}")
        for thread in threads:
            thread.join(self.thread_timeout)
        self.assertFalse(
            [thread.name for thread in threads if thread.is_alive()],
            "A fiscal concurrency worker timed out or deadlocked.",
        )

        pid_rows = []
        while not backend_pids.empty():
            pid_rows.append(backend_pids.get_nowait())
        self.assertEqual(len(pid_rows), len(operations))
        self.assertEqual(
            len({pid for _index, pid in pid_rows}),
            len(operations),
            "Each worker must own a distinct PostgreSQL backend connection.",
        )

        result_rows = []
        error_rows = []
        while not results.empty():
            result_rows.append(results.get_nowait())
        while not errors.empty():
            error_rows.append(errors.get_nowait())
        return sorted(result_rows), sorted(error_rows), sorted(pid_rows)

    def _emission_stack(self):
        stack = ExitStack()
        stack.enter_context(
            patch(
                "core.services.fiscal_emission.is_company_fiscal_ready",
                return_value=(True, []),
            )
        )
        stack.enter_context(
            patch("core.services.fiscal_emission.sync_sales_document_type_counter")
        )
        stack.enter_context(
            patch(
                "core.services.fiscal_emission.sync_fiscal_document_account_movement"
            )
        )
        stack.enter_context(
            patch(
                "core.services.fiscal_emission.ensure_stock_movements_for_order_document"
            )
        )
        return stack

    def _recovery_stack(self):
        stack = ExitStack()
        stack.enter_context(
            patch("core.services.fiscal_recovery.sync_sales_document_type_counter")
        )
        stack.enter_context(
            patch(
                "core.services.fiscal_recovery.sync_fiscal_document_account_movement"
            )
        )
        stack.enter_context(
            patch(
                "core.services.fiscal_recovery.ensure_stock_movements_for_order_document"
            )
        )
        return stack

    def _create_order(self, suffix):
        user = User.objects.create_user(
            username=f"fiscal_race_client_{suffix}",
            password="local-test-only",
        )
        profile = ClientProfile.objects.create(
            user=user,
            company_name=f"Cliente Concurrencia {suffix}",
            document_type="cuit",
            document_number="20123456786",
            iva_condition="responsable_inscripto",
            fiscal_address="Calle Concurrente 100",
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
            sku=f"RACE-{suffix}",
            name=f"Producto Concurrente {suffix}",
            price=Decimal("121.00"),
            cost=Decimal("50.00"),
            stock=10,
            is_active=True,
        )
        order = Order.objects.create(
            user=user,
            company=self.company,
            status=Order.STATUS_CONFIRMED,
            subtotal=Decimal("121.00"),
            total=Decimal("121.00"),
            client_company=profile.company_name,
            client_company_ref=client_company,
        )
        OrderItem.objects.create(
            order=order,
            product=product,
            product_sku=product.sku,
            product_name=product.name,
            quantity=1,
            unit_price_base=Decimal("121.00"),
            price_at_purchase=Decimal("121.00"),
            subtotal=Decimal("121.00"),
        )
        return order

    def _emit_operation(self, document_id, actor_id, client_factory):
        document = FiscalDocument.objects.get(pk=document_id)
        actor = User.objects.get(pk=actor_id) if actor_id else None
        outcome = emit_fiscal_document_now(
            fiscal_document=document,
            actor=actor,
            client_factory=client_factory,
        )
        return {
            "document_id": outcome.document.pk,
            "state": outcome.state,
            "number": outcome.document.number,
        }

    def _recover_operation(self, document_id, actor_id, client_factory):
        document = FiscalDocument.objects.get(pk=document_id)
        actor = User.objects.get(pk=actor_id) if actor_id else None
        outcome = recover_fiscal_document(
            fiscal_document=document,
            actor=actor,
            client_factory=client_factory,
        )
        return {
            "document_id": outcome.document.pk,
            "state": outcome.state,
            "consulted": outcome.consulted,
        }

    def _make_uncertain(self, document):
        class UncertainClient:
            def __init__(self, **_kwargs):
                pass

            def fetch_last_authorized_number(self, **_kwargs):
                return 0

            def emit_fiscal_document(
                self,
                *,
                mark_dispatched,
                **_kwargs,
            ):
                mark_dispatched()
                return ArcaEmissionResult(
                    state="uncertain",
                    error_code="simulated_timeout",
                    error_message="simulated uncertain result",
                )

        with self._emission_stack():
            outcome = emit_fiscal_document_now(
                fiscal_document=document,
                client_factory=UncertainClient,
            )
        self.assertEqual(outcome.state, FISCAL_STATUS_UNCERTAIN)
        return outcome.document

    def _holding_authorized_client(self, release_event, *, remote_last=0):
        simulated_cae = self.simulated_cae

        class HoldingAuthorizedClient:
            def __init__(self, **_kwargs):
                pass

            def fetch_last_authorized_number(self, **_kwargs):
                return remote_last

            def emit_fiscal_document(
                self,
                *,
                mark_dispatched,
                **_kwargs,
            ):
                mark_dispatched()
                if not release_event.wait(self.barrier_timeout):
                    raise RuntimeError("Timed out waiting for the competing caller.")
                return ArcaEmissionResult(
                    state="authorized",
                    cae=simulated_cae,
                    cae_due_date=date(2026, 8, 15),
                    response_payload={"Resultado": "A", "source": "local-simulation"},
                )

        HoldingAuthorizedClient.barrier_timeout = self.barrier_timeout
        return HoldingAuthorizedClient

    def _holding_recovery_client(self, release_event, consultation_counter):
        simulated_cae = self.simulated_cae

        class HoldingRecoveryClient:
            def __init__(self, **_kwargs):
                pass

            def consult_fiscal_document(self, **_kwargs):
                with consultation_counter["lock"]:
                    consultation_counter["count"] += 1
                if not release_event.wait(self.barrier_timeout):
                    raise RuntimeError("Timed out waiting for the competing worker.")
                return ArcaConsultationResult(
                    state="authorized",
                    cae=simulated_cae,
                    cae_due_date=date(2026, 8, 15),
                    response_payload={"Resultado": "A", "source": "local-simulation"},
                )

        HoldingRecoveryClient.barrier_timeout = self.barrier_timeout
        return HoldingRecoveryClient

    def test_case_1_same_idempotency_key_creates_one_effective_operation(self):
        order = self._create_order("same-key")

        def create_operation():
            current_order = Order.objects.get(pk=order.pk)
            document, created = create_local_fiscal_document_from_order(
                order=current_order,
                company=type(self.company).objects.get(pk=self.company.pk),
                doc_type="FB",
                point_of_sale=type(self.homologation_pos).objects.get(
                    pk=self.homologation_pos.pk
                ),
                issue_mode="manual",
                require_invoice_ready=False,
            )
            return {"id": document.pk, "created": created}

        results, errors, _pids = self._run_concurrently(
            [create_operation, create_operation]
        )

        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(len({row["id"] for _index, row in results}), 1)
        self.assertEqual(
            sum(bool(row["created"]) for _index, row in results),
            1,
        )
        self.assertEqual(FiscalDocument.objects.filter(order=order).count(), 1)

    def test_case_2_same_fiscal_identity_number_persists_one_record(self):
        snapshot = self._snapshot(point_of_sale=self.production_pos)
        snapshot_hash = fiscal_payload_hash(snapshot)
        number = 991

        def persist_operation():
            with transaction.atomic():
                document = FiscalDocument.objects.create(
                    source_key=f"race:identity:{uuid.uuid4()}",
                    company_id=self.company.pk,
                    point_of_sale_id=self.production_pos.pk,
                    doc_type="FB",
                    issue_mode="manual",
                    status="draft",
                    number=number,
                    subtotal_net=Decimal("100.00"),
                    tax_total=Decimal("21.00"),
                    total=Decimal("121.00"),
                    fiscal_snapshot=snapshot,
                    snapshot_hash=snapshot_hash,
                    issuer_cuit_snapshot="30693450239",
                    environment_snapshot=self.production_pos.environment,
                    point_of_sale_number_snapshot=self.production_pos.number,
                )
                return {"id": document.pk}

        results, errors, _pids = self._run_concurrently(
            [persist_operation, persist_operation]
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0][1], IntegrityError)
        self.assertEqual(
            FiscalDocument.objects.filter(
                company=self.company,
                point_of_sale=self.production_pos,
                doc_type="FB",
                number=number,
            ).count(),
            1,
        )

    def test_case_3_simultaneous_number_reservation_is_serialized(self):
        first = self._document(status="ready_to_issue")
        second = self._document(status="ready_to_issue")
        release_event = Event()
        first_client = self._holding_authorized_client(release_event, remote_last=0)

        with self._emission_stack():
            results, errors, _pids = self._run_concurrently(
                [
                    lambda: self._emit_operation(first.pk, None, first_client),
                    lambda: self._emit_operation(second.pk, None, first_client),
                ],
                completion_event=release_event,
            )

        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0][1], ValidationError)
        assigned = FiscalDocument.objects.exclude(number=None).get(
            pk__in=[first.pk, second.pk]
        )
        waiting = FiscalDocument.objects.get(
            pk=second.pk if assigned.pk == first.pk else first.pk
        )
        self.assertEqual(assigned.number, 1)
        self.assertEqual(assigned.status, FISCAL_STATUS_AUTHORIZED)
        self.assertIsNone(waiting.number)

        class NextNumberClient:
            def __init__(self, **_kwargs):
                pass

            def fetch_last_authorized_number(self, **_kwargs):
                return 1

            def emit_fiscal_document(self, *, mark_dispatched, **_kwargs):
                mark_dispatched()
                return ArcaEmissionResult(
                    state="authorized",
                    cae="74123456789013",
                    cae_due_date=date(2026, 8, 15),
                )

        with self._emission_stack():
            retried = emit_fiscal_document_now(
                fiscal_document=waiting,
                client_factory=NextNumberClient,
            )
        self.assertEqual(retried.document.number, 2)
        self.assertEqual(
            sorted(
                FiscalDocument.objects.filter(pk__in=[first.pk, second.pk])
                .values_list("number", flat=True)
            ),
            [1, 2],
        )

    def test_case_4_two_sellers_emit_one_draft_once(self):
        seller_a = User.objects.create_user(
            username="seller_concurrent_a",
            password="local-test-only",
            is_staff=True,
            is_superuser=True,
        )
        seller_b = User.objects.create_user(
            username="seller_concurrent_b",
            password="local-test-only",
            is_staff=True,
            is_superuser=True,
        )
        document = self._document(status="ready_to_issue")
        release_event = Event()
        client_factory = self._holding_authorized_client(release_event)

        with self._emission_stack():
            results, errors, _pids = self._run_concurrently(
                [
                    lambda: self._emit_operation(
                        document.pk,
                        seller_a.pk,
                        client_factory,
                    ),
                    lambda: self._emit_operation(
                        document.pk,
                        seller_b.pk,
                        client_factory,
                    ),
                ],
                completion_event=release_event,
            )

        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0][1], ValidationError)
        document.refresh_from_db()
        self.assertEqual(document.status, FISCAL_STATUS_AUTHORIZED)
        self.assertEqual(document.emission_attempts.count(), 1)
        attempt = document.emission_attempts.get(
            operation=FISCAL_ATTEMPT_OPERATION_AUTHORIZE
        )
        self.assertIn(attempt.triggered_by_id, {seller_a.pk, seller_b.pk})
        successful_index = results[0][0]
        self.assertEqual(
            attempt.triggered_by_id,
            [seller_a.pk, seller_b.pk][successful_index],
        )

    def test_case_5_two_workers_process_one_pending_operation_once(self):
        document = self._make_uncertain(self._document(status="ready_to_issue"))
        worker_a = User.objects.create_user(
            username="recovery_worker_a",
            password="local-test-only",
            is_staff=True,
        )
        worker_b = User.objects.create_user(
            username="recovery_worker_b",
            password="local-test-only",
            is_staff=True,
        )
        release_event = Event()
        consultation_counter = {"count": 0, "lock": Lock()}
        client_factory = self._holding_recovery_client(
            release_event,
            consultation_counter,
        )

        with self._recovery_stack():
            results, errors, _pids = self._run_concurrently(
                [
                    lambda: self._recover_operation(
                        document.pk,
                        worker_a.pk,
                        client_factory,
                    ),
                    lambda: self._recover_operation(
                        document.pk,
                        worker_b.pk,
                        client_factory,
                    ),
                ],
                completion_event=release_event,
            )

        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(consultation_counter["count"], 1)
        self.assertEqual(
            sum(bool(row["consulted"]) for _index, row in results),
            1,
        )
        document.refresh_from_db()
        self.assertEqual(document.status, FISCAL_STATUS_RECOVERED_AUTHORIZED)
        self.assertEqual(
            document.emission_attempts.filter(
                operation=FISCAL_ATTEMPT_OPERATION_RECOVER
            ).count(),
            1,
        )

    def test_case_6_uncertain_retry_and_recovery_keep_same_identity(self):
        document = self._make_uncertain(self._document(status="ready_to_issue"))
        original_number = document.number
        release_event = Event()
        consultation_counter = {"count": 0, "lock": Lock()}
        recovery_client = self._holding_recovery_client(
            release_event,
            consultation_counter,
        )

        class ForbiddenReemissionClient:
            def __init__(self, **_kwargs):
                raise AssertionError("An uncertain operation must not create a client.")

        with self._emission_stack(), self._recovery_stack():
            results, errors, _pids = self._run_concurrently(
                [
                    lambda: self._emit_operation(
                        document.pk,
                        None,
                        ForbiddenReemissionClient,
                    ),
                    lambda: self._recover_operation(
                        document.pk,
                        None,
                        recovery_client,
                    ),
                ],
                completion_event=release_event,
            )

        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0][1], ValidationError)
        self.assertEqual(consultation_counter["count"], 1)
        document.refresh_from_db()
        self.assertEqual(document.number, original_number)
        self.assertEqual(document.status, FISCAL_STATUS_RECOVERED_AUTHORIZED)
        self.assertEqual(
            document.emission_attempts.filter(
                operation=FISCAL_ATTEMPT_OPERATION_AUTHORIZE
            ).count(),
            1,
        )
        self.assertEqual(
            document.emission_attempts.filter(
                operation=FISCAL_ATTEMPT_OPERATION_RECOVER
            ).count(),
            1,
        )

    def test_case_7_cae_then_persistence_failure_remains_recoverable(self):
        document = self._document(status="ready_to_issue")

        class AuthorizedThenPersistenceFailsClient:
            def __init__(self, **_kwargs):
                pass

            def fetch_last_authorized_number(self, **_kwargs):
                return 0

            def emit_fiscal_document(self, *, mark_dispatched, **_kwargs):
                mark_dispatched()
                return ArcaEmissionResult(
                    state="authorized",
                    cae=self.simulated_cae,
                    cae_due_date=date(2026, 8, 15),
                    response_payload={"Resultado": "A", "source": "local-simulation"},
                )

        AuthorizedThenPersistenceFailsClient.simulated_cae = self.simulated_cae
        with self._emission_stack(), patch.object(
            FiscalEmissionAttempt,
            "finalize",
            side_effect=RuntimeError("simulated critical persistence failure"),
        ):
            with self.assertRaises(RuntimeError):
                emit_fiscal_document_now(
                    fiscal_document=document,
                    client_factory=AuthorizedThenPersistenceFailsClient,
                )

        document.refresh_from_db()
        self.assertEqual(document.status, FISCAL_STATUS_SUBMITTING)
        self.assertEqual(document.cae, "")
        original_number = document.number
        authorization_attempt = document.emission_attempts.get(
            operation=FISCAL_ATTEMPT_OPERATION_AUTHORIZE
        )
        self.assertTrue(authorization_attempt.request_may_have_been_sent)
        self.assertEqual(authorization_attempt.result_status, "pending")

        with transaction.atomic():
            locked = FiscalDocument.objects.select_for_update().get(pk=document.pk)
            locked.transition_to(
                FISCAL_STATUS_UNCERTAIN,
                error_code="task_unexpected_after_sending",
                error_message="simulated critical persistence failure",
                next_recovery_at=timezone.now(),
            )

        release_event = Event()
        consultation_counter = {"count": 0, "lock": Lock()}
        recovery_client = self._holding_recovery_client(
            release_event,
            consultation_counter,
        )

        class ForbiddenReemissionClient:
            def __init__(self, **_kwargs):
                raise AssertionError("Recovery must not create another authorization.")

        with self._emission_stack(), self._recovery_stack():
            results, errors, _pids = self._run_concurrently(
                [
                    lambda: self._emit_operation(
                        document.pk,
                        None,
                        ForbiddenReemissionClient,
                    ),
                    lambda: self._recover_operation(
                        document.pk,
                        None,
                        recovery_client,
                    ),
                ],
                completion_event=release_event,
            )

        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0][1], ValidationError)
        self.assertEqual(consultation_counter["count"], 1)
        document.refresh_from_db()
        self.assertEqual(document.number, original_number)
        self.assertEqual(document.status, FISCAL_STATUS_RECOVERED_AUTHORIZED)
        self.assertEqual(document.cae, self.simulated_cae)
        self.assertEqual(
            document.emission_attempts.filter(
                operation=FISCAL_ATTEMPT_OPERATION_AUTHORIZE
            ).count(),
            1,
        )

    def test_case_8_double_http_request_enqueues_once(self):
        seller_a = User.objects.create_user(
            username="http_seller_a",
            password="local-test-only",
            is_staff=True,
            is_superuser=True,
        )
        seller_b = User.objects.create_user(
            username="http_seller_b",
            password="local-test-only",
            is_staff=True,
            is_superuser=True,
        )
        document = self._document(status="ready_to_issue")
        clients = []
        for seller in (seller_a, seller_b):
            http_client = Client()
            http_client.force_login(seller)
            session = http_client.session
            session["active_company_id"] = self.company.pk
            session.save()
            clients.append(http_client)

        queued = []
        queued_lock = Lock()

        def record_delay(*, document_id, actor_id):
            with queued_lock:
                queued.append((document_id, actor_id))
            return {"status": "queued"}

        target_url = reverse("admin_fiscal_document_emit", args=[document.pk])
        with (
            patch(
                "core.services.fiscal_emission.is_company_fiscal_ready",
                return_value=(True, []),
            ),
            patch(
                "core.tasks.emit_fiscal_document_async_task.delay",
                side_effect=record_delay,
            ),
        ):
            results, errors, _pids = self._run_concurrently(
                [
                    lambda: clients[0].post(target_url).status_code,
                    lambda: clients[1].post(target_url).status_code,
                ]
            )

        self.assertEqual(errors, [])
        self.assertEqual([status for _index, status in results], [302, 302])
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0][0], document.pk)
        self.assertIn(queued[0][1], {seller_a.pk, seller_b.pk})
        document.refresh_from_db()
        self.assertIsNotNone(document.dispatch_requested_at)
        self.assertEqual(document.emission_attempts.count(), 0)
