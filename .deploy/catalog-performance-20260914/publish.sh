#!/usr/bin/env bash
set -euo pipefail
umask 027
exec 9>/var/lock/webflexs-deploy.lock
flock -n 9
APP=/var/www/webflexs
STAGE=/var/tmp/webflexs-catalog-performance-20260914
BACKUP=/var/backups/webflexs/catalog-performance-20260914
PYTHON="$APP/venv/bin/python"
SERVICE=webflexs-catalog-excel
FILES=(catalog/views.py catalog/templates/catalog/catalog_v3.html catalog/templates/catalog/excel_pending.html core/services/catalog_excel_jobs.py core/catalog_excel_tasks.py core/static/core/js/catalog_excel_download.js)
test "$(readlink -f "$APP")" = /var/www/webflexs
test "$(readlink -f "$STAGE")" = /var/tmp/webflexs-catalog-performance-20260914
test ! -e "$BACKUP"
test ! -e "/etc/systemd/system/$SERVICE.service"
cd "$APP"
printf '%s\n' '5a2fdad78a85fee8a55352f3e709cb0374d91d749907037f8b1cedda2b7f6b54  catalog/views.py' '84dd617a30870b9e09d24c1fa3804e55c1fd38262a584aebe87fd306755bb1c6  catalog/templates/catalog/catalog_v3.html' | sha256sum -c -
for file in "${FILES[@]:2}"; do test ! -e "$APP/$file"; done
"$PYTHON" "$STAGE/verify.py" preflight
install -d -m 700 "$BACKUP/files/catalog/templates/catalog"
cp -p catalog/views.py "$BACKUP/files/catalog/views.py"
cp -p catalog/templates/catalog/catalog_v3.html "$BACKUP/files/catalog/templates/catalog/catalog_v3.html"
rollback() {
    trap - ERR
    systemctl disable --now "$SERVICE" || true
    cp -p "$BACKUP/files/catalog/views.py" "$APP/catalog/views.py"
    cp -p "$BACKUP/files/catalog/templates/catalog/catalog_v3.html" "$APP/catalog/templates/catalog/catalog_v3.html"
    systemctl restart gunicorn
    printf '%s\n' CATALOG_DEPLOY_FAILED_ORIGINAL_ROUTES_RESTORED
    exit 1
}
trap rollback ERR
for file in "${FILES[@]}"; do
    install -o root -g www-data -m 644 "$STAGE/package/$file" "$APP/$file.performance.tmp"
    mv -f "$APP/$file.performance.tmp" "$APP/$file"
done
install -d -o root -g www-data -m 750 "$APP/runtime"
install -d -o www-data -g www-data -m 750 "$APP/runtime/catalog_exports"
install -o root -g root -m 644 "$STAGE/webflexs-catalog-excel.service" "/etc/systemd/system/$SERVICE.service"
"$PYTHON" -m py_compile catalog/views.py core/services/catalog_excel_jobs.py core/catalog_excel_tasks.py
"$PYTHON" manage.py check --settings=flexs_project.settings.production
"$PYTHON" manage.py collectstatic --noinput --verbosity 0 --settings=flexs_project.settings.production
systemctl daemon-reload
systemctl enable --now "$SERVICE"
systemctl restart gunicorn
ready=false
for attempt in {1..15}; do
    if curl --connect-timeout 2 --max-time 4 --fail --silent -o /dev/null https://flexsrepuestos.shop/accounts/login/; then
        ready=true
        break
    fi
    sleep 1
done
test "$ready" = true
systemctl is-active --quiet "$SERVICE"
systemctl is-active --quiet gunicorn
curl --fail --silent --max-time 10 -o /dev/null 'https://flexsrepuestos.shop/catalogo/?view=grid'
curl --fail --silent --max-time 10 -o /dev/null 'https://flexsrepuestos.shop/static/core/js/catalog_excel_download.js?v=20260914-1'
"$PYTHON" "$STAGE/verify.py" queue
trap - ERR
printf '%s\n' CATALOG_DEPLOY_PUBLISHED_EXPORTS_QUEUED
