# Deploy Safety Checklist

Checklist corta para evitar despliegues con errores evitables.

## 1) Preflight (antes de subir)

En el repo:

```bash
bash scripts/deploy_preflight.sh
```

Esto valida:
- repo limpio (sin cambios sueltos)
- `manage.py check`
- `makemigrations --check --dry-run`

## 2) Deploy en VPS

```bash
cd /var/www/webflexs
source venv/bin/activate

git fetch origin
git pull origin main

export DJANGO_SETTINGS_MODULE=flexs_project.settings.production
set -a
source .env
set +a

python manage.py migrate
python manage.py check
python manage.py collectstatic --noinput

sudo systemctl restart gunicorn
sudo systemctl restart nginx
```

## 3) Smoke test (despues de deploy)

```bash
bash scripts/smoke_check.sh https://flexsrepuestos.shop
```

Si algun endpoint no responde 2xx/3xx, revisar logs antes de continuar.
