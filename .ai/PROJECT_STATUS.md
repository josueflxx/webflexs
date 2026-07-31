# Estado del proyecto

## Continuacion funcional local - 24 de julio de 2026

Se completaron cuatro incrementos adicionales sin tocar produccion:

1. **Stock por deposito compatible y desactivado por defecto**
   - saldos fisico, reservado, disponible, minimo e ideal por producto/deposito;
   - inicializacion con vista previa, frase exacta y observacion auditada;
   - doble escritura idempotente solo cuando las banderas global y del deposito estan activas;
   - la base local conserva las banderas apagadas y no se crearon saldos automaticamente.
2. **Centro comercial del producto**
   - precio neto/final, margen con permiso, proveedores, stock por deposito y metricas;
   - timeline unificada de ventas, pedidos, stock, costos y auditoria;
   - el listado abre primero el centro comercial y mantiene la edicion separada.
3. **Seguimiento de clientes**
   - observacion comercial separada de notas internas;
   - tareas por empresa, responsable, prioridad, vencimiento y estado;
   - cierre, cancelacion y reapertura con observacion obligatoria y auditoria;
   - bandeja global con filtros de tareas propias/equipo y acceso desde el dashboard.
4. **Estadisticas de vendedores**
   - pedidos, facturas, notas de credito, neto, clientes y productos por vendedor;
   - filtros por periodo y detalle de comprobantes;
   - alcance estricto por empresa activa;
   - el informe declara que usa la asignacion actual del pedido, no una comision historica.

Migraciones aditivas aplicadas solo a SQLite local: `catalog.0029`, `core.0031`,
`core.0032` y `accounts.0018`.

Backups locales previos:

- `backups/local_pre_warehouse_stock_20260724.sqlite3`;
- `backups/local_pre_client_tasks_20260724.sqlite3`.

Controles realizados:

- `manage.py check`: sin errores;
- `makemigrations --check --dry-run`: sin diferencias;
- 58 pruebas de regresion conjunta final: OK;
- dentro de ese conjunto, 8 pruebas de tareas/bandeja y 3 de vendedores: OK;
- revision visual autenticada del dashboard, ficha de cliente y tareas en servidor local: OK;
- usuarios, clientes y tareas sinteticas de QA eliminados al terminar.

No hubo deploy, push, merge, llamadas ARCA, emisiones, cambios fiscales reales ni
modificaciones de datos en el host oficial.

## Actualizacion funcional local - 22 de julio de 2026

La fase comercial acordada esta implementada y aplicada a la base local:

- precio de catalogo neto sin IVA y alicuota seleccionable por producto;
- IVA calculado sobre el neto en Factura A/B y Nota de Credito A/B electronicas;
- control de stock opcional por producto, ejecutado solamente despues del CAE;
- reposicion idempotente por nota de credito autorizada;
- precio editable por operadores y observacion obligatoria bajo costo;
- vendedor asignable por operadores, con auditoria;
- items bloqueados cuando el comprobante ya tiene CAE;
- snapshots historicos de costo, IVA y observacion por item.

Se aplicaron `catalog.0028` y `orders.0016` a la base local. ARCA real permanece inactivo
porque certificados y puntos de venta fueron diferidos por el usuario.

**Actualizado:** 22 de julio de 2026  
**Fase:** análisis completado; Etapa 1A de guardrails implementada parcialmente y verificada.  
**Viabilidad:** **SÍ, PERO REQUIERE REESTRUCTURACIÓN**.  
**Producción/ARCA:** no tocados; ninguna factura, preflight, certificado, migración o deploy ejecutado.

## 1. Entregables

| Archivo | Estado | Contenido |
|---|---|---|
| `PROJECT_ANALYSIS.md` | Completo | arquitectura, flujos, datos, deuda, riesgos y reutilización |
| `FACTURACION_INTEGRATION_PLAN.md` | Completo | arquitectura objetivo, módulos, integración y estrategia |
| `DATA_MODEL.md` | Completo | entidades, relaciones, constraints, estados, Mermaid y migración |
| `ARCA_INTEGRATION.md` | Completo | WSAA, WSFE, padrón, CAE, recovery, QR y fuentes oficiales |
| `SECURITY_REVIEW.md` | Completo | hallazgos P0–P3, secretos, permisos y mitigaciones |
| `IMPLEMENTATION_PHASES.md` | Completo | gates, entrada/salida, pruebas y rollback por etapa |
| `QUESTIONS_FOR_USER.md` | Completo | decisiones bloqueantes/importantes/posteriores |
| `PROJECT_STATUS.md` | Completo | este registro |

## 2. Áreas y archivos revisados

### Configuración e infraestructura

