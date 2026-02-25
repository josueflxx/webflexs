# FLEXS - Fase 0 + Fase 1 (Rollout seguro)

## Objetivo
Implementar mejoras estructurales sin romper operacion:

1. Fase 0: feature flags, checklist de despliegue y rollback.
2. Fase 1: API v1 read-only para integraciones (`/api/v1/`).

## Cambios incluidos

- `FEATURE_API_V1_ENABLED` para activar/desactivar API v1 por entorno.
- Configuracion DRF con autenticacion por sesion/basic y throttling.
- Endpoints API v1:
  - `GET /api/v1/health/`
  - `GET /api/v1/catalog/categories/`
  - `GET /api/v1/catalog/products/`
  - `GET /api/v1/clients/` (solo staff)
  - `GET /api/v1/clients/me/` (usuario autenticado)
  - `GET /api/v1/orders/` (cliente: solo propios, staff: todos + filtros)

## Feature flags

Variables nuevas en `.env`:

```env
FEATURE_API_V1_ENABLED=True
FEATURE_BACKGROUND_JOBS_ENABLED=False
FEATURE_ADVANCED_SEARCH_ENABLED=False
FEATURE_OBSERVABILITY_ENABLED=False
```

Si `FEATURE_API_V1_ENABLED=False`, `/api/v1/` no se expone.

## Despliegue (host)

```bash
cd /var/www/webflexs
source venv/bin/activate
export DJANGO_SETTINGS_MODULE=flexs_project.settings.production
set -a
source .env
set +a

git checkout main
git pull origin main

python manage.py check
python manage.py test core.tests_api_v1 -v 2
python manage.py collectstatic --noinput

sudo systemctl restart gunicorn
sudo systemctl restart nginx
```

## Smoke tests post deploy

1. Login como cliente y probar:
   - `GET /api/v1/catalog/products/`
   - `GET /api/v1/orders/` (solo pedidos propios)
2. Login como staff y probar:
   - `GET /api/v1/clients/`
   - `GET /api/v1/orders/?user_id=<id>`
3. Probar health:
   - `GET /api/v1/health/`

## Rollback rapido

Si algo falla:

```bash
cd /var/www/webflexs
source venv/bin/activate
git log --oneline -n 5
git checkout <commit_previo_estable>
python manage.py check
python manage.py collectstatic --noinput
sudo systemctl restart gunicorn
sudo systemctl restart nginx
```

