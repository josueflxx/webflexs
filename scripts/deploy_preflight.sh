#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -n "$(git status --porcelain)" ]]; then
  echo "ERROR: el repositorio tiene cambios sin confirmar."
  git status --short
  exit 1
fi

python manage.py check
python manage.py makemigrations --check --dry-run

echo "Preflight OK: repo limpio y chequeos base correctos."
