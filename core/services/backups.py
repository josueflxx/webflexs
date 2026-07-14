"""Portable, compressed and verifiable application backups."""

import gzip
import hashlib
import json
import tarfile
from datetime import datetime, timezone as datetime_timezone
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.db import connection


def get_backup_root():
    configured = Path(getattr(settings, "BACKUP_ROOT", settings.BASE_DIR / "backups" / "automatic"))
    root = configured.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _dump_database(target_path):
    with gzip.open(target_path, "wt", encoding="utf-8") as output:
        call_command(
            "dumpdata",
            "--natural-foreign",
            "--natural-primary",
            "--exclude=contenttypes",
            "--exclude=auth.permission",
            "--exclude=sessions",
            stdout=output,
            verbosity=0,
        )


def _archive_media(target_path):
    media_root = Path(settings.MEDIA_ROOT).resolve()
    with tarfile.open(target_path, "w:gz") as archive:
        if media_root.exists():
            archive.add(media_root, arcname="media", recursive=True)


def _cleanup_old_backups(root):
    retention_days = max(int(getattr(settings, "BACKUP_RETENTION_DAYS", 30)), 1)
    cutoff = datetime.now(datetime_timezone.utc).timestamp() - (retention_days * 86400)
    removed = []
    for path in root.glob("flexs_*"):
        resolved = path.resolve()
        if resolved.parent != root or not resolved.is_file():
            continue
        if resolved.stat().st_mtime < cutoff:
            resolved.unlink()
            removed.append(resolved.name)
    return removed


def create_system_backup(*, include_media=None):
    """Create database/media artifacts plus a checksum manifest."""
    root = get_backup_root()
    include_media = (
        bool(getattr(settings, "BACKUP_INCLUDE_MEDIA", True))
        if include_media is None
        else bool(include_media)
    )
    stamp = datetime.now(datetime_timezone.utc).strftime("%Y%m%d_%H%M%S")
    prefix = f"flexs_{stamp}"
    database_path = root / f"{prefix}_database.json.gz"
    media_path = root / f"{prefix}_media.tar.gz"
    manifest_path = root / f"{prefix}_manifest.json"

    _dump_database(database_path)
    artifacts = [database_path]
    if include_media:
        _archive_media(media_path)
        artifacts.append(media_path)

    manifest = {
        "version": 1,
        "created_at": datetime.now(datetime_timezone.utc).isoformat(),
        "database_vendor": connection.vendor,
        "include_media": include_media,
        "artifacts": [
            {
                "name": path.name,
                "size": path.stat().st_size,
                "sha256": _sha256(path),
            }
            for path in artifacts
        ],
    }
    with manifest_path.open("w", encoding="utf-8") as output:
        json.dump(manifest, output, ensure_ascii=False, indent=2)
        output.write("\n")

    removed = _cleanup_old_backups(root)
    return {
        "manifest": manifest_path,
        "artifacts": artifacts,
        "removed": removed,
    }


def list_backup_sets(limit=20):
    rows = []
    for path in sorted(get_backup_root().glob("flexs_*_manifest.json"), reverse=True)[:limit]:
        try:
            with path.open("r", encoding="utf-8") as source:
                manifest = json.load(source)
        except (OSError, ValueError):
            continue
        rows.append({"manifest_name": path.name, **manifest})
    return rows
