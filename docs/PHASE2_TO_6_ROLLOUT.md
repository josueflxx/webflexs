# FLEXS - Fases 2 a 6 (sin ruptura)

## Resumen implementado

### Fase 2 - Background jobs
- Despachador de imports con backend configurable:
  - `thread` (fallback por defecto)
  - `celery` (si `FEATURE_BACKGROUND_JOBS_ENABLED=True`)
- Runner unificado:
  - `core/services/import_execution_runner.py`
- Task celery:
  - `core/tasks.py`

### Fase 3 - Busqueda avanzada
- Nuevo servicio:
  - `core/services/advanced_search.py`
- Integrado en:
  - catalogo (`catalog/views.py`)
  - admin productos (`admin_panel/views.py`)
  - sugerencias (`core/views.py`)
- Fallback automatico si no hay PostgreSQL/`pg_trgm`.

### Fase 4 - Workflow ERP por rol
- Servicio de workflow:
  - `orders/services/workflow.py`
- Roles soportados:
  - `admin`, `ventas`, `deposito`, `facturacion`
- Integracion:
  - validacion de transiciones en admin pedido detalle
  - endpoints API de cola y workflow:
    - `GET /api/v1/orders/queue/`
    - `GET /api/v1/orders/<id>/workflow/`

### Fase 5 - Observabilidad
- Request ID en cada respuesta:
  - middleware `RequestIDMiddleware` -> header `X-Request-ID`
- Logging configurable por `LOG_LEVEL`
- Integracion opcional con Sentry:
  - `FEATURE_OBSERVABILITY_ENABLED=True` + `SENTRY_DSN`

### Fase 6 - CI/CD
- GitHub Actions:
  - `.github/workflows/ci.yml`
  - ejecuta `check` y tests en push/pr.

## Variables nuevas (.env)

```env
FEATURE_BACKGROUND_JOBS_ENABLED=False
FEATURE_ADVANCED_SEARCH_ENABLED=False
FEATURE_OBSERVABILITY_ENABLED=False
ORDER_REQUIRE_PAYMENT_FOR_CONFIRMATION=False

CELERY_BROKER_URL=redis://127.0.0.1:6379/0
CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/0
CELERY_TASK_DEFAULT_QUEUE=flexs-default
CELERY_TASK_ALWAYS_EAGER=False
CELERY_TASK_EAGER_PROPAGATES=False

LOG_LEVEL=INFO
SENTRY_DSN=
SENTRY_TRACES_SAMPLE_RATE=0.05
SENTRY_ENVIRONMENT=production
```

## Worker Celery (opcional)

Si activas `FEATURE_BACKGROUND_JOBS_ENABLED=True`, iniciar worker:

```bash
cd /var/www/webflexs
source venv/bin/activate
export DJANGO_SETTINGS_MODULE=flexs_project.settings.production
celery -A flexs_project.celery worker -l INFO -Q flexs-default
```

## Recomendacion de activacion gradual
1. Deploy con flags en `False`.
2. Activar `FEATURE_ADVANCED_SEARCH_ENABLED=True` y validar busqueda.
3. Activar `FEATURE_BACKGROUND_JOBS_ENABLED=True` una vez que Celery worker este estable.
4. Activar `FEATURE_OBSERVABILITY_ENABLED=True` cuando Sentry ya este configurado.

