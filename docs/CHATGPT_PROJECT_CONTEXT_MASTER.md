# CONTEXTO MAESTRO PARA CHATGPT - PROYECTO FLEXS B2B (VERSION EXTENDIDA)

## 0) Como usar este documento
Este archivo esta pensado para abrir un chat nuevo y que ChatGPT entienda el proyecto completo sin pedirte 50 aclaraciones.

Uso recomendado:
1. Copia y pega este archivo completo en el primer mensaje del nuevo chat.
2. Agrega este texto:
   - "Trabaja sobre este contexto como fuente de verdad. No reescribas el proyecto desde cero. Haz cambios incrementales, compatibles y seguros."
3. Si el cambio impacta produccion, exige siempre:
   - backup
   - comando exacto de migracion
   - checklist de rollback

---

## 1) Que es este sistema exactamente
No es una landing comun ni una API suelta.
Es una **web app B2B operativa** para repuestos, con enfoque ERP liviano + catalogo + flujo comercial interno.

Definicion breve:
- Web app operacional para equipo interno + clientes B2B.
- Admin panel para operaciones diarias.
- Catalogo publico/cliente para consulta y compra.
- Gestion de pedidos, pagos, historial y trazabilidad.
- Modulo especializado de abrazaderas a medida y cotizador tecnico.
- Integracion manual asistida con SaaS externo para facturacion oficial.

---

## 2) Objetivo de negocio real
Objetivo central: que FLEXS use esta app como sistema operativo interno y no dependa de procesos desordenados.

Problemas que resuelve:
- Catalogo muy grande (miles de productos).
- Importaciones recurrentes de proveedores.
- Necesidad de trabajar con multiples usuarios internos.
- Consultas y pedidos por WhatsApp que deben pasar a flujo formal.
- Necesidad de control de deuda/saldo por cliente.
- Necesidad de trazabilidad completa de cada pedido y cada cambio.

Resultado esperado:
- Menos errores operativos.
- Mejor control interno.
- Flujo claro desde consulta hasta pedido/pago/facturacion.

---

## 3) Arquitectura tecnica (estado actual)
- Backend: Django 5.x
- API: Django REST Framework (API v1 inicial)
- Frontend: Django templates + JavaScript vanilla + CSS propio
- DB local: SQLite (`db.sqlite3`)
- DB produccion: PostgreSQL
- Deploy: Gunicorn + Nginx + WhiteNoise
- Jobs en segundo plano:
  - Celery + Redis opcional
  - fallback a thread local cuando Celery no esta activo
- Observabilidad:
  - logging por nivel
  - request id (`X-Request-ID`)
  - Sentry opcional por feature flag

No hay SPA React/Vue en este momento.

---

## 4) Estructura general del repo
Apps principales:
- `core`
- `catalog`
- `accounts`
- `orders`
- `admin_panel`

Carpetas clave:
- `flexs_project/settings/`
- `core/static/`
- `templates/`
- `docs/`
- `media/`

Entrypoints:
- `flexs_project/urls.py`
- `flexs_project/settings/base.py`
- `manage.py`

---

## 5) Mapa de apps y responsabilidades

### 5.1 core
Responsabilidades:
- Home publica.
- Endpoint de sugerencias de busqueda.
- Endpoint de presencia de admins online.
- Endpoint para marcar offline al cerrar pagina.
- Context processors globales.
- Middlewares de seguridad y contexto de auditoria.

Modelos clave:
- `SiteSettings`
- `UserActivity`
- `CatalogAnalyticsEvent`
- `AdminAuditLog`
- `ImportExecution`

Archivos clave:
- `core/views.py`
- `core/middleware.py`
- `core/services/advanced_search.py`
- `core/services/background_jobs.py`
- `core/services/import_manager.py`
- `core/services/import_execution_runner.py`

### 5.2 catalog
Responsabilidades:
- Catalogo de productos.
- Arbol de categorias/subcategorias.
- Proveedores.
- Filtros y visibilidad publica.
- Logica y parser de abrazaderas.
- Solicitudes de abrazaderas a medida.