- `requirements.txt`, `manage.py`;
- `flexs_project/settings/base.py`, `local.py`, `production.py` y settings auxiliares;
- `flexs_project/celery.py`, `.github/workflows/ci.yml`;
- `.env` por nombres/ubicación solamente, `.env.example`, `.gitignore`;
- `setup_vps.sh`, `deploy_update.ps1`, `deploy_catalogopro.ps1`;
- `core/services/backups.py`, `core/tasks.py`;
- inventario de `catalogopro_build/` y `appsettings*.json` sin extraer secretos.

### Modelos y migraciones

- `core/models.py`, `accounts/models.py`, `orders/models.py`, `catalog/models.py`;
- migraciones de las cuatro apps;
- constraints/índices/campos mediante introspección Django;
- estado agregado de la SQLite local sin exponer PII.

### Negocio y seguridad

- autorización, empresa activa, middleware y API v1;
- vistas de clientes, pedidos, productos, fiscal y usuarios;
- pricing, documentos comerciales, stock, ledger, pagos y auditoría;
- webhooks, importadores, backups y editor externo;
- pruebas de `accounts`, `admin_panel`, `catalog`, `core` y `orders`.

### Fiscal/ARCA

- `core/services/arca_client.py`;
- `core/services/fiscal_emission.py`;
- `core/services/fiscal_documents.py`;
- `core/services/sales_documents.py`;
- `accounts/services/account_movement_service.py`;
- `core/services/pdf_generator.py`;
- `core/services/fiscal_notifications.py`;
- vistas/templates fiscales y documentación existente.

### Fuentes externas

Sólo fuentes oficiales ARCA para WSAA, arquitectura, WSFEv1, RG 5616, constancia de inscripción y QR. Los enlaces están en `ARCA_INTEGRATION.md`.

## 3. Controles locales realizados

| Control | Resultado |
|---|---|
| `manage.py check` local | Sin errores |
| Migraciones locales pendientes | Ninguna indicada |
| Suite focalizada fiscal/permisos/features | 15 pruebas, OK |
| Guardrails nuevos (tenant, precios, secretos) | 11 pruebas, OK |
| Regresión focalizada de clientes/pagos/pedidos/workspace | 44 pruebas, OK |
| Suite Django completa | No concluyó dentro del límite de 10 minutos; resultado no afirmado |
| Certificados/claves versionados | No detectados por extensiones revisadas |
| `.env` real versionado | No; archivo local ignorado |
| Llamadas ARCA | Ninguna |

La suite focalizada generó y destruyó una base de test. Apareció un warning conocido por ausencia del directorio `staticfiles`; no afectó esas pruebas. La duración/timeout de la suite completa y la falta de pruebas específicas del cliente WSAA/WSFE quedan como deuda.

## 4. Estado agregado local

Estos conteos describen la base de desarrollo disponible, no producción:

| Entidad | Cantidad |
|---|---:|
| Empresas | 4 |
| Usuarios | 1.876 |
| Staff / superusuarios | 15 / 5 |
| Clientes | 1.856 |
| Relaciones cliente-empresa | 2.014 |
| Productos | 10.011 |
| Categorías / proveedores | 400 / 36 |
| Listas / precios de lista | 2 / 17.382 |
| Pedidos / ítems | 7 / 9 |
| Documentos fiscales / intentos | 2 / 0 |
| Movimientos de stock | 0 |
| Eventos de auditoría | 37.494 |

Calidad fiscal local:

- 364 clientes sin identificador fiscal;
- 48 grupos duplicados de identificador normalizado;
- 1.855 clientes sin domicilio fiscal;
- 5 clientes sin condición IVA;
- un documento `arca_wsfe` está sólo `ready_to_issue`; no fue emitido.

## 5. Hallazgos decisivos

1. Django/PostgreSQL y los módulos de catálogo/empresa/pedidos son reutilizables; no se recomienda reescritura.
2. `CondicionIVAReceptorId` falta en WSFE y es obligatorio desde abril de 2025.
3. No existe recovery por `FECompConsultar`; el retry actual puede reenviar un resultado incierto.
4. XML/ticket y previews de token/sign pueden terminar persistidos.
5. Ítems de pedido y edición de clientes tienen brechas de alcance horizontal en rutas legacy.
6. Precio manual no exige el permiso específico ni límites comerciales.
7. Crear un borrador fiscal puede afectar stock antes del CAE.
8. Efectos de cuenta corriente se ejecutan con excepciones silenciadas después de autorización.
9. Factura/PDF no son técnicamente inmutables.
10. Deploy no instala/supervisa Celery worker/beat, aunque emisión/recovery/backups dependen de ellos.
11. Scripts versionados contienen defaults estáticos de credenciales; deben retirarse/rotarse si se usaron.
12. El servicio CUIT actual es fallback manual, no integración ARCA.
13. No existe vendedor comercial histórico ni modelos de comisión.
14. Pagos no soportan aplicaciones N:N ni combinados normalizados.
15. Build .NET/React no tiene fuente en el repositorio y no puede auditarse/reproducirse.

