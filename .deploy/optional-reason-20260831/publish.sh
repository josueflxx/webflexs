#!/usr/bin/env bash
set -euo pipefail
umask 027
exec 9>/var/lock/webflexs-deploy.lock
flock -n 9
APP_DIR=/var/www/webflexs
STAGE_DIR=/var/tmp/webflexs-optional-reason-20260831
BACKUP_DIR=/var/backups/webflexs/optional-reason-20260831
TARGET="$APP_DIR/admin_panel/forms/brand_structure_forms.py"
PYTHON="$APP_DIR/venv/bin/python"
test "$(readlink -f "$APP_DIR")" = /var/www/webflexs
test "$(readlink -f "$STAGE_DIR")" = /var/tmp/webflexs-optional-reason-20260831
test ! -e "$BACKUP_DIR"
test ! -e "$TARGET.optional-reason.tmp"
printf '%s  %s\n' dedbc9826122520b7a514ccb614a4b64f8a8b9388feb67468db9df40e9b3696e "$TARGET" | sha256sum -c -
printf '%s  %s\n' b64a1fc34ad8be1e2f9f0ecf0b60676911cf076e0a210e3699fa9cf7b7a0e747 "$STAGE_DIR/brand_structure_forms.py" | sha256sum -c -
install -d -m 700 "$BACKUP_DIR"
cp -p "$TARGET" "$BACKUP_DIR/brand_structure_forms.py"
cd "$APP_DIR"
sha256sum admin_panel/templates/admin_panel/base.html core/static/core/css/admin.css admin_panel/templates/admin_panel/brands/structure_manage.html admin_panel/templates/admin_panel/brands/structure_batch.html core/static/core/css/brand_structure.css core/static/core/js/brand_structure.js > "$BACKUP_DIR/unchanged-layout.sha256"

rollback() {
    trap - ERR
    install -o root -g www-data -m 644 "$BACKUP_DIR/brand_structure_forms.py" "$TARGET.optional-reason.tmp"
    mv -f "$TARGET.optional-reason.tmp" "$TARGET"
    systemctl restart gunicorn
    printf '%s\n' 'PUBLICATION_FAILED_ORIGINAL_FORM_RESTORED'
    exit 1
}
trap rollback ERR
install -o root -g www-data -m 644 "$STAGE_DIR/brand_structure_forms.py" "$TARGET.optional-reason.tmp"
mv -f "$TARGET.optional-reason.tmp" "$TARGET"
"$PYTHON" manage.py check --settings=flexs_project.settings.production
"$PYTHON" "$STAGE_DIR/verify.py"
sha256sum -c "$BACKUP_DIR/unchanged-layout.sha256"
systemctl restart gunicorn
systemctl is-active --quiet gunicorn
systemctl is-active --quiet nginx
cmp -s "$TARGET" "$STAGE_DIR/brand_structure_forms.py"
trap - ERR
printf '%s\n' 'PUBLICATION_COMPLETED_ONE_FORM_ONLY'
