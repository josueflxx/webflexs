#!/usr/bin/env bash
set -euo pipefail
exec 9>/var/lock/webflexs-deploy.lock
flock -n 9
APP=/var/www/webflexs
STAGE=/var/tmp/webflexs-home-3d-20260914
BACKUP=/var/backups/webflexs/home-3d-20260914
cd "$APP"
sha256sum -c "$STAGE/originals.sha256"
# These three public-asset folders were newly created by collectstatic.
chmod 755 "$APP/staticfiles/core/models" "$APP/staticfiles/core/vendor" "$APP/staticfiles/core/vendor/model-viewer"
for asset in core/js/home_clamp.js core/css/home_clamp.css core/models/abrazadera-curva.glb core/models/clamp-studio.hdr core/vendor/model-viewer/model-viewer-4.3.1.min.js; do
    curl --connect-timeout 3 --max-time 15 --fail --silent -o /dev/null "https://flexsrepuestos.shop/static/$asset"
done
rollback() {
    trap - ERR
    cp -p "$BACKUP/files/core/templates/core/home.html" "$APP/core/templates/core/home.html"
    systemctl restart gunicorn
    exit 1
}
trap rollback ERR
install -o root -g www-data -m 644 "$STAGE/package/core/templates/core/home.html" "$APP/core/templates/core/home.html.home3d.tmp"
mv -f "$APP/core/templates/core/home.html.home3d.tmp" "$APP/core/templates/core/home.html"
sha256sum -c "$STAGE/package.sha256"
systemctl restart gunicorn
systemctl is-active --quiet gunicorn
systemctl is-active --quiet nginx
ready=false
for attempt in {1..10}; do
    if curl --connect-timeout 3 --max-time 8 --fail --silent -o "$BACKUP/public-home.html" https://flexsrepuestos.shop/ && grep -q 'id="homeClamp"' "$BACKUP/public-home.html"; then ready=true; break; fi
    sleep 1
done
test "$ready" = true
trap - ERR
printf '%s\n' HOME_3D_DEPLOY_COMPLETED
