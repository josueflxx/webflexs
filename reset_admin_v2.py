
import os
import django

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'flexs_project.settings.local')
try:
    django.setup()
except Exception as e:
    # Fallback to base settings if local not found/working
    os.environ['DJANGO_SETTINGS_MODULE'] = 'flexs_project.settings.base'
    django.setup()

from django.contrib.auth import get_user_model

def reset_admin():
    User = get_user_model()
    username = 'admin'
    email = 'admin@example.com'
    password = 'admin'  # Simple password for local dev

    if not User.objects.filter(username=username).exists():
        print(f"Creating superuser '{username}'...")
        User.objects.create_superuser(username, email, password)
        print("Superuser created successfully.")
    else:
        print(f"Updating password for user '{username}'...")
        u = User.objects.get(username=username)
        u.set_password(password)
        u.is_staff = True
        u.is_superuser = True
        u.save()
        print("Password updated successfully.")

if __name__ == '__main__':
    reset_admin()
