"""Verify a stored backup without restoring application data."""

from django.core.management.base import BaseCommand, CommandError

from core.services.backups import BackupError, execute_backup_job, request_backup


class Command(BaseCommand):
    help = 'Verifica tamaño, checksum y lectura completa de los archivos de un respaldo.'

    def add_arguments(self, parser):
        parser.add_argument('manifest', help='Nombre del manifiesto dentro de BACKUP_ROOT.')

    def handle(self, *args, **options):
        try:
            row = request_backup(kind='verify', manifest_name=options['manifest'], source='command')
            result = execute_backup_job(row['id'])
        except BackupError as exc:
            raise CommandError(str(exc)) from exc
        if result['status'] != 'completed':
            raise CommandError(result.get('error', 'La verificación no terminó.'))
        self.stdout.write(self.style.SUCCESS('Integridad verificada. Esto no reemplaza una prueba de restauración.'))
