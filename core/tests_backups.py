import gzip
import json
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path
import tempfile
import sys
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from core.models import Company
from core.services import backups
from core.services.backup_storage import BackupError, atomic_json, dump_database, export_offsite, read_json, sha256, verify_manifest


class BackupServiceTests(SimpleTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'backups'
        self.media = Path(self.temp.name) / 'media'
        self.media.mkdir()
        (self.media / 'foto.txt').write_text('imagen de prueba', encoding='utf-8')
        config = override_settings(
            BACKUP_ROOT=self.root, MEDIA_ROOT=self.media, BACKUP_INCLUDE_MEDIA=True,
            BACKUP_MIN_FREE_MB=0, BACKUP_EXECUTION_MODE='worker', BACKUP_OFFSITE_REPOSITORY='',
            BACKUP_RETENTION_ENABLED=True, BACKUP_RETENTION_DAYS=30, BACKUP_MIN_COPIES=3,
            BACKUP_SCHEDULE_ENABLED=True, BACKUP_SCHEDULE_HOUR=2, BACKUP_SCHEDULE_MINUTE=30,
        )
        config.enable()
        self.addCleanup(config.disable)
        dump = patch('core.services.backups.dump_database', self.fake_dump)
        dump.start()
        self.addCleanup(dump.stop)

    @staticmethod
    def fake_dump(path):
        with gzip.open(path, 'wt', encoding='utf-8') as stream:
            json.dump([{'model': 'catalog.product', 'pk': 1, 'fields': {'sku': 'TEST'}}], stream)

    def create_backup(self):
        row = backups.request_backup()
        result = backups.execute_backup_job(row['id'])
        self.assertEqual(result['status'], 'completed', result)
        return result, read_json(self.root / result['manifest_name'])

    def test_request_is_pending_until_worker_completes_and_validates_both_artifacts(self):
        row = backups.request_backup()
        self.assertEqual(row['status'], 'queued')
        self.assertEqual(backups.list_backup_sets(), [])
        self.assertEqual(backups.request_backup()['id'], row['id'])
        results = backups.process_backup_queue(schedule=False)
        self.assertEqual(results[0]['status'], 'completed')
        manifest = verify_manifest(results[0]['manifest_name'])
        self.assertEqual(len(manifest['artifacts']), 2)
        self.assertTrue(manifest['include_media'])
        self.assertEqual(manifest['verification_status'], 'verified')
        self.assertIsNone(manifest['restore_tested_at'])
        self.assertTrue(backups.get_backup_dashboard()['worker_online'])

    def test_failed_copy_preserves_previous_backups_and_records_error(self):
        first, manifest = self.create_backup()
        with patch('core.services.backups.archive_media', side_effect=OSError('disk full')):
            row = backups.request_backup()
            result = backups.execute_backup_job(row['id'])
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(len(backups.list_backup_sets()), 1)
        self.assertTrue((self.root / first['manifest_name']).is_file())
        self.assertFalse(list(self.root.glob('*.partial')))
        for item in manifest['artifacts']:
            self.assertTrue((self.root / item['name']).is_file())

    def test_detects_corruption_and_does_not_report_backup_as_healthy(self):
        result, manifest = self.create_backup()
        path = self.root / manifest['artifacts'][0]['name']
        data = path.read_bytes()
        path.write_bytes(b'X' + data[1:])
        verify = backups.request_backup(kind='verify', manifest_name=result['manifest_name'])
        self.assertEqual(backups.execute_backup_job(verify['id'])['status'], 'failed')
        self.assertTrue(backups.get_backup_dashboard()['backup_stale'])

    def test_malformed_manifest_does_not_crash_the_panel(self):
        result, manifest = self.create_backup()
        manifest['artifacts'] = ['invalid entry']
        atomic_json(self.root / result['manifest_name'], manifest)
        dashboard = backups.get_backup_dashboard()
        self.assertTrue(dashboard['backup_stale'])
        self.assertEqual(dashboard['backup_sets'], [])

    def test_interrupted_job_is_recovered_as_failed(self):
        row = backups.request_backup()
        row['status'] = 'running'
        backups._save_run(row)
        backups.process_backup_queue(schedule=False)
        self.assertEqual(backups.list_backup_jobs()[0]['status'], 'failed')

    def test_concurrent_execution_is_rejected(self):
        row = backups.request_backup()
        with backups._lock('backup-execution'):
            with self.assertRaises(backups.BackupBusy):
                backups.execute_backup_job(row['id'])
        self.assertEqual(backups.list_backup_jobs()[0]['status'], 'queued')

    def test_existing_lock_does_not_require_ownership_to_change_permissions(self):
        with backups._lock('shared-owner'):
            pass
        with patch('core.services.backups.os.chmod', side_effect=PermissionError):
            with backups._lock('shared-owner'):
                pass

    def test_download_cannot_escape_manifest_or_backup_directory(self):
        result, manifest = self.create_backup()
        with self.assertRaises(BackupError):
            backups.get_backup_download(result['manifest_name'], '../.env')
        manifest['artifacts'][0]['name'] = '../.env'
        atomic_json(self.root / result['manifest_name'], manifest)
        with self.assertRaises(BackupError):
            backups.get_backup_download(result['manifest_name'], '../.env')

    def test_retention_keeps_minimum_copies_and_legacy_backup(self):
        created = []
        for days in [45, 44, 43, 42]:
            result, manifest = self.create_backup()
            manifest['created_at'] = (backups._now() - timedelta(days=days)).isoformat()
            atomic_json(self.root / result['manifest_name'], manifest)
            created.append(result['manifest_name'])
        # A legacy set is preserved even after it has been verified.
        legacy = read_json(self.root / created[1])
        legacy['version'] = 1
        atomic_json(self.root / created[1], legacy)
        # Only three v2 sets remain: none may be pruned.
        self.assertEqual(backups._cleanup_old_backups(), [])
        self.create_backup()
        backups._cleanup_old_backups()
        self.assertFalse((self.root / created[0]).exists())
        self.assertTrue((self.root / created[1]).exists())
        self.assertEqual(len(backups.list_backup_sets()), 4)

    def test_scheduling_uses_local_time_and_does_not_repeat_daily_copy(self):
        zone = ZoneInfo('America/Argentina/Buenos_Aires')
        before = datetime(2026, 9, 29, 2, 29, tzinfo=zone)
        after = before + timedelta(minutes=2)
        with patch('core.services.backups.timezone.localtime', return_value=before), patch('core.services.backups._now', return_value=before):
            self.assertEqual(backups.process_backup_queue(), [])
        with patch('core.services.backups.timezone.localtime', return_value=after), patch('core.services.backups._now', return_value=after):
            self.assertEqual(len(backups.process_backup_queue()), 1)
            self.assertEqual(backups.process_backup_queue(), [])
        self.assertEqual(len(backups.list_backup_sets()), 1)

    def test_verifying_old_copy_does_not_make_it_recent(self):
        result, manifest = self.create_backup()
        manifest['created_at'] = (backups._now() - timedelta(days=40)).isoformat()
        atomic_json(self.root / result['manifest_name'], manifest)
        row = backups.request_backup(kind='verify', manifest_name=result['manifest_name'])
        self.assertEqual(backups.execute_backup_job(row['id'])['status'], 'completed')
        self.assertTrue(backups.get_backup_dashboard()['backup_stale'])

    def test_no_external_transfer_until_storage_is_configured(self):
        with patch('core.services.backup_storage.subprocess.run') as run:
            self.assertEqual(export_offsite('unused'), {'status': 'not_configured'})
        run.assert_not_called()

    def test_external_failure_keeps_verified_local_copy_and_warning(self):
        with patch('core.services.backups.export_offsite', side_effect=BackupError('Destino no disponible.')):
            result, manifest = self.create_backup()
        self.assertIn('warning', result)
        self.assertEqual(manifest['offsite']['status'], 'failed')
        verify_manifest(result['manifest_name'])

    def test_postgres_password_is_not_exposed_in_command_arguments(self):
        db = SimpleNamespace(vendor='postgresql', settings_dict={
            'NAME': 'test_backup', 'USER': 'test_user', 'PASSWORD': 'secret-for-test',
            'HOST': 'localhost', 'PORT': '5432', 'OPTIONS': {},
        })
        with patch('core.services.backup_storage.connection', db), patch('core.services.backup_storage.subprocess.run') as run:
            run.return_value.stdout = ''
            dump_database(self.root / 'test.dump')
        self.assertNotIn('secret-for-test', str(run.call_args.args[0]))
        self.assertEqual(run.call_args.kwargs['env']['PGPASSWORD'], 'secret-for-test')


class BackupRestoreSafetyTests(SimpleTestCase):
    def test_restore_failure_still_drops_only_the_generated_temporary_database(self):
        from core.management.commands.backup_restore_test import Command
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'test.dump'
            source.write_bytes(b'fixture')
            command = Command()
            commands = []

            def pg(*args):
                commands.append(args)
                if '--exit-on-error' in args:
                    raise BackupError('Simulated restore failure')
                return ''

            account = SimpleNamespace(pw_uid=1, pw_gid=1)
            pwd = SimpleNamespace(getpwnam=lambda _: account)
            with patch.dict(sys.modules, {'pwd': pwd}), patch('os.chown', create=True), \
                 patch.object(command, '_pg', side_effect=pg):
                with self.assertRaises(BackupError):
                    command._restore_disposable(source)
            create_sql = commands[0][-1]
            drop_sql = commands[-1][-1]
            self.assertRegex(create_sql, r'^CREATE DATABASE "flexs_restore_check_[a-f0-9]{32}" TEMPLATE template0$')
            self.assertEqual(create_sql.split('"')[1], drop_sql.split('"')[1])
            self.assertTrue(drop_sql.startswith('DROP DATABASE "flexs_restore_check_'))

    def test_restore_refuses_an_invalid_generated_database_name_before_running_commands(self):
        from core.management.commands.backup_restore_test import Command
        command = Command()
        pwd = SimpleNamespace(getpwnam=lambda _: SimpleNamespace(pw_uid=1, pw_gid=1))
        with patch.dict(sys.modules, {'pwd': pwd}), \
             patch('core.management.commands.backup_restore_test.uuid4', return_value=SimpleNamespace(hex='unsafe-name')), \
             patch.object(command, '_pg') as pg:
            with self.assertRaises(BackupError):
                command._restore_disposable(Path('unused'))
        pg.assert_not_called()


class BackupPanelTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        override = override_settings(BACKUP_ROOT=Path(self.temp.name), BACKUP_EXECUTION_MODE='worker')
        override.enable()
        self.addCleanup(override.disable)
        self.admin = User.objects.create_superuser('backup-admin', 'admin@example.com', 'test')
        self.company = Company.objects.create(name='Backup test', slug='backup-test')
        self.client.force_login(self.admin)
        session = self.client.session
        session['active_company_id'] = self.company.pk
        session.save()

    def test_panel_shows_absence_of_recent_backups_and_external_storage(self):
        response = self.client.get(reverse('admin_backup_center'))
        self.assertContains(response, 'Hace falta una copia reciente')
        self.assertContains(response, 'Copia externa pendiente')
        self.assertContains(response, 'Sin señal')

    def test_manual_request_reports_pending_without_claiming_success_or_using_celery(self):
        with patch('admin_panel.views.system.create_automatic_backup_task.delay') as task:
            response = self.client.post(reverse('admin_backup_run'), follow=True)
        self.assertContains(response, 'Solicitud guardada')
        self.assertContains(response, 'Pendiente')
        self.assertEqual(backups.list_backup_jobs()[0]['status'], 'queued')
        task.assert_not_called()

    def test_unprivileged_staff_cannot_download_or_request_backups(self):
        staff = User.objects.create_user('limited-backup-staff', password='test', is_staff=True)
        self.client.force_login(staff)
        self.assertIn(self.client.post(reverse('admin_backup_run')).status_code, (302, 403))
        response = self.client.get(reverse('admin_backup_center'), {'download': 'anything', 'file': '.env'})
        self.assertIn(response.status_code, (302, 403))
        self.assertEqual(backups.list_backup_jobs(), [])
