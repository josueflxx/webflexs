import os, django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'flexs_project.settings')

from django.conf import settings
print("DATABASES:", settings.DATABASES)
