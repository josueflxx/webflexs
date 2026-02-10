"""
Orders app views - Cart and checkout.
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_POST
import json

from .models import Cart, CartItem, Order, OrderItem
from catalog.models import Product


@login_required
def cart_view(request):
    """View shopping cart."""
    cart, _ = Cart.objects.get_or_create(user=request.user)
    
    # Get client discount
    discount = 0
    if hasattr(request.user, 'client_profile'):
        discount = request.user.client_profile.get_discount_decimal()
    
    # Calculate totals
    subtotal = cart.get_total()
    discount_amount = subtotal * discount
    total = subtotal - discount_amount
    
    context = {
        'cart': cart,
        'discount': discount,
        'discount_display': discount * 100,
        'subtotal': subtotal,
        'discount_amount': discount_amount,
        'total': total,
    }
    
    return render(request, 'orders/cart.html', context)


@login_required
@require_POST
def add_to_cart(request):
    """Add product to cart (AJAX)."""
    try:
        data = json.loads(request.body)
        product_id = data.get('product_id')
        quantity = int(data.get('quantity', 1))
        
        product = get_object_or_404(Product, id=product_id, is_active=True)
        cart, _ = Cart.objects.get_or_create(user=request.user)
        
        # Check if item already in cart
        cart_item, created = CartItem.objects.get_or_create(
            cart=cart,
            product=product,
            defaults={'quantity': quantity}
        )
        
        if not created:
            cart_item.quantity += quantity
            cart_item.save()
        
        return JsonResponse({
            'success': True,
            'message': f'{product.name} agregado al carrito',
            'cart_count': cart.get_item_count()
        })
    
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


@login_required
@require_POST
def update_cart_item(request):
    """Update cart item quantity (AJAX)."""
    try:
        data = json.loads(request.body)
        item_id = data.get('item_id')
        quantity = int(data.get('quantity', 1))
        
        cart_item = get_object_or_404(CartItem, id=item_id, cart__user=request.user)
        
        if quantity <= 0:
            cart_item.delete()
            message = 'Producto eliminado del carrito'
        else:
            cart_item.quantity = quantity
            cart_item.save()
            message = 'Cantidad actualizada'
        
        cart = cart_item.cart
        
        return JsonResponse({
            'success': True,
            'message': message,
            'cart_total': float(cart.get_total()),
            'cart_count': cart.get_item_count()
        })
    
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


@login_required
@require_POST
def remove_from_cart(request):
    """Remove item from cart (AJAX)."""
    try:
        data = json.loads(request.body)
        item_id = data.get('item_id')
        
        cart_item = get_object_or_404(CartItem, id=item_id, cart__user=request.user)
        cart = cart_item.cart
        cart_item.delete()
        
        return JsonResponse({
            'success': True,
            'message': 'Producto eliminado',
            'cart_total': float(cart.get_total()),
            'cart_count': cart.get_item_count()
        })
    
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


@login_required
def checkout(request):
    """Checkout view - confirm order."""
    cart = get_object_or_404(Cart, user=request.user)
    
    if cart.items.count() == 0:
        messages.warning(request, 'Tu carrito está vacío.')
        return redirect('catalog')
    
    # Get client info
    client_profile = getattr(request.user, 'client_profile', None)
    discount = client_profile.get_discount_decimal() if client_profile else 0
    
    if request.method == 'POST':
        notes = request.POST.get('notes', '').strip()
        
        # Calculate totals
        subtotal = cart.get_total()
        discount_amount = subtotal * discount
        total = subtotal - discount_amount
        
        # Create order
        order = Order.objects.create(
            user=request.user,
            notes=notes,
            subtotal=subtotal,
            discount_percentage=discount * 100,
            discount_amount=discount_amount,
            total=total,
            client_company=client_profile.company_name if client_profile else '',
            client_cuit=client_profile.cuit_dni if client_profile else '',
            client_address=client_profile.address if client_profile else '',
            client_phone=client_profile.phone if client_profile else '',
        )
        
        # Create order items
        for cart_item in cart.items.all():
            OrderItem.objects.create(
                order=order,
                product=cart_item.product,
                product_sku=cart_item.product.sku,
                product_name=cart_item.product.name,
                quantity=cart_item.quantity,
                price_at_purchase=cart_item.product.price,
            )
        
        # Clear cart
        cart.clear()
        
        messages.success(request, f'¡Pedido #{order.pk} creado exitosamente!')
        return redirect('order_detail', order_id=order.pk)
    
    context = {
        'cart': cart,
        'discount': discount,
        'discount_display': discount * 100,
        'subtotal': cart.get_total(),
        'discount_amount': cart.get_total() * discount,
        'total': cart.get_total_with_discount(discount),
    }
    
    return render(request, 'orders/checkout.html', context)


@login_required
def order_list(request):
    """List of user's orders."""
    orders = Order.objects.filter(user=request.user).order_by('-created_at')
    
    return render(request, 'orders/order_list.html', {'orders': orders})


@login_required
def order_detail(request, order_id):
    """Order detail view."""
    order = get_object_or_404(Order, pk=order_id, user=request.user)
    
    return render(request, 'orders/order_detail.html', {'order': order})


@login_required
def cart_count(request):
    """Get cart item count (AJAX)."""
    cart, _ = Cart.objects.get_or_create(user=request.user)
    return JsonResponse({'count': cart.get_item_count()})
