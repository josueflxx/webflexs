import os
import django
import sys

# Setup Django environment
sys.path.append(os.getcwd())
# Default to production, but respect env var if set
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'flexs_project.settings.production')
django.setup()

from django.contrib.auth.models import User

# Define the target user
username = 'luisflexs'
password = 'castrezanaflexs'
old_username = 'ventas'

print(f"Updating staff user: '{username}'...")

# 1. Try to rename 'ventas' if it exists
if User.objects.filter(username=old_username).exists():
    user = User.objects.get(username=old_username)
    user.username = username
    user.set_password(password)
    user.is_staff = True
    user.is_superuser = False
    user.save()
    print(f"[OK] Renamed '{old_username}' to '{username}' and updated password.")

# 2. Else check if 'luisflexs' already exists
elif User.objects.filter(username=username).exists():
    user = User.objects.get(username=username)
    user.set_password(password)
    user.is_staff = True
    user.is_superuser = False
    user.save()
    print(f"[OK] Updated existing user '{username}' with new password.")

# 3. Else create new user
else:
    user = User.objects.create_user(
        username=username,
        password=password,
        is_staff=True,
        is_superuser=False
    )
    print(f"[OK] Created new staff user: '{username}'")

print("Done.")
