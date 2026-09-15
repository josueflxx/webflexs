#!/usr/bin/env bash
set -euo pipefail
umask 027
exec 9>/var/lock/webflexs-deploy.lock
flock -n 9
APP=/var/www/webflexs
STAGE=/var/tmp/webflexs-home-3d-20260914
BACKUP=/var/backups/webflexs/home-3d-20260914
PYTHON="$APP/venv/bin/python"
test "$(readlink -f "$APP")" = /var/www/webflexs
test ! -e "$BACKUP"
mapfile -t FILES < "$STAGE/files.txt"
cd "$STAGE/package"
sha256sum -c "$STAGE/package.sha256"
cd "$APP"
sha256sum -c "$STAGE/originals.sha256"
for file in "${FILES[@]:1}"; do test ! -e "$APP/$file"; done
install -d -m 700 "$BACKUP/files/core/templates/core"
cp -p core/templates/core/home.html "$BACKUP/files/core/templates/core/home.html"
cp "$STAGE/files.txt" "$BACKUP/files.txt"

rollback() {
    trap - ERR
    cp -p "$BACKUP/files/core/templates/core/home.html" "$APP/core/templates/core/home.html"
    systemctl restart gunicorn
    printf '%s\n' HOME_DEPLOY_FAILED_PREVIOUS_TEMPLATE_RESTORED
    exit 1
}
trap rollback ERR
for file in "${FILES[@]}"; do
    install -d -m 755 "$(dirname "$APP/$file")"
    install -o root -g www-data -m 644 "$STAGE/package/$file" "$APP/$file.home3d.tmp"
    mv -f "$APP/$file.home3d.tmp" "$APP/$file"
done
"$PYTHON" manage.py check --settings=flexs_project.settings.production
(umask 022; "$PYTHON" manage.py collectstatic --noinput --settings=flexs_project.settings.production --verbosity 0)
chmod 755 "$APP/staticfiles/core/models" "$APP/staticfiles/core/vendor" "$APP/staticfiles/core/vendor/model-viewer"
systemctl restart gunicorn
systemctl is-active --quiet gunicorn
systemctl is-active --quiet nginx
ready=false
for attempt in {1..10}; do
    if curl --connect-timeout 3 --max-time 8 --fail --silent -o "$BACKUP/public-home.html" https://flexsrepuestos.shop/ && grep -q 'id="homeClamp"' "$BACKUP/public-home.html"; then
        ready=true
        break
    fi
    sleep 1
done
test "$ready" = true
for asset in core/js/home_clamp.js core/css/home_clamp.css core/models/abrazadera-curva.glb core/models/clamp-studio.hdr core/vendor/model-viewer/model-viewer-4.3.1.min.js; do
    curl --connect-timeout 3 --max-time 15 --fail --silent -o /dev/null "https://flexsrepuestos.shop/static/$asset"
done
sha256sum -c "$STAGE/package.sha256"
trap - ERR
printf '%s\n' HOME_3D_DEPLOY_COMPLETED
