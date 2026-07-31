"""
Create admin users for FLEXS system.
"""
import os
import django
import json

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'flexs_project.settings.local')
django.setup()

from django.contrib.auth.models import User

# JSON esperado: [{"username":"...","password":"..."}, ...]
raw_admins = os.getenv('DJANGO_ADMIN_USERS_JSON')
if not raw_admins:
    raise RuntimeError('Define DJANGO_ADMIN_USERS_JSON antes de ejecutar este script.')

admins = json.loads(raw_admins)
if not isinstance(admins, list) or not admins:
    raise RuntimeError('DJANGO_ADMIN_USERS_JSON debe ser una lista no vacía.')

print("Creating admin users...")
print("-" * 50)

for admin_data in admins:
    username = str(admin_data.get('username', '')).strip()
    password = str(admin_data.get('password', ''))
    if not username or not password:
        raise RuntimeError('Cada administrador requiere username y password.')
    
    # Check if user already exists
    if User.objects.filter(username=username).exists():
        user = User.objects.get(username=username)
        user.set_password(password)
        user.is_staff = True
        user.is_superuser = True
        user.save()
        print(f"[OK] Updated existing user: {username} (now admin)")
    else:
        user = User.objects.create_user(
            username=username,
            password=password,
            is_staff=True,
            is_superuser=True
        )
        print(f"[OK] Created new admin: {username}")

print("-" * 50)
print("All admin users created successfully!")
print("\nYou can now login with any of these accounts:")
for admin_data in admins:
    print(f"  - {admin_data['username']}")
