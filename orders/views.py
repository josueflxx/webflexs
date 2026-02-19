"""
Orders app views - Cart, orders, and client portal.
"""
import json
import logging
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from catalog.models import Product

from .models import Cart, CartItem, ClientFavoriteProduct, Order, OrderItem, OrderStatusHistory

logger = logging.getLogger(__name__)


def _get_client_profile(user):
    return getattr(user, "client_profile", None)


def _get_discount_decimal(user):
    client_profile = _get_client_profile(user)
    if not client_profile:
        return Decimal("0")
    return Decimal(client_profile.get_discount_decimal())


def _build_order_totals_from_cart(cart, discount_decimal):
    subtotal = cart.get_total()
    discount_amount = subtotal * discount_decimal
    total = subtotal - discount_amount
    return subtotal, discount_amount, total


def _build_order_from_cart(cart, user, notes="", status=Order.STATUS_CONFIRMED):
    client_profile = _get_client_profile(user)
    discount_decimal = _get_discount_decimal(user)
    subtotal, discount_amount, total = _build_order_totals_from_cart(cart, discount_decimal)
    order = Order.objects.create(
        user=user,
        status=status,
        notes=(notes or "").strip(),
        subtotal=subtotal,
        discount_percentage=discount_decimal * 100,
        discount_amount=discount_amount,
        total=total,
        client_company=client_profile.company_name if client_profile else "",
        client_cuit=client_profile.cuit_dni if client_profile else "",
        client_address=client_profile.address if client_profile else "",
        client_phone=client_profile.phone if client_profile else "",
    )
    OrderStatusHistory.objects.create(
        order=order,
        from_status="",
        to_status=order.status,
        changed_by=user if user.is_authenticated else None,
        note="Pedido creado por cliente",
    )
    for cart_item in cart.items.select_related("product"):
        OrderItem.objects.create(
            order=order,
            product=cart_item.product,
            product_sku=cart_item.product.sku,
            product_name=cart_item.product.name,
            quantity=cart_item.quantity,
            price_at_purchase=cart_item.product.price,
        )
    return order


@login_required
def cart_view(request):
    """View shopping cart."""
    cart, _ = Cart.objects.get_or_create(user=request.user)
    discount = _get_discount_decimal(request.user)
    subtotal = cart.get_total()
    discount_amount = subtotal * discount
    total = subtotal - discount_amount
    context = {
        "cart": cart,
        "discount": discount,
        "discount_display": discount * 100,
        "subtotal": subtotal,
        "discount_amount": discount_amount,
        "total": total,
    }
    return render(request, "orders/cart.html", context)


@login_required
@require_POST
def add_to_cart(request):
    """Add product to cart (AJAX)."""
    try:
        data = json.loads(request.body)
        product_id = data.get("product_id")
        quantity = int(data.get("quantity", 1))
        if quantity < 1:
            quantity = 1

        product = get_object_or_404(
            Product.catalog_visible(Product.objects.select_related("category").prefetch_related("categories")),
            id=product_id,
        )
        cart, _ = Cart.objects.get_or_create(user=request.user)
        cart_item, created = CartItem.objects.get_or_create(
            cart=cart,
            product=product,
            defaults={"quantity": quantity},
        )

        if not created:
            cart_item.quantity += quantity
            cart_item.save(update_fields=["quantity"])

        return JsonResponse(
            {
                "success": True,
                "message": f"{product.name} agregado al carrito",
                "cart_count": cart.get_item_count(),
            }
        )
    except Exception:
        logger.exception("Error adding product to cart")
        return JsonResponse({"success": False, "error": "No se pudo agregar el producto."}, status=400)


@login_required
@require_POST
def update_cart_item(request):
    """Update cart item quantity (AJAX)."""
    try:
        data = json.loads(request.body)
        item_id = data.get("item_id")
        quantity = int(data.get("quantity", 1))

        cart_item = get_object_or_404(CartItem, id=item_id, cart__user=request.user)
        if quantity <= 0:
            cart_item.delete()
            message = "Producto eliminado del carrito"
        else:
            cart_item.quantity = quantity
            cart_item.save(update_fields=["quantity"])
            message = "Cantidad actualizada"

        cart = cart_item.cart
        return JsonResponse(
            {
                "success": True,
                "message": message,
                "cart_total": float(cart.get_total()),
                "cart_count": cart.get_item_count(),
            }
        )
    except Exception:
        logger.exception("Error updating cart item")
        return JsonResponse({"success": False, "error": "No se pudo actualizar el carrito."}, status=400)


