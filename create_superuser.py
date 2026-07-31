"""
Script to create the initial superuser.
Run with: python manage.py shell < create_superuser.py
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'flexs_project.settings.local')
django.setup()

from django.contrib.auth.models import User

username = os.getenv('DJANGO_SUPERUSER_USERNAME', 'admin')
password = os.getenv('DJANGO_SUPERUSER_PASSWORD')
email = os.getenv('DJANGO_SUPERUSER_EMAIL', 'admin@example.com')

if not password:
    raise RuntimeError('Define DJANGO_SUPERUSER_PASSWORD antes de ejecutar este script.')

if not User.objects.filter(username=username).exists():
    user = User.objects.create_superuser(
        username=username,
        email=email,
        password=password
    )
    user.first_name = os.getenv('DJANGO_SUPERUSER_FIRST_NAME', '')
    user.last_name = os.getenv('DJANGO_SUPERUSER_LAST_NAME', '')
    user.save()
    print(f'Superusuario "{username}" creado exitosamente!')
else:
    print(f'El usuario "{username}" ya existe.')
