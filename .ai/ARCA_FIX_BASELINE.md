# Línea base previa a correcciones ARCA

Capturada: **2026-07-28 15:12:55 -03:00**  
Branch: `codex/production-fiscal-client-upgrade-20260723`  
HEAD: `a925eb4`

## Reglas de preservación

- El worktree ya estaba ampliamente modificado antes de esta implementación.
- Todos los cambios enumerados debajo se consideran preexistentes y pertenecen al usuario.
- Las correcciones ARCA se harán de manera aditiva y sin descartar, restaurar, sobrescribir en bloque ni reordenar cambios ajenos.
- Los archivos preexistentes que se solapan con ARCA (`accounts/models.py`, `core/models.py`, `core/admin.py`, `core/tests.py`, `core/api_v1/*`, `admin_panel/tests.py`, `admin_panel/views/*` y plantillas) requieren edición quirúrgica.

## Estado inicial del worktree

Salida de `git status --short` previa a cualquier cambio funcional de esta tarea:

```text
 M accounts/admin.py
 M accounts/models.py
 M admin_panel/forms/brand_forms.py
 M admin_panel/forms/sales_document_type_forms.py
MM admin_panel/templates/admin_panel/base.html
 M admin_panel/templates/admin_panel/brands/brand_list.html
 M admin_panel/templates/admin_panel/clients/_module_panel.html
 M admin_panel/templates/admin_panel/clients/categories_list.html
 M admin_panel/templates/admin_panel/clients/dashboard.html
 M admin_panel/templates/admin_panel/clients/form.html
 M admin_panel/templates/admin_panel/clients/order_history.html
 M admin_panel/templates/admin_panel/dashboard.html
 M admin_panel/templates/admin_panel/orders/_sales_workspace.html
 M admin_panel/templates/admin_panel/orders/sales_workspace.html
 M admin_panel/templates/admin_panel/products/form.html
 M admin_panel/templates/admin_panel/products/list.html
 M admin_panel/templates/admin_panel/settings.html
 M admin_panel/templates/admin_panel/settings/warehouse_form.html
 M admin_panel/templates/admin_panel/settings/warehouses_list.html
 M admin_panel/tests.py
 M admin_panel/urls.py
 M admin_panel/views/brands.py
 M admin_panel/views/clients.py
 M admin_panel/views/dashboard.py
 M admin_panel/views/helpers.py
 M admin_panel/views/orders.py
 M admin_panel/views/products.py
 M admin_panel/views/settings_views.py
 M catalog/models.py
 M catalog/templates/catalog/catalog_v3.html
 M catalogopro_build/api/appsettings.json
 D catalogopro_build/frontend/assets/index-B7XGD973.js
 D catalogopro_build/frontend/assets/index-Dfpqd_eM.css
 M catalogopro_build/frontend/index.html
 M core/admin.py
 M core/api_v1/serializers.py
 M core/api_v1/views.py
 M core/models.py
 M core/services/external_editor.py
 M core/services/sales_documents.py
 M core/static/core/css/admin_ux.css
MM core/static/core/css/base.css
 M core/static/core/css/catalog.css
 M core/static/core/js/main.js
 M core/tests.py
 M create_admins.py
 M create_operators.py
 M create_superuser.py
 M deploy_catalogopro.ps1
 M reset_admin_v2.py
 M scripts/deploy_catalogopro_vps.sh
MM templates/base.html
 M update_staff_user.py
 M verify_admin_views.py
?? .ai/
?? .claude/
?? accounts/migrations/0018_clientprofile_commercial_observation_clienttask.py
?? admin_panel/templates/admin_panel/brands/catalog_inbox.html
?? admin_panel/templates/admin_panel/brands/catalog_settings.html
?? admin_panel/templates/admin_panel/clients/task_inbox.html
?? admin_panel/templates/admin_panel/clients/tasks.html
?? admin_panel/templates/admin_panel/products/workspace.html
?? admin_panel/templates/admin_panel/sellers/
?? admin_panel/templates/admin_panel/settings/warehouse_stock_initialize.html
?? admin_panel/test_client_tasks.py
?? admin_panel/test_product_workspace.py
?? admin_panel/test_seller_performance.py
?? admin_panel/test_warehouse_stock_ui.py
?? catalog/migrations/0029_product_allow_negative_stock_product_is_purchasable_and_more.py
?? catalog/migrations/0030_brand_cataloging_workflow.py
?? catalog/services/brand_cataloging.py
?? catalog/services/product_timeline.py
?? catalog/test_product_timeline.py
?? catalog/tests_brand_cataloging.py
?? catalogopro_build/frontend/assets/index-A6kjHoC7.js
?? catalogopro_build/frontend/assets/index-B0J-Uyef.js
?? catalogopro_build/frontend/assets/index-BF9Lxq6U.css
?? catalogopro_build/frontend/assets/index-BOnr4VBX.css
?? catalogopro_build/frontend/assets/index-BziJCGV_.js
?? catalogopro_build/frontend/assets/index-C70PkTGw.js
?? catalogopro_build/frontend/assets/index-CDIBqgrB.css
?? catalogopro_build/frontend/assets/index-CVKS_Gxp.js
?? catalogopro_build/frontend/assets/index-ChhfILCu.css
?? catalogopro_build/frontend/assets/index-D6CeVuH_.css
?? catalogopro_build/frontend/assets/index-DFdJtHwE.js
?? catalogopro_build/frontend/assets/index-DbC8TMy4.css
?? catalogopro_build/frontend/assets/index-DsV5nbIB.css
?? catalogopro_build/frontend/assets/index-GT83DSX9.js
?? catalogopro_build/frontend/assets/index-QQpQausp.js
?? catalogopro_build/frontend/assets/index-WPQFXBpQ.css
?? catalogopro_build/frontend/assets/index-b6GMqmU2.js
?? catalogopro_build/frontend/assets/index-oRqg5hAT.js
?? core/management/commands/bootstrap_warehouse_stock.py
?? core/migrations/0031_sitesettings_warehouse_stock_enabled_and_more.py
?? core/migrations/0032_warehouse_stock_balance_enabled.py
?? core/services/warehouse_stock.py
?? core/static/core/css/public_header.css
?? core/static/core/css/smart_select.css
?? core/static/core/js/smart_select.js
?? core/test_warehouse_stock.py
?? docs/EXTERNAL_EDITOR_INTEGRATION.md
?? docs/arca/
```

