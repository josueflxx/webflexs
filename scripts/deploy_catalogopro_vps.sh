#!/usr/bin/env bash
set -euo pipefail

# Deploy the React mass editor as a first-party WEBFLEXS frontend. Product writes
# go through /api/v1/editor/ in Django. The legacy service remains available
# during the canary unless its retirement is explicitly requested.

WEBFLEXS_ROOT="${WEBFLEXS_ROOT:-/var/www/webflexs}"
BUILD_DIR="${WEBFLEXS_ROOT}/catalogopro_build/frontend"
TARGET_DIR="/var/www/catalogopro/editor-masivo"
RETIRE_LEGACY_CATALOGOPRO="${RETIRE_LEGACY_CATALOGOPRO:-0}"

if [[ ! -f "${BUILD_DIR}/index.html" ]]; then
    echo "Missing editor build at ${BUILD_DIR}." >&2
    exit 1
fi

install -d -m 0755 "${TARGET_DIR}"
if [[ "${TARGET_DIR}" != "/var/www/catalogopro/editor-masivo" ]]; then
    echo "Unexpected deployment target: ${TARGET_DIR}" >&2
    exit 1
fi
find "${TARGET_DIR}" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
cp -a "${BUILD_DIR}/." "${TARGET_DIR}/"

if [[ "${RETIRE_LEGACY_CATALOGOPRO}" == "1" ]]; then
    # Run only after the official editor has completed its stabilization period.
    systemctl disable --now catalogopro 2>/dev/null || true
    rm -f /var/www/catalogopro/api/appsettings.Production.json
fi

NGINX_FILE=""
for candidate in /etc/nginx/sites-enabled/*; do
    if [[ -f "${candidate}" ]]; then
        NGINX_FILE="$(readlink -f "${candidate}")"
        break
    fi
done

if [[ -z "${NGINX_FILE}" || ! -f "${NGINX_FILE}" ]]; then
    echo "No active Nginx site was found." >&2
    exit 1
fi

NGINX_FILE="${NGINX_FILE}" RETIRE_LEGACY_CATALOGOPRO="${RETIRE_LEGACY_CATALOGOPRO}" python3 <<'PY'
import os
import re
from pathlib import Path

path = Path(os.environ["NGINX_FILE"])
content = path.read_text(encoding="utf-8")


def remove_location(source, location_expression):
    pattern = re.compile(r"(?m)^\s*location\s+" + location_expression + r"\s*\{")
    while True:
        match = pattern.search(source)
        if not match:
            return source
        depth = 0
        end = None
        for index in range(match.end() - 1, len(source)):
            if source[index] == "{":
                depth += 1
            elif source[index] == "}":
                depth -= 1
                if depth == 0:
                    end = index + 1
                    break
        if end is None:
            raise SystemExit(f"Unbalanced Nginx location in {path}")
        source = source[: match.start()] + source[end:]


# Replace previous static editor variants. Keep the legacy API proxy throughout
# the canary; removing it is a separate, explicit retirement action.
content = remove_location(content, r"=?\s*/editor-masivo/?")
if os.environ.get("RETIRE_LEGACY_CATALOGOPRO") == "1":
    content = remove_location(content, r"/api/catalogopro/")

last_brace = content.rfind("}")
if last_brace < 0:
    raise SystemExit(f"Invalid Nginx configuration in {path}")

block = r'''
    # CatálogoPRO editor backed by the official WEBFLEXS API.
    location = /editor-masivo {
        return 301 /editor-masivo/;
    }

    location /editor-masivo/ {
        alias /var/www/catalogopro/editor-masivo/;
        try_files $uri $uri/ /editor-masivo/index.html;
    }
'''
content = content[:last_brace].rstrip() + "\n" + block + "\n" + content[last_brace:]
path.write_text(content, encoding="utf-8")
PY

nginx -t
systemctl reload nginx

echo "Editor deployed at /editor-masivo/."
echo "Keep FEATURE_EXTERNAL_EDITOR_WRITES=False until staging validation is approved."
if [[ "${RETIRE_LEGACY_CATALOGOPRO}" != "1" ]]; then
    echo "Legacy CatalogoPRO remains active for canary fallback."
fi
