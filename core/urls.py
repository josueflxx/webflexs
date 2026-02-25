"""
Core app URL configuration.
"""
from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('api/search-suggestions/', views.search_suggestions, name='search_suggestions'),
    path('api/admin-presence/', views.admin_presence, name='admin_presence'),
    path('api/go-offline/', views.go_offline, name='go_offline'),
]
