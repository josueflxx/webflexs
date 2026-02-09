"""
Create admin users for FLEXS system.
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'flexs_project.settings.local')
django.setup()

from django.contrib.auth.models import User

# Admin users to create
admins = [
    {'username': 'josueflexs', 'password': 'josueflexs2007'},
    {'username': 'fedeflexs', 'password': 'fedeflexs2026'},
    {'username': 'ricardoroces', 'password': 'ricardoflexs1954'},
    {'username': 'brianroces', 'password': 'brianrocesflexs2026'},
]

print("Creating admin users...")
print("-" * 50)

for admin_data in admins:
    username = admin_data['username']
    password = admin_data['password']
    
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