Modelos clave:
- `Category`
- `CategoryAttribute`
- `Supplier`
- `Product`
- `ClampSpecs`
- `ClampMeasureRequest`

Servicios clave:
- `catalog/services/clamp_quoter.py`
- `catalog/services/clamp_code.py`
- `catalog/services/clamp_request_products.py`

### 5.3 accounts
Responsabilidades:
- Autenticacion basica (login/logout).
- Solicitud de cuentas nuevas.
- Perfil extendido de clientes.
- Registro de pagos de cliente.
- Calculo de saldo corriente.

Modelos clave:
- `ClientProfile`
- `AccountRequest`
- `ClientPayment`

### 5.4 orders
Responsabilidades:
- Carrito.
- Checkout.
- Pedido y sus items con precios congelados.
- Historial de estados por pedido.
- Workflow por roles.
- Favoritos de cliente.
- Cotizaciones guardadas del cotizador.

Modelos clave:
- `Cart`
- `CartItem`
- `Order`
- `OrderItem`
- `OrderStatusHistory`
- `ClampQuotation`
- `ClientFavoriteProduct`

Servicio clave:
- `orders/services/workflow.py`

### 5.5 admin_panel
Responsabilidades:
- Backoffice completo de operacion.
- Gestion de productos/categorias/proveedores/clientes.
- Solicitudes de cuenta.
- Pedidos y pagos.
- Cotizador.
- Importadores.
- Permisos de administradores.
- Configuracion de sitio.

Rutas principales:
- Dashboard, productos, categorias, proveedores
- solicitudes medida
- clientes + historial
- pedidos + detalle
- pagos
- cotizador
- importador + historial + rollback
- admins/permisos

Archivo clave:
- `admin_panel/views.py`
- `admin_panel/urls.py`

---

## 6) Rutas principales (mapa funcional)

### Sitio y API util
- `/` home publica
- `/api/search-suggestions/`
- `/api/admin-presence/`
- `/api/go-offline/`

### Catalogo y producto
- `/catalogo/`
- `/catalogo/producto/<sku>/`
- `/catalogo/abrazaderas-a-medida/`
- `/catalogo/abrazaderas-a-medida/<id>/agregar-carrito/`

### Cuenta y autenticacion
- `/accounts/login/`
- `/accounts/logout/`
- `/accounts/solicitar/`
- `/accounts/redirect/`

### Pedidos (cliente)
- `/pedidos/carrito/`
- `/pedidos/checkout/`
- `/pedidos/portal/`
- `/pedidos/pedidos/`
- `/pedidos/pedidos/<id>/`

### Admin panel
- `/admin-panel/`
- `/admin-panel/productos/`
- `/admin-panel/categorias/`
- `/admin-panel/proveedores/`
- `/admin-panel/abrazaderas-a-medida/`
- `/admin-panel/clientes/`
- `/admin-panel/solicitudes/`
- `/admin-panel/pedidos/`
- `/admin-panel/pagos/`
- `/admin-panel/cotizador/`
- `/admin-panel/importar/`
- `/admin-panel/admins/`
- `/admin-panel/configuracion/`

### API v1
Se expone si `FEATURE_API_V1_ENABLED=True`.
Base: `/api/v1/`

---

## 7) Modelo de datos de negocio (resumen relacional)

Relaciones clave:
- `User` 1-1 `ClientProfile`
- `User` 1-1 `Cart`
- `Cart` 1-N `CartItem`
- `User` 1-N `Order`
- `Order` 1-N `OrderItem`
- `Order` 1-N `OrderStatusHistory`
- `ClientProfile` 1-N `ClientPayment`
- `Category` arbol por `parent` (self FK)
- `Product` N-M `Category` via `categories`
- `Product` 0-1 `Category` via `category` legacy principal
- `Supplier` 1-N `Product` via `supplier_ref`
- `ClampMeasureRequest` opcionalmente vincula `Product`

