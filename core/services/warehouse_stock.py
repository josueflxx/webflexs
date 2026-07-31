"""Safe dual-write helpers for warehouse-level stock balances."""

from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from catalog.models import Product
from core.models import ProductWarehouseStock, SiteSettings


STOCK_QUANTUM = Decimal("0.001")


@dataclass(frozen=True)
class WarehouseStockApplyResult:
    applied: bool
    reason: str = ""
    balance_id: int | None = None


@dataclass(frozen=True)
class WarehouseStockInitializationResult:
    created_count: int
    skipped_count: int
    stock_total: Decimal


def warehouse_stock_is_enabled():
    return bool(SiteSettings.get_settings().warehouse_stock_enabled)


def normalize_stock_quantity(value):
    return Decimal(value or 0).quantize(STOCK_QUANTUM)


def get_warehouse_stock_initialization_preview(warehouse):
    initialized_product_ids = ProductWarehouseStock.objects.filter(
        warehouse=warehouse,
        initialized_at__isnull=False,
    ).values_list("product_id", flat=True)
    products = Product.objects.exclude(pk__in=initialized_product_ids)
    return WarehouseStockInitializationResult(
        created_count=products.count(),
        skipped_count=Product.objects.filter(pk__in=initialized_product_ids).count(),
        stock_total=sum(
            (normalize_stock_quantity(value) for value in products.values_list("stock", flat=True)),
            Decimal("0.000"),
        ),
    )


def initialize_warehouse_from_legacy_stock(warehouse):
    """Copy each product's legacy balance into one explicitly selected warehouse."""
    now = timezone.now()
    with transaction.atomic():
        initialized_product_ids = ProductWarehouseStock.objects.filter(
            warehouse=warehouse,
            initialized_at__isnull=False,
        ).values_list("product_id", flat=True)
        locked_products = list(
            Product.objects.select_for_update()
            .exclude(pk__in=initialized_product_ids)
            .order_by("pk")
        )
        stock_total = sum(
            (normalize_stock_quantity(product.stock) for product in locked_products),
            Decimal("0.000"),
        )
        ProductWarehouseStock.objects.bulk_create(
            [
                ProductWarehouseStock(
                    product=product,
                    warehouse=warehouse,
                    on_hand=product.stock,
                    reserved=0,
                    minimum=0,
                    ideal=0,
                    initialized_at=now,
                )
                for product in locked_products
            ],
            batch_size=1000,
            ignore_conflicts=True,
        )
        skipped_count = Product.objects.filter(
            warehouse_balances__warehouse=warehouse,
            warehouse_balances__initialized_at__isnull=False,
        ).distinct().count() - len(locked_products)
    return WarehouseStockInitializationResult(
        created_count=len(locked_products),
        skipped_count=max(skipped_count, 0),
        stock_total=stock_total,
    )


def apply_movement_to_warehouse_balance(
    *,
    movement,
    signed_effect,
    previous_signed_effect=0,
):
    """
    Apply one idempotent movement effect to its initialized warehouse balance.

    The legacy ``Product.stock`` field remains the compatibility source during
    rollout. Missing initialization never blocks an already-authorized fiscal
    document; the movement stores a reconciliation error instead.
    """
    if not warehouse_stock_is_enabled():
        return WarehouseStockApplyResult(False, "Stock por deposito desactivado.")
    if not getattr(movement, "warehouse_id", None):
        reason = "El movimiento no tiene deposito asignado."
        movement.warehouse_balance_error = reason
        movement.save(update_fields=["warehouse_balance_error", "updated_at"])
        return WarehouseStockApplyResult(False, reason)
    if not movement.warehouse.stock_balance_enabled:
        return WarehouseStockApplyResult(
            False,
            "Los saldos por deposito no estan activos para este deposito.",
        )
    if not getattr(movement, "product_id", None):
        reason = "El movimiento no tiene producto asignado."
        movement.warehouse_balance_error = reason
        movement.save(update_fields=["warehouse_balance_error", "updated_at"])
        return WarehouseStockApplyResult(False, reason)

    balance = (
        ProductWarehouseStock.objects.select_for_update()
        .filter(
            product_id=movement.product_id,
            warehouse_id=movement.warehouse_id,
            initialized_at__isnull=False,
        )
        .first()
    )
    if not balance:
        reason = "El stock del producto no fue inicializado para este deposito."
        movement.warehouse_balance_error = reason
        movement.save(update_fields=["warehouse_balance_error", "updated_at"])
        return WarehouseStockApplyResult(False, reason)

    current_effect = normalize_stock_quantity(signed_effect)
    if movement.warehouse_balance_applied_at:
        delta = current_effect - normalize_stock_quantity(previous_signed_effect)
    else:
        delta = current_effect

    if delta:
        ProductWarehouseStock.objects.filter(pk=balance.pk).update(
            on_hand=F("on_hand") + delta,
            updated_at=timezone.now(),
        )

    movement.warehouse_balance_applied_at = timezone.now()
    movement.warehouse_balance_error = ""
    movement.save(
        update_fields=[
            "warehouse_balance_applied_at",
            "warehouse_balance_error",
            "updated_at",
        ]
    )
    return WarehouseStockApplyResult(True, balance_id=balance.pk)
