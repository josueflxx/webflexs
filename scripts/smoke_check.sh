#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${1:-https://flexsrepuestos.shop}"

check_url() {
  local path="$1"
  local url="${BASE_URL%/}${path}"
  local code
  code="$(curl -s -o /dev/null -w "%{http_code}" "$url")"
  echo "$path -> HTTP $code"
  if [[ "$code" -lt 200 || "$code" -ge 400 ]]; then
    echo "ERROR: fallo smoke test en $url"
    exit 1
  fi
}

check_url "/"
check_url "/catalogo/"
check_url "/accounts/login/"
check_url "/admin-panel/login/"

echo "Smoke check OK para $BASE_URL"
