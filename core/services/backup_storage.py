"""Backup artifacts, validation and optional encrypted off-server copies."""

import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile

from django.conf import settings
from django.core.management import call_command
from django.db import connection


class BackupError(Exception):
    """A safe, user-facing backup error (never include connection secrets)."""


def backup_root(*, create=False):
    root = Path(settings.BACKUP_ROOT).expanduser().resolve()
    if create:
        root.mkdir(parents=True, exist_ok=True, mode=0o770)
    return root


def safe_path(name, *, must_exist=True):
    if not isinstance(name, str) or not name or Path(name).name != name or '/' in name or '\\' in name:
        raise BackupError('El archivo de respaldo no es válido.')
    root = backup_root()
    path = root / name
    if path.is_symlink() or path.resolve().parent != root:
        raise BackupError('El archivo de respaldo no es válido.')
    if must_exist and not path.is_file():
        raise BackupError('No se encontró el archivo del respaldo.')
    return path


def atomic_json(path, data):
    root = backup_root(create=True)
    if path.parent != root:
        raise BackupError('Ubicación de respaldo inválida.')
    fd, temporary = tempfile.mkstemp(prefix='.backup-', suffix='.tmp', dir=root)
    try:
        os.chmod(temporary, 0o660)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path):
    with path.open(encoding='utf-8') as stream:
        data = json.load(stream)
    if not isinstance(data, dict):
        raise BackupError('El registro del respaldo está dañado.')
    return data


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _run(argv, *, env=None, timeout=None):
    try:
        return subprocess.run(
            argv, env=env, capture_output=True, text=True, check=True,
            timeout=timeout or settings.BACKUP_TIMEOUT_SECONDS,
        ).stdout
    except FileNotFoundError as exc:
        raise BackupError('Falta una herramienta necesaria en el servidor. Revisá la configuración del servicio.') from exc
    except subprocess.TimeoutExpired as exc:
        raise BackupError('La operación superó el tiempo disponible. Revisá el espacio y el estado del servidor.') from exc
    except subprocess.CalledProcessError as exc:
        # stderr may contain connection information; do not expose it in the UI/logs.
        raise BackupError('La herramienta de respaldo no pudo completar la operación. Revisá permisos y conexión.') from exc


def postgres_environment():
    db = connection.settings_dict
    env = os.environ.copy()
    for key, field in [('PGDATABASE', 'NAME'), ('PGUSER', 'USER'), ('PGPASSWORD', 'PASSWORD'),
                       ('PGHOST', 'HOST'), ('PGPORT', 'PORT')]:
        if db.get(field) is not None:
            env[key] = str(db[field])
    options = db.get('OPTIONS', {})
    for option in ('sslmode', 'sslcert', 'sslkey', 'sslrootcert', 'service', 'passfile'):
        if options.get(option):
            env['PG' + option.upper()] = str(options[option])
    env['PGCONNECT_TIMEOUT'] = '15'
    return env


def dump_database(path):
    if connection.vendor == 'postgresql':
        _run([
            settings.BACKUP_PG_DUMP_BINARY, '--format=custom', '--no-owner', '--no-acl',
            '--file', str(path),
        ], env=postgres_environment())
        return 'postgresql_custom'
    with gzip.open(path, 'wt', encoding='utf-8') as output:
        call_command(
            'dumpdata', '--natural-foreign', '--natural-primary', '--exclude=contenttypes',
            '--exclude=auth.permission', '--exclude=sessions', stdout=output, verbosity=0,
        )
    return 'django_json_gzip'


def archive_media(path):
    root = Path(settings.MEDIA_ROOT).resolve()
    if not root.is_dir():
        raise BackupError('No se encontró la carpeta de imágenes y archivos. La copia completa no pudo terminar.')
    if backup_root() == root or root in backup_root().parents:
        raise BackupError('La carpeta de backups debe estar fuera de la carpeta de imágenes.')
    with tarfile.open(path, 'w:gz') as archive:
        archive.add(root, arcname='media', filter=lambda entry: None if entry.issym() or entry.islnk() else entry)


