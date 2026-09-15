import os
import sys
from pathlib import Path
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from tempfile import TemporaryDirectory
PROJECT = Path.cwd()
sys.path.insert(0, str(PROJECT))
os.environ['DJANGO_SETTINGS_MODULE'] = 'flexs_project.settings.test'
from django.conf import settings
with TemporaryDirectory(prefix='flexs-clamp-preview-') as directory:
    settings.DATABASES['default']['NAME'] = ':memory:'
    settings.DEBUG = True
    settings.SECURE_SSL_REDIRECT = False
    settings.SESSION_COOKIE_SECURE = False
    import django
    django.setup()
    from django.core.management import call_command
    from django.contrib.auth import get_user_model
    from django.test import Client
    from django.contrib.staticfiles import finders
    call_command('migrate', verbosity=0, interactive=False)
    user = get_user_model().objects.create_superuser(username='clamp-preview', password=None)
    client = Client()
    client.force_login(user)
    from core.services.company_context import get_default_company
    session = client.session
    session['active_company_id'] = get_default_company().pk
    session.save()
    response = client.get('/?theme=blueprint')
    from django.db import connections
    # In-memory preview stays isolated from project data.
    assert response.status_code == 200, (response.status_code, response.get('Location'))
    html = response.content
    class Handler(SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith('/static/'):
                return super().do_GET()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(html)
        def translate_path(self, path):
            from urllib.parse import urlsplit, unquote
            name = unquote(urlsplit(path).path).removeprefix('/static/')
            if '..' in Path(name).parts:
                return str(Path(directory) / 'missing')
            return finders.find(name) or str(Path(directory) / 'missing')
    print('CLAMP PREVIEW READY http://127.0.0.1:8773', flush=True)
    ThreadingHTTPServer(('127.0.0.1', 8773), Handler).serve_forever()


