"""Loopback-only backup UI preview with disposable data and no external connection."""
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['DJANGO_SETTINGS_MODULE'] = 'flexs_project.settings.test'


def main():
    from django.conf import settings
    with TemporaryDirectory(prefix='flexs-backup-preview-') as directory:
        root = Path(directory)
        settings.DATABASES['default']['NAME'] = str(root / 'preview.sqlite3')
        settings.MEDIA_ROOT = root / 'media'
        settings.MEDIA_ROOT.mkdir()
        settings.STATIC_ROOT = root / 'static'
        settings.STATIC_ROOT.mkdir()
        settings.BACKUP_ROOT = root / 'backups'
        settings.BACKUP_OFFSITE_REPOSITORY = ''
        settings.BACKUP_EXECUTION_MODE = 'worker'
        settings.BACKUP_INCLUDE_MEDIA = True
        settings.DEBUG = True
        settings.SESSION_COOKIE_SECURE = False
        settings.CSRF_COOKIE_SECURE = False
        index = settings.MIDDLEWARE.index('django.contrib.auth.middleware.AuthenticationMiddleware')
        settings.MIDDLEWARE.insert(index + 1, 'scripts.preview_catalog_frontend.PreviewIdentityMiddleware')
        import django
        django.setup()
        from django.core.management import call_command
        from django.contrib.auth import get_user_model
        from core.models import Company
        from core.services.backups import request_backup, process_backup_queue

        call_command('migrate', verbosity=0, interactive=False)
        company, _ = Company.objects.get_or_create(slug='flexs', defaults={'name': 'Flexs (prueba)'})
        admin = get_user_model().objects.create_superuser(username='preview-backups', password=None)
        settings.PREVIEW_USER_IDS = {'admin': admin.pk, 'client': admin.pk}
        settings.PREVIEW_COMPANY_ID = company.pk
        request_backup(source='manual', requested_by='preview-backups')
        process_backup_queue(schedule=False)
        print('LOCAL ONLY http://127.0.0.1:8774/admin-panel/configuracion/backups/', flush=True)
        call_command('runserver', '127.0.0.1:8774', use_reloader=False, use_threading=True, insecure=True)


if __name__ == '__main__':
    main()
