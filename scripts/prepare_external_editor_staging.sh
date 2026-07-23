#!/usr/bin/env bash
set -euo pipefail

# Creates a verified PostgreSQL backup and restores it into an explicitly named
# staging database. It refuses to touch the source database or a target that does
# not end in "_staging".

: "${DB_NAME:?Define DB_NAME}"
: "${DB_USER:?Define DB_USER}"
: "${DB_PASSWORD:?Define DB_PASSWORD}"
: "${STAGING_DB_NAME:?Define STAGING_DB_NAME (must end in _staging)}"

DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"
BACKUP_ROOT="${BACKUP_ROOT:-/var/backups/webflexs/external-editor}"

if [[ "${STAGING_DB_NAME}" == "${DB_NAME}" ]]; then
    echo "Refusing to overwrite the source database." >&2
    exit 1
fi

if [[ ! "${STAGING_DB_NAME}" =~ _staging$ ]]; then
    echo "STAGING_DB_NAME must end in _staging." >&2
    exit 1
fi

if [[ "${ALLOW_STAGING_RECREATE:-}" != "YES" ]]; then
    echo "Set ALLOW_STAGING_RECREATE=YES to confirm recreation of ${STAGING_DB_NAME}." >&2
    exit 1
fi

install -d -m 0700 "${BACKUP_ROOT}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_path="${BACKUP_ROOT}/${DB_NAME}_${timestamp}.dump"
manifest_path="${backup_path}.manifest"

export PGPASSWORD="${DB_PASSWORD}"
pg_dump \
    --host "${DB_HOST}" \
    --port "${DB_PORT}" \
    --username "${DB_USER}" \
    --format custom \
    --no-owner \
    --no-acl \
    --file "${backup_path}.tmp" \
    "${DB_NAME}"

pg_restore --list "${backup_path}.tmp" > "${manifest_path}.tmp"
test -s "${manifest_path}.tmp"
mv "${backup_path}.tmp" "${backup_path}"
mv "${manifest_path}.tmp" "${manifest_path}"

dropdb --if-exists --host "${DB_HOST}" --port "${DB_PORT}" --username "${DB_USER}" "${STAGING_DB_NAME}"
createdb --host "${DB_HOST}" --port "${DB_PORT}" --username "${DB_USER}" "${STAGING_DB_NAME}"
pg_restore \
    --host "${DB_HOST}" \
    --port "${DB_PORT}" \
    --username "${DB_USER}" \
    --dbname "${STAGING_DB_NAME}" \
    --no-owner \
    --no-acl \
    "${backup_path}"

row_count="$(psql --host "${DB_HOST}" --port "${DB_PORT}" --username "${DB_USER}" --dbname "${STAGING_DB_NAME}" --tuples-only --no-align --command 'SELECT COUNT(*) FROM catalog_product;')"
echo "Backup verified: ${backup_path}"
echo "Staging database ready: ${STAGING_DB_NAME} (${row_count} products)"
