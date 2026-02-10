
import os
import django
import sys

# Setup Django environment
sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'flexs_project.settings.production')
django.setup()

from django.contrib.auth.models import User

usernames = ['brianroces', 'fedeflexs', 'ricardoroces']

print("Checking user status...")
print("-" * 50)
for username in usernames:
    try:
        user = User.objects.get(username=username)
        print(f"User: {user.username}")
        print(f"  - Active: {user.is_active}")
        print(f"  - Staff: {user.is_staff}")
        print(f"  - Superuser: {user.is_superuser}")
        print(f"  - Password Set: {user.has_usable_password()}")
    except User.DoesNotExist:
        print(f"User: {username} - NOT FOUND")
print("-" * 50)
