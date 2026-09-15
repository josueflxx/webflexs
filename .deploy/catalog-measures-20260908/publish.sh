#!/usr/bin/env bash
set -euo pipefail
umask 027

exec 9>/var/lock/webflexs-deploy.lock
flock -n 9

APP_DIR=/var/www/webflexs
STAGE_DIR=/var/tmp/webflexs-catalog-measures-20260908-r2
BACKUP_DIR=/var/backups/webflexs/catalog-measures-20260908-r2
PYTHON="$APP_DIR/venv/bin/python"
SETTINGS=flexs_project.settings.production
FILES=(
    core/services/catalog_excel_exporter.py
    core/services/catalog_excel_status.py
    core/static/core/img/todas_las_medidas.png
)
CODE_FILES=(
    core/services/catalog_excel_exporter.py
    core/services/catalog_excel_status.py
)

test "$(readlink -f "$APP_DIR")" = /var/www/webflexs
test "$(readlink -f "$STAGE_DIR")" = /var/tmp/webflexs-catalog-measures-20260908-r2
test ! -e "$BACKUP_DIR"

cd "$STAGE_DIR/package"
sha256sum -c "$STAGE_DIR/package.sha256"
cd "$APP_DIR"
sha256sum -c "$STAGE_DIR/originals.sha256"
test ! -e "$APP_DIR/core/static/core/img/todas_las_medidas.png"

for file in "${FILES[@]}"; do
    test ! -e "$APP_DIR/$file.catalog-measures.tmp"
done

install -d -m 700 "$BACKUP_DIR/files/core/services"
for file in "${CODE_FILES[@]}"; do
    cp -p "$APP_DIR/$file" "$BACKUP_DIR/files/$file"
done

rollback() {
    trap - ERR
    for file in "${CODE_FILES[@]}"; do
        install -o root -g www-data -m 644 "$BACKUP_DIR/files/$file" "$APP_DIR/$file.catalog-measures.tmp"
        mv -f "$APP_DIR/$file.catalog-measures.tmp" "$APP_DIR/$file"
    done
    rm -f "$APP_DIR/core/static/core/img/todas_las_medidas.png"
    systemctl restart gunicorn
    printf '%s\n' 'PUBLICATION_FAILED_ORIGINAL_FILES_RESTORED'
    exit 1
}
trap rollback ERR

for file in "${FILES[@]}"; do
    install -d -o root -g www-data -m 755 "$APP_DIR/$(dirname "$file")"
    install -o root -g www-data -m 644 "$STAGE_DIR/package/$file" "$APP_DIR/$file.catalog-measures.tmp"
    mv -f "$APP_DIR/$file.catalog-measures.tmp" "$APP_DIR/$file"
done

"$PYTHON" -m py_compile \
    "$APP_DIR/core/services/catalog_excel_exporter.py" \
    "$APP_DIR/core/services/catalog_excel_status.py"
"$PYTHON" manage.py check --settings="$SETTINGS"
"$PYTHON" "$STAGE_DIR/verify.py"

systemctl restart gunicorn
systemctl is-active --quiet gunicorn
systemctl is-active --quiet nginx

"$PYTHON" "$STAGE_DIR/verify.py"
bash scripts/smoke_check.sh https://flexsrepuestos.shop

cd "$APP_DIR"
sha256sum -c "$STAGE_DIR/package.sha256"

trap - ERR
printf '%s\n' 'PUBLICATION_COMPLETED_CATALOG_MEASURES'
