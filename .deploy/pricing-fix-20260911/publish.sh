#!/usr/bin/env bash
set -euo pipefail
umask 027
exec 9>/var/lock/webflexs-deploy.lock
flock -n 9

APP=/var/www/webflexs
STAGE=/var/tmp/webflexs-pricing-fix-20260911-r2
BACKUP=/var/backups/webflexs/pricing-fix-20260911-r2
PYTHON="$APP/venv/bin/python"
FILES=(core/services/pricing.py core/services/catalog_excel_exporter.py core/services/catalog_excel_status.py)

test "$(readlink -f "$APP")" = /var/www/webflexs
test "$(readlink -f "$STAGE")" = /var/tmp/webflexs-pricing-fix-20260911-r2
test ! -e "$BACKUP"
cd "$STAGE/package"
sha256sum -c "$STAGE/package.sha256"
cd "$APP"
sha256sum -c "$STAGE/originals.sha256"
for file in "${FILES[@]}"; do test ! -e "$APP/$file.pricing-fix.tmp"; done

install -d -m 700 "$BACKUP/files/core/services"
for file in "${FILES[@]}"; do cp -p "$APP/$file" "$BACKUP/files/$file"; done
"$PYTHON" "$STAGE/verify.py" inspect > "$BACKUP/before.jsonl"

rollback() {
    trap - ERR
    for file in "${FILES[@]}"; do
        install -o root -g www-data -m 644 "$BACKUP/files/$file" "$APP/$file.pricing-fix.tmp"
        mv -f "$APP/$file.pricing-fix.tmp" "$APP/$file"
    done
    systemctl restart gunicorn
    printf '%s\n' PRICING_DEPLOY_FAILED_FILES_RESTORED
    exit 1
}
trap rollback ERR

for file in "${FILES[@]}"; do
    install -o root -g www-data -m 644 "$STAGE/package/$file" "$APP/$file.pricing-fix.tmp"
    mv -f "$APP/$file.pricing-fix.tmp" "$APP/$file"
done
"$PYTHON" -m py_compile "${FILES[@]}"
"$PYTHON" manage.py check --settings=flexs_project.settings.production
"$PYTHON" "$STAGE/verify.py" verify
systemctl restart gunicorn
systemctl is-active --quiet gunicorn
systemctl is-active --quiet nginx
ready=false
for attempt in {1..10}; do
    if curl --connect-timeout 2 --max-time 3 --fail --silent -o /dev/null https://flexsrepuestos.shop/accounts/login/; then
        ready=true
        break
    fi
    sleep 1
done
test "$ready" = true
bash scripts/smoke_check.sh https://flexsrepuestos.shop
sha256sum -c "$STAGE/package.sha256"
"$PYTHON" "$STAGE/verify.py" inspect > "$BACKUP/after.jsonl"
if cmp -s "$BACKUP/before.jsonl" "$BACKUP/after.jsonl"; then
    printf '%s\n' PRODUCT_PRICES_AND_CUSTOMER_RULES_UNCHANGED
else
    printf '%s\n' CONCURRENT_DATA_CHANGE_DETECTED_REVIEW_FINGERPRINTS
fi
trap - ERR
printf '%s\n' PRICING_DEPLOY_COMPLETED
