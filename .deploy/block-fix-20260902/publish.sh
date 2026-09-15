#!/usr/bin/env bash
set -euo pipefail
umask 027

exec 9>/var/lock/webflexs-deploy.lock
flock -n 9

APP_DIR=/var/www/webflexs
STAGE_DIR=/var/tmp/webflexs-block-fix-20260902
BACKUP_DIR=/var/backups/webflexs/block-fix-20260902
PYTHON="$APP_DIR/venv/bin/python"
SETTINGS=flexs_project.settings.production
FILES=(
    admin_panel/templates/admin_panel/categories/manage_products.html
    admin_panel/views/products.py
    catalog/services/category_block_conversion.py
)

test "$(readlink -f "$APP_DIR")" = /var/www/webflexs
test "$(readlink -f "$STAGE_DIR")" = /var/tmp/webflexs-block-fix-20260902
test ! -e "$BACKUP_DIR"

cd "$STAGE_DIR/package"
sha256sum -c "$STAGE_DIR/package.sha256"
cd "$APP_DIR"
sha256sum -c "$STAGE_DIR/originals.sha256"

for file in "${FILES[@]}"; do
    test ! -e "$APP_DIR/$file.block-fix.tmp"
done

install -d -m 700 "$BACKUP_DIR/files"
for file in "${FILES[@]}"; do
    install -d -m 700 "$BACKUP_DIR/files/$(dirname "$file")"
    cp -p "$APP_DIR/$file" "$BACKUP_DIR/files/$file"
done

"$PYTHON" "$STAGE_DIR/verify.py" fingerprint > "$BACKUP_DIR/category-before.json"

rollback() {
    trap - ERR
    for file in "${FILES[@]}"; do
        install -o root -g www-data -m 644 "$BACKUP_DIR/files/$file" "$APP_DIR/$file.block-fix.tmp"
        mv -f "$APP_DIR/$file.block-fix.tmp" "$APP_DIR/$file"
    done
    systemctl restart gunicorn
    printf '%s\n' 'PUBLICATION_FAILED_ORIGINAL_FILES_RESTORED'
    exit 1
}
trap rollback ERR

for file in "${FILES[@]}"; do
    install -o root -g www-data -m 644 "$STAGE_DIR/package/$file" "$APP_DIR/$file.block-fix.tmp"
    mv -f "$APP_DIR/$file.block-fix.tmp" "$APP_DIR/$file"
done

"$PYTHON" -m py_compile \
    "$APP_DIR/admin_panel/views/products.py" \
    "$APP_DIR/catalog/services/category_block_conversion.py"
"$PYTHON" manage.py check --settings="$SETTINGS"
"$PYTHON" "$STAGE_DIR/verify.py" smoke

systemctl restart gunicorn
systemctl is-active --quiet gunicorn
systemctl is-active --quiet nginx

"$PYTHON" "$STAGE_DIR/verify.py" smoke
bash scripts/smoke_check.sh https://flexsrepuestos.shop
sha256sum -c "$STAGE_DIR/package.sha256"
"$PYTHON" "$STAGE_DIR/verify.py" fingerprint > "$BACKUP_DIR/category-after.json"

if cmp -s "$BACKUP_DIR/category-before.json" "$BACKUP_DIR/category-after.json"; then
    printf '%s\n' 'CATEGORY_788_DATA_UNCHANGED'
else
    printf '%s\n' 'CATEGORY_788_CHANGED_DURING_PUBLICATION_REVIEW_REQUIRED'
fi

trap - ERR
printf '%s\n' 'PUBLICATION_COMPLETED_BLOCK_FIX'
