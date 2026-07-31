# Auditoría de preparación ARCA — ejecución 2026-07-29

## 1. Resumen de cambios

- Se corrigió el arranque causado por `fiscal_document_recover`: la vista
  existía, pero no estaba incluida en `admin_panel.views.fiscal.__all__`, por
  lo que el import por wildcard no la exponía a las URLs.
- Se congeló la identidad fiscal y el contenido histórico de cada comprobante:
  emisor, receptor, punto de venta, entorno, ítems, importes y hashes.
- Se agregaron claves UUID de correlación e idempotencia, identidad estable de
  serie, metadatos de despacho/recuperación y auditoría de mutaciones
  rechazadas.
- Se endurecieron las transiciones: un comprobante autorizado no admite
  cambios fiscales, cambios de ítems ni borrado físico. La reversión legal se
  representa mediante nota de crédito relacionada.
- La frontera previa al transporte se persiste antes de entrar al envío. Un
  resultado ambiguo bloquea la serie y sólo permite consulta/recuperación.
- El PDF falla cerrado: sin snapshot verificado, número, fecha, CAE y
  vencimiento persistidos no se presenta como autorizado ni se genera QR.
- El contenido impreso se obtiene del snapshot histórico; no consulta datos
  vivos de empresa, cliente u orden.
- Los documentos de homologación muestran
  `HOMOLOGACIÓN – SIN VALIDEZ FISCAL`.
- Se corrigieron locks que SQLite toleraba pero PostgreSQL rechazaba:
  `FOR UPDATE OF self` bloquea la fila principal sin intentar bloquear el lado
  nullable de un `LEFT JOIN`.
- Se preparó un plan verificable de concurrencia e idempotencia para una etapa
  posterior.

No se hizo deploy, merge, push, commit, conexión ARCA, uso de certificados,
cambio de producción ni cambio de Firebase Rules.

## 2. Archivos modificados o revisados en el alcance

### Arranque y vistas

- `admin_panel/views/fiscal.py`: export de `fiscal_document_recover`, locking
  PostgreSQL, render fiscal histórico, autorización fail-closed y QR.
- `admin_panel/urls.py`: ruta de recuperación validada.
- `admin_panel/templates/admin_panel/fiscal/print.html`: estados visuales,
  watermark, snapshot y ocultamiento de CAE cuando el documento no es legal.

### Modelo e integridad fiscal

- `accounts/models.py`: identidad fiscal normalizada y metadatos de condición
  IVA.
- `accounts/fiscal_identity.py`: normalización/validación de documento fiscal.
- `core/models.py`: estados, snapshots, series, intentos, auditoría,
  constraints e inmutabilidad.
- `core/services/fiscal_integrity.py`: canonicalización y hash de snapshots.
- `core/services/fiscal_documents.py`: preparación histórica e idempotencia
  local.
- `core/services/fiscal_emission.py`: reserva de serie, frontera de despacho y
  resolución segura.
- `core/services/fiscal_recovery.py`: recuperación sólo por consulta.
- `core/services/arca_client.py`: callback persistente inmediatamente antes del
  transporte.
- `core/services/pdf_generator.py`: QR desde evidencia persistida y carga
  diferida del motor PDF.

### Integración PostgreSQL

- `flexs_project/settings/test_postgres.py`: settings aislados para PostgreSQL.
- `admin_panel/views/orders.py`: lock compatible con PostgreSQL al registrar
  pagos.
- `orders/services/request_workflow.py`: lock compatible con PostgreSQL en la
  conversión idempotente de solicitudes.

### Tests y fixtures estabilizados

- `core/test_fiscal_readiness.py`: 16 casos nuevos de PDF, QR, estados,
  permisos, idempotencia, fallo de persistencia y recuperación.
- `core/test_fiscal_admin_immutability.py`: mutaciones administrativas.
- `core/test_arca_security.py`: configuración, credenciales y transporte
  simulados.
- `accounts/tests.py`
- `admin_panel/tests.py`
- `admin_panel/test_seller_performance.py`
- `core/tests.py`
- `core/test_commercial_rules.py`
- `core/test_warehouse_stock.py`
- `orders/tests.py`

Los últimos archivos contienen ajustes de fixtures para respetar CUIT válidos,
transiciones reales y límites que PostgreSQL sí aplica. No se redujeron
validaciones.

