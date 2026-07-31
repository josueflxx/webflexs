# Inventario protegido previo a homologacion ARCA

Fecha: 2026-07-30  
Rama: `codex/production-fiscal-client-upgrade-20260723`  
HEAD: `a925eb434d7e5231d07c9c149d178f21ac267a0c`

Este inventario no contiene valores de configuracion, secretos, certificados ni
contenido de `.env`. Solo registra nombres, estados y hashes Git.

## Resumen inicial

- 74 rutas modificadas o borradas en el working tree.
- 84 archivos no rastreados antes de crear este inventario.
- 3 archivos indexados y modificados nuevamente (`MM`).
- 3 archivos presentes en el index.
- Diff no indexado: 9.795 inserciones y 2.354 eliminaciones sobre 74 archivos.
- Diff indexado: 11 inserciones y 5 eliminaciones sobre 3 archivos.
- No se ejecuto reset, restore, checkout, clean, merge, push ni deploy.

## Archivos MM

### `admin_panel/templates/admin_panel/base.html`

- HEAD: `5a9901fc4b1ac3c5302c168cb9d1989aba7ccad0`
- Index: `3065ee40090bd110eec94df1fee24bb62d707b60`
- Working tree: `d258bd45089e7b9274c18db20543f7f1757a1b25`
- Index: agrega `style="display: none;"` al logo claro del panel.
- Working tree: rediseña la navegacion administrativa, agrega smart-select,
  cambia grupos de herramientas y persistencia de scroll.
- Relacion con esta tarea: ninguna relacion directa con PDF fiscal o
  concurrencia.
- Riesgo: alto; agregar el archivo completo mezclaria dos cambios de UI de
  momentos/autorias no demostrables.
- Decision: no modificar ni agregar al index.

### `core/static/core/css/base.css`

- HEAD: `a096761eb2bb9811ca9e6b6f47a68ddc609a8f68`
- Index: `3458fb3396ca83c599a92a74e88ec40b0ba1c203`
- Working tree: `d2754877c63ba70e1cf24e38d7f75f0bf5a2c9a8`
- Index: refuerza selectores de logos oscuro/claro.
- Working tree: cambia breakpoints, tamaños y espaciado del header publico.
- Relacion con esta tarea: ninguna relacion directa.
- Riesgo: alto; el archivo contiene dos capas de cambios visuales.
- Decision: no modificar ni agregar al index.

### `templates/base.html`

- HEAD: `bc87b22428f37513d38d91e73bf003cdce1a863a`
- Index: `e6a2603d68e2e0ab48ea3de6a92648f820e826f4`
- Working tree: `adbabff069022cd086a6add29eb2941e7c513e83`
- Index: actualiza cachebuster y oculta inicialmente el logo claro.
- Working tree: rediseña header publico, navegacion, iconos y smart-select.
- Relacion con esta tarea: no es necesario para WeasyPrint, PDF fiscal ni
  concurrencia.
- Riesgo: alto; contiene un pequeño ajuste previo realizado durante la auditoria
  y una capa extensa de UI de autoria mixta.
- Decision: no modificar ni agregar al index.

## Clasificacion primaria

Cada ruta tiene una categoria primaria. Las rutas pueden tener ademas las
banderas `MM`, `sensitive-name` o `uncertain-author`.

### Codigo fiscal

- `accounts/fiscal_identity.py`
- `accounts/services/fiscal_review.py`
- `admin_panel/views/fiscal.py`
- `core/services/arca_client.py`
- `core/services/arca_config.py`
- `core/services/arca_credentials.py`
- `core/services/arca_parameters.py`
- `core/services/arca_ticket_cache.py`
- `core/services/arca_transport.py`
- `core/services/fiscal.py`
- `core/services/fiscal_documents.py`
- `core/services/fiscal_emission.py`
- `core/services/fiscal_integrity.py`
- `core/services/fiscal_recovery.py`

Archivos mixtos con cambios fiscales y no fiscales que requieren separacion por
hunks antes de cualquier commit:

- `accounts/models.py`
- `admin_panel/urls.py`
- `admin_panel/views/helpers.py`
- `admin_panel/views/orders.py`
- `core/admin.py`
- `core/apps.py`
- `core/models.py`
- `core/tasks.py`
- `flexs_project/settings/base.py`
- `orders/services/request_workflow.py`

### Tests fiscales o mixtos

- `accounts/tests.py`
- `admin_panel/tests.py`
- `core/test_arca_security.py`
- `core/test_fiscal_admin_immutability.py`
- `core/test_fiscal_readiness.py`
- `core/tests.py`
- `orders/tests.py`

### Migraciones

