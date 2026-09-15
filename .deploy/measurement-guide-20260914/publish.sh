#!/usr/bin/env bash
set -euo pipefail
umask 022
exec 9>/var/lock/webflexs-deploy.lock
flock -n 9
APP=/var/www/webflexs
STAGE=/var/tmp/webflexs-measurement-guide-20260914
BACKUP=/var/backups/webflexs/measurement-guide-20260914
PYTHON="$APP/venv/bin/python"
test "$(readlink -f "$APP")" = /var/www/webflexs
test ! -e "$BACKUP"
mapfile -t FILES < "$STAGE/files.txt"
mapfile -t EXISTING < "$STAGE/existing.txt"
mapfile -t NEW < "$STAGE/new.txt"
cd "$STAGE/package"
sha256sum -c "$STAGE/package.sha256"
cd "$APP"
sha256sum -c "$STAGE/originals.sha256"
for file in "${NEW[@]}"; do test ! -e "$APP/$file"; done
install -d -m 700 "$BACKUP"
for file in "${EXISTING[@]}"; do
    install -d -m 700 "$(dirname "$BACKUP/files/$file")"
    cp -p "$APP/$file" "$BACKUP/files/$file"
done
cp "$STAGE/files.txt" "$BACKUP/files.txt"
rollback() {
    trap - ERR
    for file in "${EXISTING[@]}"; do cp -p "$BACKUP/files/$file" "$APP/$file"; done
    # Ensure the previous public JS is restored even if its old mtime is older.
    for file in "${EXISTING[@]}"; do touch "$APP/$file"; done
    "$PYTHON" manage.py collectstatic --noinput --settings=flexs_project.settings.production --verbosity 0
    systemctl restart gunicorn
    printf '%s\n' MEASUREMENT_GUIDE_DEPLOY_FAILED_PREVIOUS_FILES_RESTORED
    exit 1
}
trap rollback ERR
for file in "${FILES[@]}"; do
    install -d -m 755 "$(dirname "$APP/$file")"
    install -o root -g www-data -m 644 "$STAGE/package/$file" "$APP/$file.measurement-guide.tmp"
    mv -f "$APP/$file.measurement-guide.tmp" "$APP/$file"
done
"$PYTHON" manage.py check --settings=flexs_project.settings.production
"$PYTHON" manage.py collectstatic --noinput --settings=flexs_project.settings.production --verbosity 0
for asset in core/css/measurement_guide.css core/js/measurement_guide.js; do
    curl --connect-timeout 3 --max-time 15 --fail --silent -o /dev/null "https://flexsrepuestos.shop/static/$asset"
done
systemctl restart gunicorn
systemctl is-active --quiet gunicorn
systemctl is-active --quiet nginx
ready=false
for attempt in {1..10}; do
    if curl --connect-timeout 3 --max-time 8 --fail --silent -o "$BACKUP/public-guide.html" https://flexsrepuestos.shop/catalogo/como-medir/ && grep -q 'measurementGuide' "$BACKUP/public-guide.html"; then ready=true; break; fi
    sleep 1
done
test "$ready" = true
sha256sum -c "$STAGE/package.sha256"
trap - ERR
printf '%s\n' MEASUREMENT_GUIDE_DEPLOY_COMPLETED
