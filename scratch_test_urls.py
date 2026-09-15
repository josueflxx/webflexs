import os, django

os.environ['DJANGO_SETTINGS_MODULE'] = 'flexs_project.settings.local'
django.setup()

from django.test import Client

client = Client(HTTP_HOST='flexsrepuestos.shop')

for url in ['/', '/admin-panel/', '/admin-panel/clientes/', '/catalogo/']:
    try:
        res = client.get(url, follow=True)
        print(f"URL: {url} -> STATUS: {res.status_code}")
        if res.status_code == 500:
            print(f"500 HTML snippet for {url}:\n{res.content.decode('utf-8', errors='ignore')[:600]}")
    except Exception as e:
        print(f"URL: {url} EXCEPTION: {e}")
        import traceback
        traceback.print_exc()
