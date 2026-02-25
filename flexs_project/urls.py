"""
URL configuration for flexs_project project.
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    # Django admin (backup)
    path('django-admin/', admin.site.urls),
    
    # Main apps
    path('', include('core.urls')),
    path('catalogo/', include('catalog.urls')),
    path('accounts/', include('accounts.urls')),
    path('pedidos/', include('orders.urls')),
    
    # Custom admin panel
    path('admin-panel/', include('admin_panel.urls')),
]

if settings.FEATURE_API_V1_ENABLED:
    urlpatterns.insert(1, path('api/v1/', include(('core.api_v1.urls', 'api_v1'), namespace='api_v1')))

# Serve media files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