Importante:
- Se mantiene dualidad `category` + `categories` por compatibilidad.
- La logica actual prioriza flexibilidad M2M.
- El campo legacy sigue siendo util para vistas viejas y categoria principal.

---

## 8) Reglas funcionales criticas (no romper)

### 8.1 Productos y categorias
1. Un producto puede pertenecer a multiples categorias.
2. Si una categoria padre se desactiva, se desactivan descendientes.
3. Producto visible en catalogo si:
   - `is_active=True`
   - y al menos una categoria vinculada activa
   - o sin categoria si la consulta incluye uncategorized.
4. Un producto inactivo nunca debe mostrarse en catalogo cliente.

### 8.2 Pedidos y trazabilidad
1. `OrderItem` guarda snapshot (`price_at_purchase`, `product_name`, `product_sku`).
2. Cambios posteriores de precio en `Product` no alteran pedidos historicos.
3. Cambios de estado registran historial en `OrderStatusHistory`.
4. Estados actuales de `Order`:
   - `draft`
   - `confirmed`
   - `preparing`
   - `shipped`
   - `delivered`
   - `cancelled`
5. Si esta activado `ORDER_REQUIRE_PAYMENT_FOR_CONFIRMATION=True`, no se confirma con saldo pendiente.

### 8.3 Pagos y saldo
1. `ClientPayment` puede asociarse a pedido o ser pago general.
2. Pago se puede anular (`is_cancelled`) sin borrar historial.
3. Saldo cliente:
   - total pedidos en estados operativos (`confirmed`, `preparing`, `shipped`, `delivered`)
   - menos total pagos no anulados.

### 8.4 Abrazaderas a medida
1. Cliente puede solicitar medida especial.
2. Admin revisa y confirma precio/lista.
3. Estado completado habilita flujo de agregacion al carrito segun reglas actuales.
4. Admin puede publicar la medida como producto estandar en catalogo.

---

## 9) Workflow por roles de pedidos
Archivo: `orders/services/workflow.py`

Roles internos:
- `admin`
- `ventas`
- `deposito`
- `facturacion`

Resolucion de rol:
- superuser -> `admin`
- grupos de Django -> rol equivalente
- `staff` sin grupo -> `admin` (compatibilidad)

Colas por rol:
- `admin`: todos los estados
- `ventas`: `draft`, `confirmed`
- `deposito`: `confirmed`, `preparing`
- `facturacion`: `shipped`, `delivered`

Transiciones por rol:
- `admin`: todas las validas por modelo
- `ventas`:
  - `draft -> confirmed`
  - `draft -> cancelled`
  - `confirmed -> cancelled`
- `deposito`:
  - `confirmed -> preparing`
  - `preparing -> shipped`
- `facturacion`:
  - `shipped -> delivered`

---

## 10) Permisos administrativos y criterio de superadmin
Hay una regla especial de negocio:
- El superadmin principal operativo es `josueflexs`.

Consecuencia:
- Ciertas operaciones sensibles estan restringidas al superadmin principal.
- Admins staff no-superadmin tienen acceso operativo, pero con limites en acciones criticas.

Ejemplos de limites en panel de clientes:
- Pueden usar historial y pagos.
- No deben poder borrar clientes segun la politica actual.
- Modificar credenciales puede quedar restringido.

Archivo principal de esta logica:
- `admin_panel/views.py`

---

## 11) Modulo de cotizador y abrazaderas (detalle tecnico)

### 11.1 Objetivo
Calcular costo y listas de venta de abrazaderas con reglas de negocio propias.

### 11.2 Servicio central
- `catalog/services/clamp_quoter.py`

### 11.3 Tabla de peso por diametro
`CLAMP_WEIGHT_MAP`:
- `7/16`: 0.76
- `1/2`: 0.993
- `9/16`: 1.258
- `5/8`: 1.553
- `3/4`: 2.236
- `7/8`: 3.043
- `1`: 3.975
- `18`: 1.92
- `20`: 2.5
- `22`: 3.043
- `24`: 3.8

