"""
Accounts app URL configuration.
"""
from django.urls import path
from . import views

urlpatterns = [
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('redirect/', views.login_redirect, name='login_redirect'),
    path('solicitar/', views.account_request, name='account_request'),
]
