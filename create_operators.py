
"""
Script to create Operator users (Staff but NOT Superuser).
Run with: python manage.py shell < create_operators.py
OR
python create_operators.py
"""
import os
import django
import sys
import json

# Setup Django environment
sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'flexs_project.settings.production')
django.setup()

from django.contrib.auth.models import User

# JSON esperado: [{"username":"...","password":"..."}, ...]
raw_operators = os.getenv('DJANGO_OPERATOR_USERS_JSON')
if not raw_operators:
    raise RuntimeError('Define DJANGO_OPERATOR_USERS_JSON antes de ejecutar este script.')

operators = json.loads(raw_operators)
if not isinstance(operators, list) or not operators:
    raise RuntimeError('DJANGO_OPERATOR_USERS_JSON debe ser una lista no vacía.')

print("Creating Operator users...")
print("-" * 50)

for op_data in operators:
    username = str(op_data.get('username', '')).strip()
    password = str(op_data.get('password', ''))
    if not username or not password:
        raise RuntimeError('Cada operador requiere username y password.')
    
    if User.objects.filter(username=username).exists():
        user = User.objects.get(username=username)
        user.set_password(password)
        user.is_staff = True
        user.is_superuser = False  # IMPORTANT: Not superuser
        user.save()
        print(f"[OK] Updated existing user: {username} (Role: Operator)")
    else:
        user = User.objects.create_user(
            username=username,
            password=password,
            is_staff=True,
            is_superuser=False
        )
        print(f"[OK] Created new Operator: {username}")

print("-" * 50)
print("Done.")
