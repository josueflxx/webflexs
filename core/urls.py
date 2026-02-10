"""
Core app URL configuration.
"""
from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('api/go-offline/', views.go_offline, name='go_offline'),
]