### Ajustes no fiscales mínimos detectados por la suite

- `templates/base.html`: reemplazo de dos emojis públicos por SVG/texto según
  una prueba de presentación existente. El archivo ya tenía cambios indexados
  ajenos y queda marcado como mixto.

### Documentación

- `docs/arca/ARCA_HOMOLOGATION_CONCURRENCY_TEST_PLAN.md`: plan de concurrencia.
- `docs/arca/ARCA_READINESS_EXECUTION_REPORT_2026-07-29.md`: este informe.

## 3. Migraciones

### `accounts.0019_client_fiscal_identity`

Modelo afectado: `ClientProfile`.

Agrega:

- `normalized_fiscal_document`, único e indexado.
- `iva_condition_arca_id`.
- `iva_condition_source`.
- `iva_condition_validated_at`.

El backfill normaliza sólo identidades legacy inequívocas. Los duplicados se
conservan y quedan `NULL` para revisión; no se elimina ni fusiona información.

### `core.0033_arca_fiscal_integrity`

Modelos nuevos:

- `ArcaReceiverIvaConditionParameter`
- `ArcaVatRateParameter`
- `FiscalMutationAudit`
- `FiscalSeriesReconciliation`

Agrega snapshots, UUIDs, hashes, timestamps de preparación/despacho/resolución,
metadatos de recuperación, identidad de serie y evidencia de cada intento.

Constraints relevantes:

- `uniq_fiscal_doc_company_pos_type_number` (existente y verificado).
- `uniq_fiscal_doc_identity_number`.
- `uniq_active_arca_operation_per_order`.
- `uniq_fiscal_series_identity`.
- `uniq_fiscal_attempt_operation_number`.
- Índice único de `FiscalDocument.idempotency_key`.
- Índices únicos de correlación de documento e intento.

Índices operativos verificados:

- identidad fiscal por entorno/CUIT/POS/tipo;
- estado + próxima recuperación;
- identidad y bloqueo de serie;
- correlación de intentos.

`python manage.py makemigrations --check --dry-run` devolvió
`No changes detected`.

## 4. Resultados de pruebas

| Comando / validación | Base | Aprobadas | Fallidas | Errores | Omitidas |
|---|---:|---:|---:|---:|---:|
| `python manage.py check` | configuración local | n/a | 0 | 0 | 0 |
| servidor `127.0.0.1:8017 --noreload` + HTTP | SQLite local | 1 | 0 | 0 | 0 |
| import fiscal + import URLs + reverse recovery | SQLite local | 3 | 0 | 0 | 0 |
| migración completa desde base vacía | PostgreSQL 18 | todas | 0 | 0 | 0 |
| upgrade `accounts 0018/core 0032` → latest con datos legacy | PostgreSQL 18 | todas | 0 | 0 | 0 |
| bloque fiscal/seguridad/inmutabilidad | PostgreSQL 18 | 60 | 0 | 0 | 0 |
| `python manage.py test --settings=...test_postgres` | PostgreSQL 18 | 559 | 0 | 0 | 0 |
| `python manage.py test accounts orders` | SQLite | 57 | 0 | 0 | 0 |
| `python manage.py test admin_panel` | SQLite | 205 | 0 | 0 | 0 |
| `python manage.py test catalog` | SQLite | 126 | 0 | 0 | 0 |
| `python manage.py test core` | SQLite | 171 | 0 | 0 | 0 |
| Total SQLite por segmentos | SQLite | 559 | 0 | 0 | 0 |
| smoke binario `generate_document_pdf(...)` | entorno local | 0 | 0 | 1 | 0 |

El `INFO arca.I001` es intencional: `ARCA_ENABLED=False` y
`ARCA_ENVIRONMENT=disabled`.

El primer pase global SQLite agotó el timeout al ejecutarse en paralelo y el
segundo agotó diez minutos. No se contabilizaron como éxito. Los mismos 559
casos se ejecutaron luego por aplicaciones, con resultado verde y conteo
exacto. PostgreSQL sí completó el comando global.

La prueba binaria real falló porque el intérprete local no tiene instalado
`weasyprint`. La dependencia está declarada en `requirements.txt`. Las pruebas
de contenido/seguridad PDF, respuesta HTTP y QR pasan; la respuesta de la
aplicación ante ausencia del motor es controlada y no fabrica un PDF.

