"""
Accounts app views - Login, logout, and account requests.
"""
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.decorators.http import require_http_methods
from .models import AccountRequest


def login_view(request):
    """User login view."""
    if request.user.is_authenticated:
        return redirect('login_redirect')
    
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        
        user = authenticate(request, username=username, password=password)
        
        if user is not None:
            login(request, user)
            messages.success(request, f'¡Bienvenido, {user.first_name or user.username}!')
            
            # Check for next parameter
            next_url = request.GET.get('next', '')
            if next_url:
                return redirect(next_url)
            
            return redirect('login_redirect')
        else:
            messages.error(request, 'Usuario o contraseña incorrectos.')
    
    return render(request, 'accounts/login.html')


@login_required
def login_redirect(request):
    """
    Redirect after login based on user role.
    Admin sees choice screen, client goes to catalog.
    """
    if request.user.is_staff:
        return render(request, 'accounts/admin_redirect.html')
    return redirect('catalog')


def logout_view(request):
    """User logout view."""
    logout(request)
    messages.info(request, 'Has cerrado sesión.')
    return redirect('home')


def account_request(request):
    """Account request form for new B2B clients."""
    if request.user.is_authenticated:
        return redirect('catalog')
    
    if request.method == 'POST':
        # Get form data
        company_name = request.POST.get('company_name', '').strip()
        contact_name = request.POST.get('contact_name', '').strip()
        cuit_dni = request.POST.get('cuit_dni', '').strip()
        email = request.POST.get('email', '').strip()
        phone = request.POST.get('phone', '').strip()
        province = request.POST.get('province', '').strip()
        address = request.POST.get('address', '').strip()
        message = request.POST.get('message', '').strip()
        
        # Basic validation
        errors = []
        if not company_name:
            errors.append('El nombre de la empresa es requerido.')
        if not contact_name:
            errors.append('El nombre de contacto es requerido.')
        if not email:
            errors.append('El email es requerido.')
        if not phone:
            errors.append('El teléfono es requerido.')
        
        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            # Create account request
            AccountRequest.objects.create(
                company_name=company_name,
                contact_name=contact_name,
                cuit_dni=cuit_dni,
                email=email,
                phone=phone,
                province=province,
                address=address,
                message=message,
            )
            messages.success(
                request, 
                '¡Solicitud enviada! Nos pondremos en contacto pronto para activar tu cuenta.'
            )
            return redirect('home')
    
    return render(request, 'accounts/account_request.html')
