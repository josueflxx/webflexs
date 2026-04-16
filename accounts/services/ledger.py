"""Backward-compatible facade for current-account helpers.

Keep legacy imports working while the business rules live in
``account_movement_service``.
"""

from accounts.services.account_movement_service import (
    create_adjustment_transaction,
    ensure_adjustment_transaction,
    get_order_accountable_internal_document,
    get_order_billable_fiscal_document,
    resolve_order_charge_snapshot,
    resync_client_ledger,
    sync_fiscal_document_account_movement,
    sync_internal_document_account_movement,
    sync_order_charge_transaction,
    sync_payment_transaction,
)


__all__ = [
    "create_adjustment_transaction",
    "ensure_adjustment_transaction",
    "get_order_accountable_internal_document",
    "get_order_billable_fiscal_document",
    "resolve_order_charge_snapshot",
    "resync_client_ledger",
    "sync_fiscal_document_account_movement",
    "sync_internal_document_account_movement",
    "sync_order_charge_transaction",
    "sync_payment_transaction",
]
