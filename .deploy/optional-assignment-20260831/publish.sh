#!/usr/bin/env bash
set -euo pipefail
umask 027
exec 9>/var/lock/webflexs-deploy.lock
flock -n 9
APP_DIR=/var/www/webflexs
STAGE_DIR=/var/tmp/webflexs-optional-assignment-20260831
BACKUP_DIR=/var/backups/webflexs/optional-assignment-20260831
PYTHON="$APP_DIR/venv/bin/python"
SETTINGS=flexs_project.settings.production
MIGRATION=catalog/migrations/0034_brandcatalogbatch_optional_observation.py
STATIC_JS="$APP_DIR/staticfiles/core/js/brand_product_workspace.js"
test "$(readlink -f "$APP_DIR")" = /var/www/webflexs
test "$(readlink -f "$STAGE_DIR")" = /var/tmp/webflexs-optional-assignment-20260831
test ! -e "$BACKUP_DIR"
test ! -e "$APP_DIR/$MIGRATION"
test -f "$STATIC_JS"
cd "$STAGE_DIR/package"
sha256sum -c "$STAGE_DIR/package.sha256"
cd "$APP_DIR"
sha256sum -c "$STAGE_DIR/originals.sha256"
mapfile -t ORIGINALS < <(awk '{print $2}' "$STAGE_DIR/originals.sha256")
mapfile -t FILES < <(awk '{print $2}' "$STAGE_DIR/package.sha256")
for file in "${FILES[@]}"; do test ! -e "$APP_DIR/$file.optional-assignment.tmp"; done
install -d -m 700 "$BACKUP_DIR"
for file in "${ORIGINALS[@]}"; do
    install -d -m 700 "$BACKUP_DIR/files/$(dirname "$file")"
    cp -p "$APP_DIR/$file" "$BACKUP_DIR/files/$file"
done
cp -p "$STATIC_JS" "$BACKUP_DIR/served-brand_product_workspace.js"
"$PYTHON" "$STAGE_DIR/host_checks.py" backup
"$PYTHON" "$STAGE_DIR/host_checks.py" fingerprint > "$BACKUP_DIR/catalog-before.json"
sha256sum admin_panel/templates/admin_panel/base.html admin_panel/templates/admin_panel/brands/brand_list.html core/static/core/css/admin.css core/static/core/css/brand_product_workspace.css > "$BACKUP_DIR/unchanged-layout.sha256"

rollback() {
    trap - ERR
    for file in "${ORIGINALS[@]}"; do
        cp -p "$BACKUP_DIR/files/$file" "$APP_DIR/$file"
    done
    cp -p "$BACKUP_DIR/served-brand_product_workspace.js" "$STATIC_JS"
    systemctl restart gunicorn
    printf '%s\n' 'PUBLICATION_FAILED_ORIGINAL_FILES_RESTORED_ADDITIVE_MIGRATION_PRESERVED'
    exit 1
}
trap rollback ERR
for file in "${FILES[@]}"; do
    install -o root -g www-data -m 644 "$STAGE_DIR/package/$file" "$APP_DIR/$file.optional-assignment.tmp"
    mv -f "$APP_DIR/$file.optional-assignment.tmp" "$APP_DIR/$file"
done
"$PYTHON" "$STAGE_DIR/host_checks.py" migration_sql
"$PYTHON" manage.py migrate catalog 0034 --noinput --settings="$SETTINGS"
"$PYTHON" manage.py check --settings="$SETTINGS"
"$PYTHON" "$STAGE_DIR/host_checks.py" smoke
"$PYTHON" manage.py collectstatic --noinput --verbosity 0 --settings="$SETTINGS"
sha256sum -c "$BACKUP_DIR/unchanged-layout.sha256"
cmp -s "$APP_DIR/core/static/core/js/brand_product_workspace.js" "$STATIC_JS"
systemctl restart gunicorn
systemctl is-active --quiet gunicorn
systemctl is-active --quiet nginx
sha256sum -c "$STAGE_DIR/package.sha256"
"$PYTHON" "$STAGE_DIR/host_checks.py" fingerprint > "$BACKUP_DIR/catalog-after.json"
if cmp -s "$BACKUP_DIR/catalog-before.json" "$BACKUP_DIR/catalog-after.json"; then
    printf '%s\n' 'CATALOG_AND_HISTORY_UNCHANGED'
else
    printf '%s\n' 'CATALOG_CHANGED_DURING_PUBLICATION_REVIEW_REQUIRED'
fi
trap - ERR
printf '%s\n' 'PUBLICATION_COMPLETED'