@login_required
@require_POST
def remove_from_cart(request):
    """Remove item from cart (AJAX)."""
    try:
        data = json.loads(request.body)
        item_id = data.get("item_id")
        cart_item = get_object_or_404(CartItem, id=item_id, cart__user=request.user)
        cart = cart_item.cart
        cart_item.delete()
        return JsonResponse(
            {
                "success": True,
                "message": "Producto eliminado",
                "cart_total": float(cart.get_total()),
                "cart_count": cart.get_item_count(),
            }
        )
    except Exception:
        logger.exception("Error removing cart item")
        return JsonResponse({"success": False, "error": "No se pudo eliminar el producto."}, status=400)


@login_required
def checkout(request):
    """Checkout view - confirm order."""
    cart = get_object_or_404(Cart, user=request.user)
    if cart.items.count() == 0:
        messages.warning(request, "Tu carrito esta vacio.")
        return redirect("catalog")

    discount = _get_discount_decimal(request.user)
    if request.method == "POST":
        notes = request.POST.get("notes", "").strip()
        with transaction.atomic():
            order = _build_order_from_cart(
                cart=cart,
                user=request.user,
                notes=notes,
                status=Order.STATUS_CONFIRMED,
            )
            cart.clear()
        messages.success(request, f"Pedido #{order.pk} creado exitosamente.")
        return redirect("order_detail", order_id=order.pk)

    context = {
        "cart": cart,
        "discount": discount,
        "discount_display": discount * 100,
        "subtotal": cart.get_total(),
        "discount_amount": cart.get_total() * discount,
        "total": cart.get_total_with_discount(discount),
    }
    return render(request, "orders/checkout.html", context)


@login_required
def order_list(request):
    """List of user's orders."""
    status = request.GET.get("status", "").strip()
    orders = Order.objects.filter(user=request.user).prefetch_related("items")
    if status:
        orders = orders.filter(status=status)
    orders = orders.order_by("-created_at")
    return render(
        request,
        "orders/order_list.html",
        {
            "orders": orders,
            "status": status,
            "status_choices": Order.STATUS_CHOICES,
        },
    )


@login_required
def order_detail(request, order_id):
    """Order detail view."""
    order = get_object_or_404(Order.objects.prefetch_related("items", "status_history"), pk=order_id, user=request.user)
    return render(request, "orders/order_detail.html", {"order": order})


@login_required
@require_POST
def reorder_to_cart(request, order_id):
    """Copy all order lines into active cart for quick re-order."""
    order = get_object_or_404(Order.objects.prefetch_related("items"), pk=order_id, user=request.user)
    cart, _ = Cart.objects.get_or_create(user=request.user)
    added = 0
    for item in order.items.all():
        if not item.product_id or not item.product:
            continue
        cart_item, created = CartItem.objects.get_or_create(
            cart=cart,
            product=item.product,
            defaults={"quantity": item.quantity},
        )
        if not created:
            cart_item.quantity += item.quantity
            cart_item.save(update_fields=["quantity"])
        added += 1
    messages.success(request, f"Se agregaron {added} productos al carrito para recompra.")
    return redirect("cart")


@login_required
def client_portal(request):
    """B2B client portal dashboard."""
    orders_qs = Order.objects.filter(user=request.user)
    favorites = (
        ClientFavoriteProduct.objects.filter(user=request.user)
        .select_related("product")
        .order_by("-created_at")[:10]
    )

    context = {
        "active_orders_count": orders_qs.exclude(
            status__in=[Order.STATUS_DELIVERED, Order.STATUS_CANCELLED]
        ).count(),
        "total_orders_count": orders_qs.count(),
        "recent_orders": orders_qs.order_by("-created_at")[:8],
        "favorites": favorites,
    }
    return render(request, "orders/client_portal.html", context)


@login_required
@require_POST
def toggle_favorite(request):
    """Add/remove product favorites for quick reorder portal."""
    try:
        data = json.loads(request.body)
        product_id = int(data.get("product_id"))
        product = get_object_or_404(Product, pk=product_id)
        favorite, created = ClientFavoriteProduct.objects.get_or_create(
            user=request.user,
            product=product,
        )
        if not created:
            favorite.delete()
        count = ClientFavoriteProduct.objects.filter(user=request.user).count()
        return JsonResponse(
            {
                "success": True,
                "is_favorite": created,
                "favorites_count": count,
            }
        )
    except Exception:
        logger.exception("Error toggling favorite")
        return JsonResponse({"success": False, "error": "No se pudo actualizar favorito."}, status=400)


@login_required
def cart_count(request):
    """Get cart item count (AJAX)."""
    cart, _ = Cart.objects.get_or_create(user=request.user)
    favorites_count = ClientFavoriteProduct.objects.filter(user=request.user).count()
    return JsonResponse({"count": cart.get_item_count(), "favorites": favorites_count})