### 11.4 Reglas importantes actuales
- Laminadas permitidas solo en diametros:
  - `3/4`
  - `1`
  - `7/8`
- Ajuste por forma:
  - `PLANA`: +20
  - `SEMICURVA`: +10
  - `CURVA`: +0
- Zincado: recargo x1.20
- Laminada: actualmente se aplica regla de costo a la mitad vs trefilada (`/2.0`)
- Listas:
  - Lista 1: x1.4
  - Lista 2: x1.5
  - Lista 3: x1.6
  - Lista 4: x1.7
  - Facturacion: x2.0

### 11.5 Defaults operativos actuales
En flujo de solicitudes/custom:
- Dolar manual default: `1450`
- Precio acero default: `1.45`
- Desc proveedor default: `0`
- Aumento general default: `40` en flujo de solicitudes a medida
- En `ClampQuotation` modelo el default historico es `23` para `general_increase_pct`

### 11.6 Parser/generador de codigos
- `catalog/services/clamp_code.py`
- Documentacion complementaria:
  - `docs/clamp_code_logic.md`

Soporta:
- parseo robusto ABL/ABT
- generacion de codigo desde atributos
- metadata de advertencia cuando falta mapeo

---

## 12) Solicitudes de abrazaderas a medida (ciclo)
Modelo: `ClampMeasureRequest`

Estados:
- `pending`
- `in_review`
- `quoted`
- `rejected`
- `completed`

Campos clave:
- datos tecnicos (`clamp_type`, `diameter`, `width_mm`, `length_mm`, `profile_type`)
- datos economicos (`dollar_rate`, `steel_price_usd`, descuentos, aumento)
- costo base y precio estimado
- lista seleccionada y precio confirmado
- notas de cliente/admin
- producto vinculado (`linked_product`) cuando se publica

Flujo tipico:
1. Cliente consulta medida.
2. Sistema calcula propuesta.
3. Admin ajusta tecnicos/costos/lista si hace falta.
4. Admin confirma precio.
5. Cliente visualiza estado/precio.
6. Se puede agregar al carrito y terminar en pedido.
7. Admin puede publicar como producto real de catalogo.

---

## 13) Importadores y ejecuciones
Panel:
- `/admin-panel/importar/`

Tipos de importacion:
- productos
- clientes
- categorias
- abrazaderas

Infra:
- `ImportExecution` guarda historial y estado.
- Polling por `task_id` para seguimiento.
- Rollback disponible en casos compatibles.

Dispatcher de jobs:
- si `FEATURE_BACKGROUND_JOBS_ENABLED=True` y backend disponible -> Celery
- si no -> thread/fallback sin romper operacion

Archivos:
- `core/services/background_jobs.py`
- `core/services/import_manager.py`
- `core/services/import_execution_runner.py`
- `core/tasks.py`

---

## 14) Busqueda, filtros y sugerencias

### 14.1 Motor base
- `core/services/advanced_search.py`
- estrategia:
  - `icontains` siempre disponible
  - trigram similarity en PostgreSQL si `FEATURE_ADVANCED_SEARCH_ENABLED=True`
  - fallback automatico si falla trigram/extension

### 14.2 Endpoint de sugerencias
- `/api/search-suggestions/`
- minimo 2 caracteres
- limite 8 sugerencias

Scopes implementados:
- `catalog`
- `admin_products`
- `admin_supplier_products`
- `admin_categories`
- `admin_clients`
- `admin_orders`
- `admin_suppliers`
- `admin_payments`
- `admin_clamp_requests`
- `admin_admins`

Frontend:
- `core/static/core/js/search_suggestions.js`
- autodescubre scope por URL
- soporta teclado (arriba/abajo/enter/escape)
- fallback robusto si API falla

---

## 15) UX y frontend actuales

### Publico/cliente
- Header navegable con logo marca.
- Catalogo con filtros avanzados.
- Panel lateral categorias tipo arbol.
- Sugerencias de busqueda.
- Vista detalle de producto y carrito.
- Seccion de contacto con mapa interactivo Leaflet + geocoding abierto (sin Google API obligatoria).

