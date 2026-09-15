#!/usr/bin/env bash
set -euo pipefail
umask 027
exec 9>/var/lock/webflexs-deploy.lock
flock -n 9
ROOT=/var/www/webflexs
STAGE=/var/tmp/webflexs-brand-structure-20260831
BACKUP=/var/backups/webflexs/brand-structure-20260831
PY="$ROOT/venv/bin/python"
SETTINGS=flexs_project.settings.production
test "$(readlink -f "$ROOT")" = /var/www/webflexs
test "$(readlink -f "$STAGE")" = /var/tmp/webflexs-brand-structure-20260831
test ! -e "$BACKUP"
cd "$STAGE/package"
sha256sum -c "$STAGE/package.sha256"
cd "$ROOT"
sha256sum -c "$STAGE/originals.sha256"

ORIGINALS=(catalog/models.py admin_panel/urls.py admin_panel/views/__init__.py admin_panel/templates/admin_panel/brands/brand_list.html)
NEW_FILES=(catalog/migrations/0033_brandstructurebatch.py catalog/services/brand_structure.py admin_panel/forms/brand_structure_forms.py admin_panel/views/brand_structure.py admin_panel/templates/admin_panel/brands/structure_manage.html admin_panel/templates/admin_panel/brands/structure_batch.html core/static/core/css/brand_structure.css core/static/core/js/brand_structure.js)
for file in "${NEW_FILES[@]}"; do test ! -e "$ROOT/$file"; done
install -d -m 700 "$BACKUP"
for file in "${ORIGINALS[@]}"; do
    install -d -m 700 "$BACKUP/files/$(dirname "$file")"
    cp -p "$ROOT/$file" "$BACKUP/files/$file"
done
"$PY" "$STAGE/host_checks.py" backup "$BACKUP"
"$PY" "$STAGE/host_checks.py" fingerprint > "$BACKUP/catalog-before.json"
sha256sum admin_panel/templates/admin_panel/base.html core/static/core/css/admin.css > "$BACKUP/unchanged-layout.sha256"

rollback() {
    trap - ERR
    printf '%s\n' 'Publication failed; restoring the four previous application files. The additive database backup/migration is preserved.'
    for file in "${ORIGINALS[@]}"; do cp -p "$BACKUP/files/$file" "$ROOT/$file"; done
    systemctl restart gunicorn
    exit 1
}
trap rollback ERR

# Keep the old Marcas template until the new database and views are ready.
for file in "${NEW_FILES[@]}" catalog/models.py admin_panel/views/__init__.py admin_panel/urls.py; do
    install -o root -g www-data -m 644 "$STAGE/package/$file" "$ROOT/$file"
done
"$PY" manage.py migrate catalog 0033 --noinput --settings="$SETTINGS"
"$PY" manage.py check --settings="$SETTINGS"
"$PY" manage.py collectstatic --noinput --verbosity 0 --settings="$SETTINGS"
install -o root -g www-data -m 644 "$STAGE/package/admin_panel/templates/admin_panel/brands/brand_list.html" "$ROOT/admin_panel/templates/admin_panel/brands/brand_list.html"
"$PY" "$STAGE/host_checks.py" smoke
sha256sum -c "$BACKUP/unchanged-layout.sha256"
systemctl restart gunicorn
systemctl is-active --quiet gunicorn
sha256sum -c "$STAGE/package.sha256"
"$PY" "$STAGE/host_checks.py" fingerprint > "$BACKUP/catalog-after.json"
if cmp -s "$BACKUP/catalog-before.json" "$BACKUP/catalog-after.json"; then
    printf '%s\n' 'CATALOG_DATA_UNCHANGED'
else
    printf '%s\n' 'CATALOG_CHANGED_DURING_PUBLICATION_REVIEW_REQUIRED'
fi
trap - ERR
printf '%s\n' 'PUBLICATION_COMPLETED'