def validate_artifact(path, artifact_format):
    if artifact_format == 'postgresql_custom':
        _run([settings.BACKUP_PG_RESTORE_BINARY, '--list', str(path)])
    elif artifact_format == 'django_json_gzip':
        with gzip.open(path, 'rt', encoding='utf-8') as stream:
            data = json.load(stream)
        if not isinstance(data, list):
            raise BackupError('La exportación de datos no tiene un formato válido.')
    elif artifact_format == 'media_tar_gzip':
        with tarfile.open(path, 'r:gz') as archive:
            for member in archive:
                parts = Path(member.name).parts
                if not parts or parts[0] != 'media' or '..' in parts or member.issym() or member.islnk():
                    raise BackupError('El archivo de imágenes contiene una ruta inválida.')
                if member.isfile():
                    with archive.extractfile(member) as stream:
                        for _ in iter(lambda: stream.read(1024 * 1024), b''):
                            pass
    else:
        raise BackupError('El formato de este archivo no se puede verificar.')


def load_manifest(name):
    if not isinstance(name, str) or not name.startswith('flexs_') or not name.endswith('_manifest.json'):
        raise BackupError('El respaldo seleccionado no es válido.')
    manifest = read_json(safe_path(name))
    artifacts = manifest.get('artifacts')
    if not isinstance(artifacts, list) or not artifacts:
        raise BackupError('El respaldo no tiene archivos registrados.')
    if not isinstance(manifest.get('version', 1), int) or not isinstance(manifest.get('created_at'), str):
        raise BackupError('El registro del respaldo está dañado.')
    if not isinstance(manifest.get('offsite', {}), dict):
        raise BackupError('El registro de copia externa está dañado.')
    for item in artifacts:
        if (not isinstance(item, dict) or not isinstance(item.get('size'), int)
                or item['size'] < 0 or not re.fullmatch(r'[a-f0-9]{64}', str(item.get('sha256', '')))):
            raise BackupError('El registro de archivos está dañado.')
        safe_path(item.get('name'), must_exist=False)
    return manifest


def artifact_format(artifact):
    if artifact.get('format'):
        return artifact['format']
    name = artifact.get('name', '')
    if name.endswith('_database.json.gz'):
        return 'django_json_gzip'
    if name.endswith('_media.tar.gz'):
        return 'media_tar_gzip'
    raise BackupError('No se reconoce el formato del respaldo antiguo.')


def verify_manifest(name):
    manifest = load_manifest(name)
    for artifact in manifest['artifacts']:
        path = safe_path(artifact.get('name'))
        if path.stat().st_size != artifact.get('size') or sha256(path) != artifact.get('sha256'):
            raise BackupError('La verificación detectó un archivo incompleto o modificado. Conservá otra copia válida.')
        try:
            validate_artifact(path, artifact_format(artifact))
        except BackupError:
            raise
        except (OSError, ValueError, tarfile.TarError) as exc:
            raise BackupError('No se pudo leer completamente uno de los archivos del respaldo.') from exc
    return manifest


def export_offsite(manifest_name):
    """Restic encrypts client-side; no data leaves the server until configured."""
    repository = settings.BACKUP_OFFSITE_REPOSITORY
    password_file = settings.BACKUP_OFFSITE_PASSWORD_FILE
    if not repository:
        return {'status': 'not_configured'}
    if not password_file or not Path(password_file).is_file():
        raise BackupError('Falta configurar la clave de cifrado para la copia externa.')
    if not shutil.which(settings.BACKUP_RESTIC_BINARY):
        raise BackupError('Falta instalar la herramienta para las copias externas cifradas.')
    manifest = load_manifest(manifest_name)
    paths = [str(safe_path(row['name'])) for row in manifest['artifacts']]
    paths.append(str(safe_path(manifest_name)))
    env = os.environ.copy()
    env['RESTIC_REPOSITORY'] = repository
    env['RESTIC_PASSWORD_FILE'] = password_file
    output = _run([
        settings.BACKUP_RESTIC_BINARY, '--no-cache', 'backup', '--json', '--tag', 'webflexs', *paths,
    ], env=env)
    for line in reversed(output.splitlines()):
        try:
            result = json.loads(line)
        except ValueError:
            continue
        if result.get('message_type') == 'summary' and result.get('snapshot_id'):
            return {'status': 'completed', 'snapshot_id': result['snapshot_id']}
    raise BackupError('El almacenamiento externo no confirmó la copia cifrada.')
