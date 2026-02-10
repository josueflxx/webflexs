
"""
Script to create Operator users (Staff but NOT Superuser).
Run with: python manage.py shell < create_operators.py
OR
python create_operators.py
"""
import os
import django
import sys

# Setup Django environment
sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'flexs_project.settings.production')
django.setup()

from django.contrib.auth.models import User

# ==========================================
# LISTA DE OPERADORES A CREAR (Edita esto)
# ==========================================
operators = [
    {'username': 'brianroces', 'password': 'contraseña2026'},
    {'username': 'fedeflexs', 'password': 'villanueva2026'},
    {'username': 'ricardoroces', 'password': 'roces1954'},
]

print("Creating Operator users...")
print("-" * 50)

if not operators:
    print("No operators defined in the script.")
    print("Please edit 'create_operators.py' and add users to the 'operators' list.")

for op_data in operators:
    username = op_data['username']
    password = op_data['password']
    
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