Además, Git mostró una advertencia de permisos al intentar leer el ignore global de
`C:\Users\Brian\.config\git\ignore`; no afecta el contenido versionado observado.

## Verificaciones iniciales

### Django

Comando:

```text
python manage.py check
```

Resultado: **APROBADO** — `System check identified no issues (0 silenced)`.

### Pruebas fiscales y de seguridad focalizadas

Comando:

```text
python manage.py test core.test_commercial_rules core.test_security_hardening admin_panel.test_security_hardening.ClientFiscalReviewTests core.tests.FiscalDocumentSnapshotTests core.tests.FiscalTypeCompatibilityTests admin_panel.tests.FiscalPrintTemplateTests -v 1
```

Resultado: **APROBADO**.

- Descubiertas: 19 pruebas.
- Ejecutadas: 19.
- Fallas/errores: 0.
- Tiempo informado por Django: 22,828 s.
- Tiempo de proceso observado: 87,7 s, incluyendo creación/migración/destrucción de la DB temporal.
- Advertencias esperadas: directorio `staticfiles` ausente en test y respuestas HTTP 400/409 deliberadas en pruebas de CUIT.

## Línea base de seguridad externa

- No se contactó WSAA, WSFEv1, homologación ni producción.
- No se solicitó TA ni CAE.
- No se leyó ni mostró contenido de certificados, claves, tokens o firmas.
- No se desplegó, hizo push ni merge.

