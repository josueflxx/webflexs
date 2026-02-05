"""
Script to create the initial superuser.
Run with: python manage.py shell < create_superuser.py
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'flexs_project.settings.local')
django.setup()

from django.contrib.auth.models import User

username = 'josueflexs'
password = 'josueflexs2007'
email = 'ventas@flexs.com.ar'

if not User.objects.filter(username=username).exists():
    user = User.objects.create_superuser(
        username=username,
        email=email,
        password=password
    )
    user.first_name = 'Josue'
    user.last_name = 'FLEXS'
    user.save()
    print(f'Superusuario "{username}" creado exitosamente!')
else:
    print(f'El usuario "{username}" ya existe.')