## 6. Bloqueos

### Para completar Etapa 1A

No hay bloqueo para los guardrails ya incorporados. Quedan fuera de este diff la limpieza de
credenciales literales en scripts que ya tenían cambios locales del usuario, la conversión de
errores críticos silenciados y una auditoría exhaustiva de cada export/PDF. Deben resolverse en
un cambio separado para evitar mezclar o sobrescribir trabajo preexistente.

### Para clientes/ARCA/facturación

Responder los ítems BLOQUEANTE de `QUESTIONS_FOR_USER.md`, en particular:

- régimen/tipos/operaciones del emisor;
- semántica actual de precios e IVA;
- acceso directo o proveedor para consulta CUIT;
- responsable de homologación/certificados/POS;
- conciliación de duplicados;
- visibilidad entre vendedores;
- descuentos/bajo costo;
- evento de stock.

### De calidad

- hacer que la suite completa finalice de forma estable en CI/local;
- agregar pruebas WSAA/WSFE/recovery y concurrencia PostgreSQL;
- recuperar fuente/pipeline del build externo o retirarlo del camino crítico.

## 7. Cambios realizados en esta tarea

- querysets reutilizables y fail-closed para pedidos y clientes por empresa autorizada;
- scoping aplicado a listas, detalle, mutaciones, solicitudes comerciales, workspace y pagos;
- bloqueo de edición global cuando un cliente está compartido con empresas fuera del alcance;
- permiso backend `change_prices` obligatorio cuando el precio enviado difiere del calculado;
- eliminación de previews de token/sign y sanitización recursiva antes de persistir payloads ARCA;
- extensiones habituales de certificados y claves agregadas a `.gitignore`;
- 11 pruebas de seguridad nuevas y 44 regresiones focalizadas existentes aprobadas.

La afirmacion anterior de que no se modificaron modelos/migraciones quedo superada por esta
fase: se agregaron campos aditivos y se migraron localmente. No hubo llamadas a ARCA,
certificados, commit, push, merge ni deploy. Las modificaciones previas se preservaron.

## Verificacion de esta fase

- `manage.py makemigrations --check --dry-run`: sin diferencias.
- `manage.py check`: sin errores.
- 15 pruebas nuevas de reglas comerciales, formularios y seguridad: OK.
- 6 pruebas focales de IVA, stock y apertura fiscal pendiente: OK.
- Regresion ampliada: 82 casos; 80 pasaron, el caso fiscal afectado fue corregido y revalidado.
  Queda un test legacy cuyo fixture no crea la relacion cliente-empresa ahora obligatoria; recibe
  404 por el aislamiento de empresas y es ajeno a esta fase.

## 8. Próximo paso recomendado

Revisar este diff local y responder las decisiones BLOQUEANTE de `QUESTIONS_FOR_USER.md`.
Después, completar el remanente de Etapa 1A en un worktree limpio y avanzar a Etapas 1B–3.
No iniciar homologación/producción ARCA hasta definir régimen, semántica de precios e IVA,
certificados/POS de prueba y recovery por consulta.

## 9. Continuación implementada el 22 de julio de 2026

- El editor masivo de productos muestra, filtra y asigna alícuota de IVA y control opcional de
  stock, incluso sobre todos los resultados del filtro.
- La consulta de CUIT valida el dígito verificador y bloquea duplicados dentro de la empresa
  activa.
- Los duplicados de alta, edición o consulta se agrupan en una única revisión manual pendiente;
  no se crea automáticamente un segundo cliente.
- Se incorporó una bandeja local de revisiones fiscales accesible desde Clientes. Todos los
  operadores staff pueden resolver o descartar, con observación obligatoria y auditoría.
- Se aplicó localmente `accounts.0017_clientfiscalreview` y se reinició el servidor local en
  `127.0.0.1:8001`.
- Verificación final: `manage.py check` sin errores; migraciones de modelos sincronizadas;
  25 pruebas focalizadas de reglas comerciales y seguridad aprobadas; render autenticado de
  Clientes, Revisiones CUIT y Editor masivo con HTTP 200.

La obtención oficial de razón social, condición IVA y domicilio desde ARCA continúa marcada
como verificación pendiente hasta configurar el servicio de padrón, certificado y autorización
de homologación definidos en B3/B4. Mientras tanto, la UI informa el fallback manual y conserva
el CUIT sin presentarlo como dato oficial verificado.
