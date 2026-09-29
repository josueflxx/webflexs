"""Process persisted backup requests and the daily backup schedule."""

from django.core.management.base import BaseCommand, CommandError

from core.services.backups import BackupBusy, process_backup_queue


class Command(BaseCommand):
    help = 'Procesa la cola de backups una vez. Ejecutar cada minuto con un timer dedicado.'

    def add_arguments(self, parser):
        parser.add_argument('--no-schedule', action='store_true', help='Procesa solo solicitudes pendientes.')

    def handle(self, *args, **options):
        try:
            results = process_backup_queue(schedule=not options['no_schedule'])
        except BackupBusy:
            self.stdout.write('Ya hay un ejecutor de backups activo.')
            return
        failed = False
        for row in results:
            self.stdout.write(f"{row['id']}: {row['status']}")
            failed = failed or row['status'] == 'failed'
        if failed:
            raise CommandError('Una operación de backup falló. El motivo quedó registrado en el panel.')