### Admin
- Layout con sidebar izquierda y panel de presencia derecha.
- Tablas largas con overflow horizontal.
- Drag horizontal con click derecho en contenedores admin (mejora de usabilidad).
- Seleccion masiva de productos con soporte para "todos los filtrados".
- Indicador de cantidad seleccionada en operaciones masivas.

---

## 16) Seguridad implementada

### 16.1 Middlewares clave
Archivo: `core/middleware.py`

- `RequestIDMiddleware`
  - agrega `X-Request-ID` a respuesta
- `SessionIdleTimeoutMiddleware`
  - expira sesion por inactividad
- `AuditRequestContextMiddleware`
  - contexto request para auditoria
- `AuthSessionIsolationMiddleware`
  - cache-control privado/no-store en rutas sensibles
- `UserActivityMiddleware`
  - presencia online de admins con throttle por cache

### 16.2 Configuracion de seguridad
Archivo: `flexs_project/settings/base.py` y `production.py`

- cookies seguras y httpOnly configurables por env
- SameSite configurado
- headers de seguridad (nosniff, referrer policy, frame deny)
- lockout de login:
  - `LOGIN_MAX_FAILED_ATTEMPTS`
  - `LOGIN_LOCKOUT_SECONDS`
  - `LOGIN_ATTEMPT_WINDOW_SECONDS`
- timeout de sesion por inactividad:
  - `SESSION_IDLE_TIMEOUT_SECONDS`

---

## 17) Observabilidad y auditoria
- Logging configurable con `LOG_LEVEL`.
- Request-id en responses y logs.
- `AdminAuditLog` para acciones sensibles.
- `ImportExecution` para trazabilidad de importaciones.
- Sentry opcional si:
  - `FEATURE_OBSERVABILITY_ENABLED=True`
  - `SENTRY_DSN` configurado

---

## 18) Feature flags (control de rollout)
Flags principales:
- `FEATURE_API_V1_ENABLED`
- `FEATURE_BACKGROUND_JOBS_ENABLED`
- `FEATURE_ADVANCED_SEARCH_ENABLED`
- `FEATURE_OBSERVABILITY_ENABLED`
- `ORDER_REQUIRE_PAYMENT_FOR_CONFIRMATION`

Politica:
- activar de a una por entorno
- validar impacto con smoke tests
- no activar todo junto en produccion

---

## 19) API v1 actual
Namespace: `/api/v1/`

Endpoints:
- `GET /api/v1/health/`
- `GET /api/v1/catalog/categories/`
- `GET /api/v1/catalog/products/`
- `GET /api/v1/clients/` (staff)
- `GET /api/v1/clients/me/`
- `GET /api/v1/orders/`
- `GET /api/v1/orders/queue/` (staff)
- `GET /api/v1/orders/<id>/workflow/`

Protecciones:
- DRF auth por sesion/basic
- permisos por contexto
- throttling anon/user/scoped configurable por env

Archivos:
- `core/api_v1/urls.py`
- `core/api_v1/views.py`
- `core/api_v1/serializers.py`
- `core/api_v1/permissions.py`

---

## 20) Entornos y configuracion

### Local
- Settings: `flexs_project/settings/local.py`
- DB: SQLite `db.sqlite3`
- Recomendaciones sqlite aplicadas:
  - WAL
  - timeout aumentado
  - cache tuning

### Produccion
- Settings: `flexs_project/settings/production.py`
- DB: PostgreSQL via env (`DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`)
- App server: Gunicorn
- Reverse proxy: Nginx
- static: WhiteNoise + manifest compress

---

## 21) CI actual
Archivo:
- `.github/workflows/ci.yml`

Pipeline:
1. instala dependencias
2. prepara `.env` de CI
3. `python manage.py check`
4. `python manage.py test -v 2`

---

## 22) Comandos operativos (runbook corto)

