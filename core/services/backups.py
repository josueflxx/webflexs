"""Durable backup jobs; state survives application and worker restarts."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone as datetime_timezone
import logging
import os
import re
import shutil
import time
from uuid import uuid4

from django.conf import settings
from django.db import connection
from django.utils import timezone

from .backup_storage import (
    BackupError, archive_media, atomic_json, backup_root, dump_database,
    export_offsite, load_manifest, read_json, safe_path, sha256, validate_artifact, verify_manifest,
)

logger = logging.getLogger(__name__)
ACTIVE_STATUSES = {'queued', 'running'}
RUN_ID = re.compile(r'^[a-f0-9]{32}$')


class BackupBusy(BackupError):
    pass


def _now():
    return datetime.now(datetime_timezone.utc)


def _date(value):
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=datetime_timezone.utc)
    except (TypeError, ValueError):
        return None


def get_backup_root():
    return backup_root(create=True)


@contextmanager
def _lock(name):
    path = safe_path(f'.{name}.lock', must_exist=False)
    backup_root(create=True)
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o660)
        os.chmod(path, 0o660)
    except FileExistsError:
        # Web requests and the worker may have different owners in the same group.
        # Only the creator may chmod; opening an existing group-writable lock is enough.
        fd = os.open(path, os.O_RDWR)
    stream = os.fdopen(fd, 'r+b')
    acquired = False
    try:
        if path.stat().st_size == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except (OSError, BlockingIOError) as exc:
            raise BackupBusy('Ya hay una operación de backup en curso. Esperá a que termine.') from exc
        yield
    finally:
        if acquired:
            stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()


def _run_path(run_id):
    if not RUN_ID.fullmatch(str(run_id)):
        raise BackupError('La solicitud de backup no es válida.')
    return safe_path(f'run_{run_id}.json', must_exist=False)


def _save_run(row):
    atomic_json(_run_path(row['id']), row)


def list_backup_jobs(limit=50):
    root = backup_root()
    rows = []
    if not root.exists():
        return rows
    for path in root.glob('run_*.json'):
        if path.is_symlink():
            continue
        try:
            row = read_json(path)
            if RUN_ID.fullmatch(str(row.get('id', ''))):
                rows.append(row)
        except (OSError, ValueError, BackupError):
            continue
    rows.sort(key=lambda row: row.get('requested_at', ''), reverse=True)
    return rows if limit is None else rows[:limit]


def request_backup(*, include_media=None, source='manual', requested_by='', kind='backup', manifest_name=''):
    if kind not in {'backup', 'verify'}:
        raise BackupError('La operación solicitada no es válida.')
    if kind == 'verify':
        load_manifest(manifest_name)
    with _lock('backup-requests'):
        jobs = list_backup_jobs(limit=None)
        for row in jobs:
            if row.get('status') in ACTIVE_STATUSES and row.get('kind') == kind:
                if kind == 'backup' or row.get('manifest_name') == manifest_name:
                    return row
        if sum(row.get('status') in ACTIVE_STATUSES for row in jobs) >= 20:
            raise BackupError('Hay demasiadas solicitudes pendientes. Esperá a que termine el servicio.')
        row = {
            'id': uuid4().hex, 'kind': kind, 'source': source,
            'requested_by': str(requested_by)[:150], 'requested_at': _now().isoformat(),
            'include_media': settings.BACKUP_INCLUDE_MEDIA if include_media is None else bool(include_media),
            'status': 'queued', 'stage': 'Esperando al servicio', 'manifest_name': manifest_name,
        }
        _save_run(row)
        return row


def _set_stage(row, stage):
    row['stage'] = stage
    _save_run(row)


def _files_present(manifest):
    try:
        return all(safe_path(item['name']).stat().st_size == item['size'] for item in manifest['artifacts'])
    except (BackupError, KeyError, OSError):
        return False


def _cleanup_old_backups():
    if not settings.BACKUP_RETENTION_ENABLED:
        return []
    rows = list_backup_sets(limit=None)
    verified = [row for row in rows if row.get('version') == 2 and row.get('verified_at')
                and row.get('verification_status') == 'verified' and _files_present(row)]
    protected = {row['manifest_name'] for row in verified[:settings.BACKUP_MIN_COPIES]}
    cutoff = _now() - timedelta(days=settings.BACKUP_RETENTION_DAYS)
    removed = []
    for row in verified:
        created_at = _date(row.get('created_at'))
        if row['manifest_name'] in protected or not created_at or created_at >= cutoff:
            continue
        if settings.BACKUP_OFFSITE_REPOSITORY and row.get('offsite', {}).get('status') != 'completed':
            continue
        # Only delete a complete, recognized set. Legacy and damaged sets stay untouched.
        try:
            paths = [safe_path(item['name']) for item in row['artifacts']]
            paths.append(safe_path(row['manifest_name']))
            expected_prefix = row['manifest_name'].removesuffix('_manifest.json')
            if any(not path.name.startswith(expected_prefix + '_') for path in paths):
                continue
        except (BackupError, KeyError):
            continue
        for path in paths:
            path.unlink()
            removed.append(path.name)
    return removed


def _build_backup(row):
    root = backup_root(create=True)
    if shutil.disk_usage(root).free < settings.BACKUP_MIN_FREE_MB * 1024 * 1024:
        raise BackupError('No hay espacio libre suficiente para iniciar una copia segura.')
    prefix = f"flexs_{_now().strftime('%Y%m%d_%H%M%S')}_{row['id'][:8]}"
    database_format = 'postgresql_custom' if connection.vendor == 'postgresql' else 'django_json_gzip'
    extension = 'dump' if database_format == 'postgresql_custom' else 'json.gz'
    specs = [(f'{prefix}_database.{extension}', database_format, dump_database)]
    if row['include_media']:
        specs.append((f'{prefix}_media.tar.gz', 'media_tar_gzip', archive_media))
    artifacts, created = [], []
    manifest_path = root / f'{prefix}_manifest.json'
    try:
        for name, fmt, builder in specs:
            final_path = root / name
            partial = root / (name + '.partial')
            created.append(partial)
            fd = os.open(partial, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o660)
            os.close(fd)
            _set_stage(row, 'Guardando base de datos' if fmt != 'media_tar_gzip' else 'Guardando imágenes y archivos')
            builder(partial)
            _set_stage(row, 'Verificando los archivos creados')
            validate_artifact(partial, fmt)
            artifact = {'name': name, 'size': partial.stat().st_size, 'sha256': sha256(partial), 'format': fmt}
            os.replace(partial, final_path)
            created.append(final_path)
            artifacts.append(artifact)
        manifest = {
            'version': 2, 'created_at': _now().isoformat(), 'database_vendor': connection.vendor,
            'include_media': row['include_media'], 'artifacts': artifacts, 'run_id': row['id'],
            'verification_status': 'verified', 'verified_at': _now().isoformat(),
            'restore_tested_at': None, 'offsite': {'status': 'not_configured'},
        }
        atomic_json(manifest_path, manifest)
    except Exception:
        # These paths belong only to this failed attempt, never to older backup sets.
        for path in created:
            if path.exists() and path.parent == root:
                path.unlink()
        raise
    _set_stage(row, 'Comprobando copia externa')
    try:
        manifest['offsite'] = export_offsite(manifest_path.name)
    except BackupError as exc:
        manifest['offsite'] = {'status': 'failed', 'error': str(exc)}
        row['warning'] = 'La copia local está verificada, pero falló el envío de la copia externa.'
    atomic_json(manifest_path, manifest)
    try:
        removed = _cleanup_old_backups()
    except OSError:
        removed = []
        row['warning'] = 'La copia está verificada; quedó pendiente la limpieza de respaldos antiguos.'
    row['manifest_name'] = manifest_path.name
    row['size'] = sum(item['size'] for item in artifacts)
    row['removed'] = removed
    return manifest


def execute_backup_job(run_id):
    with _lock('backup-execution'):
        row = read_json(_run_path(run_id))
        if row.get('status') != 'queued':
            return row
        # Owning the OS lock proves a previously running process is no longer alive.
        for previous in list_backup_jobs(limit=None):
            if previous.get('status') == 'running':
                previous.update(status='failed', finished_at=_now().isoformat(),
                                error='El proceso se interrumpió antes de completar la copia.', stage='Interrumpido')
                _save_run(previous)
        started = time.monotonic()
        row.update(status='running', started_at=_now().isoformat(), stage='Preparando copia')
        _save_run(row)
        try:
            if row['kind'] == 'verify':
                _set_stage(row, 'Verificando integridad')
                try:
                    manifest = verify_manifest(row['manifest_name'])
                except Exception:
                    manifest = load_manifest(row['manifest_name'])
                    manifest.update(verification_status='failed', verification_failed_at=_now().isoformat())
                    atomic_json(safe_path(row['manifest_name']), manifest)
                    raise
                manifest.update(verification_status='verified', verified_at=_now().isoformat())
                atomic_json(safe_path(row['manifest_name']), manifest)
            else:
                _build_backup(row)
            row.update(status='completed', stage='Completado')
        except Exception as exc:
            logger.error('Backup %s failed (%s)', row['id'], type(exc).__name__)
            row.update(status='failed', stage='Falló',
                       error=str(exc) if isinstance(exc, BackupError) else
                       'La copia no pudo completarse. Revisá el registro del servicio de backups.')
        row.update(finished_at=_now().isoformat(), duration_seconds=round(time.monotonic() - started, 1))
        _save_run(row)
        return row


def create_system_backup(*, include_media=None):
    row = request_backup(include_media=include_media, source='command')
    result = execute_backup_job(row['id'])
    if result['status'] != 'completed':
        raise BackupError(result.get('error', 'La copia todavía no terminó.'))
    manifest = load_manifest(result['manifest_name'])
    return {
        'manifest': safe_path(result['manifest_name']),
        'artifacts': [safe_path(item['name']) for item in manifest['artifacts']],
        'removed': result.get('removed', []),
    }


def list_backup_sets(limit=20):
    rows = []
    root = backup_root()
    if not root.exists():
        return rows
    for path in root.glob('flexs_*_manifest.json'):
        try:
            manifest = load_manifest(path.name)
            rows.append({**manifest, 'manifest_name': path.name})
        except (OSError, ValueError, BackupError):
            continue
    rows.sort(key=lambda row: row.get('created_at', ''), reverse=True)
    return rows if limit is None else rows[:limit]


def get_backup_download(manifest_name, filename):
    manifest = load_manifest(manifest_name)
    allowed = {row.get('name') for row in manifest['artifacts']}
    allowed.add(manifest_name)
    if filename not in allowed:
        raise BackupError('El archivo no pertenece a este respaldo.')
    return safe_path(filename)


def _schedule_due_backup():
    if not settings.BACKUP_SCHEDULE_ENABLED:
        return
    now = timezone.localtime()
    slot = now.replace(hour=settings.BACKUP_SCHEDULE_HOUR, minute=settings.BACKUP_SCHEDULE_MINUTE,
                       second=0, microsecond=0)
    if now < slot:
        return
    jobs = list_backup_jobs(limit=None)
    for row in jobs:
        requested = _date(row.get('requested_at'))
        if row.get('kind') != 'backup' or not requested:
            continue
        if row.get('status') in ACTIVE_STATUSES:
            return
        if requested >= slot and row.get('status') == 'completed' and row.get('include_media') == settings.BACKUP_INCLUDE_MEDIA:
            return
        if row.get('source') == 'scheduled' and requested > now - timedelta(hours=1):
            return
    request_backup(source='scheduled')


def process_backup_queue(*, schedule=True):
    with _lock('backup-worker'):
        heartbeat_path = backup_root(create=True) / '.worker-status.json'
        atomic_json(heartbeat_path, {'seen_at': _now().isoformat(), 'state': 'running'})
        results = []
        try:
            # Recover interrupted jobs even if no new request has arrived.
            try:
                with _lock('backup-execution'):
                    for row in list_backup_jobs(limit=None):
                        if row.get('status') == 'running':
                            row.update(status='failed', stage='Interrumpido', finished_at=_now().isoformat(),
                                       error='El servicio se interrumpió. Podés solicitar una nueva copia.')
                            _save_run(row)
            except BackupBusy:
                return results
            if schedule:
                _schedule_due_backup()
            jobs = [row for row in list_backup_jobs(limit=None) if row.get('status') == 'queued']
            for row in reversed(jobs[-3:]):
                try:
                    results.append(execute_backup_job(row['id']))
                except BackupBusy:
                    break
        finally:
            atomic_json(heartbeat_path, {'seen_at': _now().isoformat(), 'state': 'idle'})
        return results


def get_backup_dashboard():
    rows = list_backup_sets(limit=30)
    now = _now()
    for row in rows:
        row['created_datetime'] = _date(row.get('created_at'))
        row['verified_datetime'] = _date(row.get('verified_at'))
        row['restore_datetime'] = _date(row.get('restore_tested_at'))
        row['size'] = sum(item.get('size', 0) for item in row['artifacts'])
        row['files_present'] = _files_present(row)
        row['is_verified'] = bool(row.get('verified_at') and row.get('verification_status') == 'verified' and row['files_present'])
        row['is_legacy'] = row.get('version', 1) < 2
        row['offsite_status'] = row.get('offsite', {}).get('status', 'not_configured')
    jobs = list_backup_jobs(limit=20)
    for job in jobs:
        job['requested_datetime'] = _date(job.get('requested_at'))
        job['is_active'] = job.get('status') in ACTIVE_STATUSES
    last_good = next((row for row in rows if row['is_verified']), None)
    age = (now - last_good['created_datetime']).total_seconds() / 3600 if last_good and last_good['created_datetime'] else None
    stale = age is None or age > settings.BACKUP_MAX_AGE_HOURS
    heartbeat = {}
    try:
        path = safe_path('.worker-status.json')
        heartbeat = read_json(path)
    except (BackupError, OSError, ValueError):
        pass
    seen = _date(heartbeat.get('seen_at'))
    worker_online = bool(seen and (now - seen).total_seconds() < 300)
    if not worker_online and heartbeat.get('state') == 'running' and seen:
        worker_online = (now - seen).total_seconds() < settings.BACKUP_TIMEOUT_SECONDS + 120
    local_now = timezone.localtime()
    next_run = local_now.replace(hour=settings.BACKUP_SCHEDULE_HOUR, minute=settings.BACKUP_SCHEDULE_MINUTE,
                                 second=0, microsecond=0)
    if next_run <= local_now:
        next_run += timedelta(days=1)
    return {
        'backup_sets': rows, 'backup_jobs': jobs, 'last_good_backup': last_good,
        'backup_stale': stale, 'max_age_hours': settings.BACKUP_MAX_AGE_HOURS,
        'has_active_backup_jobs': any(row['is_active'] for row in jobs),
        'worker_online': worker_online, 'worker_seen_at': seen,
        'execution_mode': settings.BACKUP_EXECUTION_MODE, 'next_backup_at': next_run,
        'schedule_enabled': settings.BACKUP_SCHEDULE_ENABLED,
        'include_media': settings.BACKUP_INCLUDE_MEDIA,
        'retention_days': settings.BACKUP_RETENTION_DAYS, 'minimum_copies': settings.BACKUP_MIN_COPIES,
        'retention_enabled': settings.BACKUP_RETENTION_ENABLED,
        'offsite_configured': bool(settings.BACKUP_OFFSITE_REPOSITORY),
        'latest_failed_job': next((row for row in jobs if row.get('status') == 'failed'), None),
    }