- `accounts/migrations/0018_clientprofile_commercial_observation_clienttask.py`
- `accounts/migrations/0019_client_fiscal_identity.py`
- `catalog/migrations/0029_product_allow_negative_stock_product_is_purchasable_and_more.py`
- `catalog/migrations/0030_brand_cataloging_workflow.py`
- `core/migrations/0031_sitesettings_warehouse_stock_enabled_and_more.py`
- `core/migrations/0032_warehouse_stock_balance_enabled.py`
- `core/migrations/0033_arca_fiscal_integrity.py`

Solo `accounts.0019` y `core.0033` son exclusivamente fiscales. Las otras
migraciones son dependencias de estado o cambios funcionales preexistentes.

### Codigo PDF o QR

- `admin_panel/templates/admin_panel/fiscal/print.html`
- `core/services/pdf_generator.py`

### Dependencias o configuracion reproducible

- `.env.example` (`sensitive-name`; es plantilla, no contiene valores auditados)
- `.gitignore`
- `requirements-dev.txt`

`requirements.txt` ya declara `weasyprint>=61.0` y `qrcode>=7.4`, pero no tiene
cambios Git al inicio de esta tarea.

### Documentacion

- `.ai/ARCA_BLOCKERS.md`
- `.ai/ARCA_FIX_BASELINE.md`
- `.ai/ARCA_INTEGRATION.md`
- `.ai/ARCA_READINESS_REPORT.md`
- `.ai/ARCA_TEST_PLAN.md`
- `.ai/DATA_MODEL.md`
- `.ai/FACTURACION_INTEGRATION_PLAN.md`
- `.ai/IMPLEMENTATION_PHASES.md`
- `.ai/PROJECT_ANALYSIS.md`
- `.ai/PROJECT_STATUS.md`
- `.ai/QUESTIONS_FOR_USER.md`
- `.ai/SECURITY_REVIEW.md`
- `docs/EXTERNAL_EDITOR_INTEGRATION.md`
- `docs/arca/ARCA_ARCHITECTURE.md`
- `docs/arca/ARCA_DATA_MODEL.md`
- `docs/arca/ARCA_HOMOLOGATION_CONCURRENCY_TEST_PLAN.md`
- `docs/arca/ARCA_IMPLEMENTATION_PLAN.md`
- `docs/arca/ARCA_OFFICIAL_RESEARCH.md`
- `docs/arca/ARCA_OPEN_QUESTIONS.md`
- `docs/arca/ARCA_PROJECT_AUDIT.md`
- `docs/arca/ARCA_READINESS_EXECUTION_REPORT_2026-07-29.md`
- `docs/arca/ARCA_SECURITY.md`

La autoria de los 12 archivos `.ai/` es incierta. No se modificaran.

### Estado MM

- `admin_panel/templates/admin_panel/base.html`
- `core/static/core/css/base.css`
- `templates/base.html`

### Configuracion local

- `.claude/settings.local.json` (`sensitive-name`, `uncertain-author`)

Debe permanecer fuera de commits. Es candidato a `.gitignore`.

### Archivo potencialmente sensible

- `catalogopro_build/api/appsettings.json` (`sensitive-name`,
  `uncertain-author`)

No se inspeccionaron ni imprimieron valores. No debe agregarse a commits de esta
tarea.

### Archivos generados

- `catalogopro_build/frontend/assets/index-B7XGD973.js` (borrado)
- `catalogopro_build/frontend/assets/index-Dfpqd_eM.css` (borrado)
- `catalogopro_build/frontend/assets/index-A6kjHoC7.js`
- `catalogopro_build/frontend/assets/index-B0J-Uyef.js`
- `catalogopro_build/frontend/assets/index-BF9Lxq6U.css`
- `catalogopro_build/frontend/assets/index-BOnr4VBX.css`
- `catalogopro_build/frontend/assets/index-BziJCGV_.js`
- `catalogopro_build/frontend/assets/index-C70PkTGw.js`
- `catalogopro_build/frontend/assets/index-CDIBqgrB.css`
- `catalogopro_build/frontend/assets/index-CVKS_Gxp.js`
- `catalogopro_build/frontend/assets/index-ChhfILCu.css`
- `catalogopro_build/frontend/assets/index-D6CeVuH_.css`
- `catalogopro_build/frontend/assets/index-DFdJtHwE.js`
- `catalogopro_build/frontend/assets/index-DbC8TMy4.css`
- `catalogopro_build/frontend/assets/index-DsV5nbIB.css`
- `catalogopro_build/frontend/assets/index-GT83DSX9.js`
- `catalogopro_build/frontend/assets/index-QQpQausp.js`
- `catalogopro_build/frontend/assets/index-WPQFXBpQ.css`
- `catalogopro_build/frontend/assets/index-b6GMqmU2.js`
- `catalogopro_build/frontend/assets/index-oRqg5hAT.js`

