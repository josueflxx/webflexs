import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'flexs_project.settings')
django.setup()
from django.test import RequestFactory
from catalog.views import catalog_v3

rf = RequestFactory()
req = rf.get('/')
req.user = type('User', (), {'is_authenticated': False, 'is_staff': False, 'is_superuser': False})()
req.session = {}

try:
    res = catalog_v3(req)
    print("STATUS:", res.status_code)
except Exception as e:
    import traceback
    traceback.print_exc()
