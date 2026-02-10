
import os
import django
import sys
from django.conf import settings

# Setup Django environment
sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'flexs_project.settings.production')
django.setup()

print("Checking Database Configuration in Shell...")
print("-" * 50)
db_settings = settings.DATABASES['default']
print(f"Engine: {db_settings['ENGINE']}")
print(f"Name: {db_settings['NAME']}")
print(f"User: {db_settings['USER']}")
print(f"Host: {db_settings['HOST']}")
print("-" * 50)
print("Environment Variables in current shell:")
print(f"DB_NAME: {os.environ.get('DB_NAME')}")
print(f"DB_USER: {os.environ.get('DB_USER')}")
print("-" * 50)
