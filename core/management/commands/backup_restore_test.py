"""Restore a backup into a disposable local PostgreSQL database, never the live database."""

import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from uuid import uuid4

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from core.services.backups import _lock, _now, list_backup_sets
from core.services.backup_storage import BackupError, atomic_json, safe_path, verify_manifest


class Command(BaseCommand):
    help = 'Prueba una restauración aislada de PostgreSQL local. Requiere root; nunca restaura sobre la base de la aplicación.'

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument('--manifest', help='Nombre del manifiesto dentro de BACKUP_ROOT.')
        group.add_argument('--latest', action='store_true', help='Usa la última copia verificada de PostgreSQL.')

    def handle(self, *args, **options):
        if os.name != 'posix' or os.geteuid() != 0:
            raise CommandError('Esta prueba se ejecuta como root en el servidor Linux de PostgreSQL local.')
        if connection.vendor != 'postgresql' or connection.settings_dict.get('HOST') not in ('', 'localhost', '127.0.0.1', '/var/run/postgresql'):
            raise CommandError('La prueba automática requiere PostgreSQL en este mismo servidor.')
        try:
            with _lock('backup-execution'):
                name = options['manifest']
                if options['latest']:
                    name = next((row['manifest_name'] for row in list_backup_sets(limit=None)
                                 if row.get('verification_status') == 'verified'
                                 and any(item.get('format') == 'postgresql_custom' for item in row['artifacts'])), None)
                if not name:
                    raise BackupError('Todavía no hay una copia verificada de PostgreSQL para probar.')
                manifest = verify_manifest(name)
                database = next((item for item in manifest['artifacts'] if item.get('format') == 'postgresql_custom'), None)
                if not database:
                    raise BackupError('Esta prueba requiere una copia nativa de PostgreSQL.')
                manifest['restore_status'] = 'running'
                atomic_json(safe_path(name), manifest)
                try:
                    result = self._restore_disposable(safe_path(database['name']))
                except Exception:
                    manifest.update(restore_status='failed', restore_failed_at=_now().isoformat())
                    atomic_json(safe_path(name), manifest)
                    raise
                manifest.update(restore_status='verified', restore_tested_at=_now().isoformat(), restore_result=result)
                atomic_json(safe_path(name), manifest)
        except BackupError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(
            f"Recuperación verificada: {result['tables']} tablas, {result['products']} productos. Base temporal eliminada."
        ))

    def _pg(self, program, *args):
        command = ['runuser', '-u', 'postgres', '--', program,
                   '--host=/var/run/postgresql', f"--port={connection.settings_dict.get('PORT') or '5432'}", *args]
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=True,
                                    timeout=settings.BACKUP_TIMEOUT_SECONDS)
        except (OSError, subprocess.SubprocessError) as exc:
            raise BackupError('La prueba de recuperación de PostgreSQL falló. La base de producción no fue modificada.') from exc
        return result.stdout.strip()

    def _restore_disposable(self, source):
        import pwd
        account = pwd.getpwnam('postgres')
        dbname = 'flexs_restore_check_' + uuid4().hex
        if not re.fullmatch(r'flexs_restore_check_[a-f0-9]{32}', dbname) or dbname == connection.settings_dict['NAME']:
            raise BackupError('Nombre inseguro para la base temporal.')
        temporary = Path(tempfile.mkdtemp(prefix='webflexs-restore-'))
        archive = temporary / 'database.dump'
        created = False
        try:
            shutil.copyfile(source, archive)
            os.chown(archive, account.pw_uid, account.pw_gid)
            os.chmod(archive, 0o600)
            os.chown(temporary, account.pw_uid, account.pw_gid)
            os.chmod(temporary, 0o700)
            self._pg('psql', '--no-psqlrc', '--set=ON_ERROR_STOP=1', '--dbname=postgres', '-c',
                     f'CREATE DATABASE "{dbname}" TEMPLATE template0')
            created = True
            self._pg(settings.BACKUP_PG_RESTORE_BINARY, '--exit-on-error', '--no-owner', '--no-privileges',
                     f'--dbname={dbname}', str(archive))
            tables = self._pg('psql', '--no-psqlrc', '--set=ON_ERROR_STOP=1', f'--dbname={dbname}', '-Atc',
                              "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'")
            products = self._pg('psql', '--no-psqlrc', '--set=ON_ERROR_STOP=1', f'--dbname={dbname}', '-Atc',
                                'SELECT count(*) FROM catalog_product')
            return {'tables': int(tables), 'products': int(products)}
        finally:
            try:
                if created:
                    # Only the exact UUID-named database created above can reach this branch.
                    self._pg('psql', '--no-psqlrc', '--set=ON_ERROR_STOP=1', '--dbname=postgres', '-c',
                             f'DROP DATABASE "{dbname}" WITH (FORCE)')
            finally:
                if archive.is_file():
                    archive.unlink()
                temporary.rmdir()