Bases temporales usadas:

- `webflexs_arca_clean_a925eb4`
- `webflexs_arca_upgrade_a925eb4`

Contienen sólo datos sintéticos de auditoría. No se tocó producción.

## 5. Estado de Git

Estado final:

- 139 entradas con el resumen estándar de Git.
- 158 rutas con archivos no rastreados expandidos.
- 74 rutas modificadas no indexadas.
- 84 archivos no rastreados.
- 3 rutas ya indexadas y además modificadas (`MM`):
  - `admin_panel/templates/admin_panel/base.html`
  - `core/static/core/css/base.css`
  - `templates/base.html`

Clasificación primaria de las 158 rutas:

- Cambios fiscales necesarios: 20.
- Tests: 18.
- Migraciones: 7.
- Documentación: 22.
- Cambios no relacionados o mixtos: 69.
- Archivos generados: 20.
- Potencialmente sensibles: 2.

Potencialmente sensibles o de autoría incierta:

- `.env` existe y está ignorado; no fue leído.
- `.claude/settings.local.json` está no rastreado.
- `catalogopro_build/api/appsettings.json` está modificado; no se inspeccionaron
  valores.
- `.ai/` contiene 12 documentos locales no rastreados de autoría no confirmada.

Generados:

- 20 bundles con hash en `catalogopro_build/frontend/assets/`, incluyendo dos
  archivos rastreados borrados y variantes nuevas no rastreadas.

Archivos que conviene ignorar si no forman parte deliberada del repositorio:

- `.claude/settings.local.json`.
- `.ai/` si es metadata local y no documentación de proyecto.
- bundles con hash si el repositorio decide producirlos en CI; antes hay que
  confirmar la política actual porque dos bundles sí están rastreados.

No se ejecutó `git add`, commit, merge ni push.

### Propuesta de commits

1. `fix(fiscal): export recovery view and restore Django startup`
2. `feat(fiscal): freeze identity, snapshots and state transitions`
3. `feat(fiscal): enforce PostgreSQL idempotency and series constraints`
4. `fix(fiscal): fail closed PDF and QR rendering`
5. `test(fiscal): cover permissions, recovery, PostgreSQL and immutability`
6. `docs(arca): add concurrency and homologation readiness plan`

Antes de crear esos commits hay que:

1. Revisar y preservar los tres archivos `MM`.
2. Separar por hunks los archivos mixtos (`core/models.py`,
   `admin_panel/tests.py`, `templates/base.html`, entre otros).
3. Confirmar autoría de las 69 rutas no relacionadas y de `.ai/`.
4. Revisar `appsettings.json` por secretos sin incluirlos en la conversación.
5. Definir la política de bundles generados.

Un `reset`, `checkout`, `clean` o cambio de rama podría perder 74 cambios no
indexados y 84 archivos no rastreados, incluidas migraciones y documentación.

## 6. Riesgos restantes

### Críticos

1. El árbol de Git sigue mezclado y con autoría no aclarada. Los cambios
   fiscales están identificados, pero todavía no están físicamente aislados en
   commits revisables.
2. Este intérprete local no puede producir el binario PDF porque falta instalar
   `weasyprint`. Antes de homologación debe instalarse el entorno desde
   `requirements.txt` y pasar un smoke real que valide `%PDF`.

### No críticos

1. La prueba concurrente multi-thread de PostgreSQL está especificada, pero aún
   no implementada; los constraints e idempotencia secuencial sí fueron
   probados.
2. Las bases PostgreSQL temporales siguen presentes con datos sintéticos.
3. Git advierte futura conversión LF→CRLF; `git diff --check` no detectó errores
   de whitespace.
4. La integración ARCA permanece deliberadamente deshabilitada y no fue
   probada contra homologación.

## 7. Veredicto actualizado

`TODAVÍA NO LISTO`

El núcleo fiscal, las migraciones y las 559 pruebas están verdes en PostgreSQL,
y el contenido PDF/QR falla cerrado. El veredicto se mantiene por los dos
bloqueos críticos restantes: motor PDF binario ausente en el entorno local y
árbol Git todavía mezclado/sensible sin separación segura.