### Local (Windows PowerShell)
```powershell
cd C:\Users\Brian\Desktop\webflexs
.\venv\Scripts\activate
$env:DJANGO_SETTINGS_MODULE="flexs_project.settings.local"
python manage.py migrate
python manage.py runserver
```

### Deploy VPS (produccion)
```bash
cd /var/www/webflexs
source venv/bin/activate
export DJANGO_SETTINGS_MODULE=flexs_project.settings.production
set -a
source .env
set +a
git checkout main
git pull origin main
pip install -r requirements.txt
python manage.py check
python manage.py migrate
python manage.py collectstatic --noinput
sudo systemctl restart gunicorn
sudo systemctl restart nginx
```

---

## 23) Troubleshooting recurrente (historico)

### Error: tabla/campo inexistente en host
Sintoma:
- `ProgrammingError` por columnas nuevas o tablas M2M faltantes.

Causa:
- Codigo actualizado sin migraciones aplicadas.

Accion:
1. activar venv
2. cargar `.env`
3. `python manage.py showmigrations`
4. `python manage.py migrate`
5. restart gunicorn/nginx

### Error: no selecciona productos en acciones masivas
Sintoma:
- mensaje "No se seleccionaron productos" aun con checkboxes marcados.

Causa historica:
- parseo ambiguo de ids y doble flujo de botones.

Fix aplicado:
- parseo robusto `extract_target_product_ids_from_post`.
- soporte para ids en formato `"9.353"` -> `9353`.
- unificacion de flujo de seleccion y `product_ids_csv`.

### Error: reverse product detail con SKU que incluye slash
Fix aplicado:
- ruta de detalle usa `<path:sku>` en lugar de `<str:sku>`.

---

## 24) Convenciones para nuevos cambios (obligatorio)
1. No reescribir desde cero.
2. Cambios incrementales.
3. Compatibilidad con datos existentes.
4. Migraciones aditivas y seguras.
5. No borrar datos criticos sin alternativa de anulacion.
6. No exponer informacion sensible interna a clientes.
7. Separar cambios de logica y cambios visuales cuando sea posible.
8. Verificar `check` y tests relevantes antes de deploy.
9. Si hay dudas, priorizar estabilidad de produccion.

---

## 25) Reglas para IA al colaborar en este repo
Instrucciones que debe seguir cualquier agente:
- usar este documento como fuente de verdad funcional
- validar codigo real antes de afirmar
- preservar comportamiento existente salvo pedido explicito
- incluir pasos de verificacion manual
- en cambios de host, entregar comandos exactos y ordenados

Si hay conflicto entre "idea nueva" y "operacion actual":
- gana operacion actual en produccion
- la mejora se implementa por fases y con flag si hace falta

---

## 26) Prompt recomendado para abrir chat nuevo (version larga)
Pegar este bloque junto con este documento:

```text
Usa el CONTEXTO MAESTRO FLEXS como fuente de verdad.
No reescribas el proyecto desde cero.
Trabaja de forma incremental, compatible con el codigo actual y con foco en estabilidad.
Antes de cualquier cambio:
1) analiza los archivos existentes
2) explica impacto tecnico y de negocio
3) indica riesgos de migracion/deploy
4) propone validaciones
Si el cambio toca produccion, dame backup + comandos exactos + rollback.
Respeta roles/permisos, no rompas flujos de clientes/admin, y evita exponer datos internos sensibles.
```

---

## 27) Prompt corto (para uso rapido)
```text
Trabaja sobre este proyecto Django FLEXS B2B sin reescribir de cero.
Haz cambios incrementales, seguros y compatibles con produccion.
Primero analiza, luego implementa, y dame checklist de validacion + comandos de deploy/rollback.
```

---

## 28) Resumen ejecutivo final
Este proyecto ya funciona como plataforma operativa real de la empresa:
- catalogo grande
- operacion interna multiusuario
- pedidos/pagos/saldo
- cotizador y solicitudes de abrazaderas a medida
- importaciones masivas con trazabilidad
- seguridad y observabilidad base

La estrategia correcta es seguir evolucionandolo por capas, sin romper la base.