No se borraran ni agregaran. La politica de versionado debe definirse antes de
proponer una regla de ignore.

### Cambios funcionales no relacionados

- `accounts/admin.py`
- `accounts/services/client_importer.py`
- `admin_panel/forms/brand_forms.py`
- `admin_panel/forms/sales_document_type_forms.py`
- `admin_panel/templates/admin_panel/brands/brand_list.html`
- `admin_panel/templates/admin_panel/brands/catalog_inbox.html`
- `admin_panel/templates/admin_panel/brands/catalog_settings.html`
- `admin_panel/templates/admin_panel/clients/_module_panel.html`
- `admin_panel/templates/admin_panel/clients/categories_list.html`
- `admin_panel/templates/admin_panel/clients/dashboard.html`
- `admin_panel/templates/admin_panel/clients/form.html`
- `admin_panel/templates/admin_panel/clients/order_history.html`
- `admin_panel/templates/admin_panel/clients/task_inbox.html`
- `admin_panel/templates/admin_panel/clients/tasks.html`
- `admin_panel/templates/admin_panel/dashboard.html`
- `admin_panel/templates/admin_panel/orders/_sales_workspace.html`
- `admin_panel/templates/admin_panel/orders/sales_workspace.html`
- `admin_panel/templates/admin_panel/products/form.html`
- `admin_panel/templates/admin_panel/products/list.html`
- `admin_panel/templates/admin_panel/products/workspace.html`
- `admin_panel/templates/admin_panel/sellers/performance.html`
- `admin_panel/templates/admin_panel/settings.html`
- `admin_panel/templates/admin_panel/settings/warehouse_form.html`
- `admin_panel/templates/admin_panel/settings/warehouse_stock_initialize.html`
- `admin_panel/templates/admin_panel/settings/warehouses_list.html`
- `admin_panel/views/brands.py`
- `admin_panel/views/clients.py`
- `admin_panel/views/dashboard.py`
- `admin_panel/views/products.py`
- `admin_panel/views/settings_views.py`
- `catalog/models.py`
- `catalog/services/brand_cataloging.py`
- `catalog/services/product_timeline.py`
- `catalog/templates/catalog/catalog_v3.html`
- `catalogopro_build/frontend/index.html` (`uncertain-author`)
- `core/api_v1/serializers.py`
- `core/api_v1/views.py`
- `core/checks.py`
- `core/management/commands/bootstrap_warehouse_stock.py`
- `core/services/external_editor.py`
- `core/services/sales_documents.py`
- `core/services/sensitive_data.py`
- `core/services/warehouse_stock.py`
- `core/static/core/css/admin_ux.css`
- `core/static/core/css/catalog.css`
- `core/static/core/css/public_header.css`
- `core/static/core/css/smart_select.css`
- `core/static/core/js/main.js`
- `core/static/core/js/smart_select.js`
- `create_admins.py`
- `create_operators.py`
- `create_superuser.py`
- `deploy_catalogopro.ps1` (`uncertain-author`)
- `reset_admin_v2.py`
- `scripts/deploy_catalogopro_vps.sh` (`uncertain-author`)
- `update_staff_user.py`
- `verify_admin_views.py`

### Tests funcionales no relacionados

- `admin_panel/test_client_tasks.py`
- `admin_panel/test_product_workspace.py`
- `admin_panel/test_seller_performance.py`
- `admin_panel/test_warehouse_stock_ui.py`
- `catalog/test_product_timeline.py`
- `catalog/tests_brand_cataloging.py`
- `core/test_commercial_rules.py`
- `core/test_warehouse_stock.py`
- `flexs_project/settings/test.py`
- `flexs_project/settings/test_postgres.py`
- `test_logo_excel.py`

Los settings de test se usaran para ejecutar pruebas, pero no se asumira su
autoria para commits.

## Sensibles detectados por nombre

- `.env`: existe e ignorado; no fue leido.
- `.env.example`: plantilla modificada; no se imprimieron valores.
- `.claude/settings.local.json`: configuracion local no rastreada.
- `catalogopro_build/api/appsettings.json`: configuracion modificada de autoria
  incierta.

No se detectaron por nombre archivos `.pem`, `.key`, `.crt`, `.cer`, `.p12` o
`.pfx` dentro del estado Git inicial.

## Proteccion para las fases siguientes

- Todas las ediciones se haran con parches acotados.
- No se usaran comandos destructivos de Git o filesystem.
- No se tocara ningun archivo `MM`.
- No se modificaran `.ai/`, `.claude/`, `catalogopro_build/` ni scripts de
  deploy.
- Los PDFs, PNGs y archivos intermedios se generaran bajo `tmp/pdfs/` o
  `output/pdf/` y permaneceran fuera de commits.
- No se hara staging hasta que un diff sea atribuible, aislado y auditado.
