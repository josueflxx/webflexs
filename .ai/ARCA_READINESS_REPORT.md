# Auditoría de preparación para integración de prueba con ARCA

Fecha: **2026-07-28**  
Repositorio: `C:\Users\Brian\Desktop\webflexs`  
Revisión observada: branch `codex/production-fiscal-client-upgrade-20260723`, HEAD `a925eb4`  
Alcance: código, configuración versionada, configuración local sólo por presencia/estado, base local mediante consultas de lectura y pruebas offline. No se contactó ARCA, no se emitió, no se desplegó y no se modificó código funcional.

## Resumen ejecutivo

La aplicación sí tiene un backend Django real, modelos fiscales separados, cálculo con `Decimal`, controles de permisos del lado servidor, snapshots, intentos, sanitización y bloqueo transaccional básico. Es una base aprovechable.

No está preparada para una integración controlada completa. Hay **6 bloqueantes**: reintento ciego tras resultado incierto; correlatividad remota *fail-open*; posibilidad de etiquetar como homologación una URL productiva; ausencia de `CondicionIVAReceptorId`; credenciales/relaciones externas de homologación ausentes o no verificables; e inmutabilidad fiscal eludible desde Django admin/modelo. Además, el PDF puede afirmar “Comprobante autorizado” sin CAE ni marca de homologación.

En la base local se observaron 4 empresas (2 activas), 2 puntos de venta de homologación, 0 de producción, 2 documentos fiscales, 0 autorizados, 0 con CAE y 0 intentos. Las dos empresas activas fallan el chequeo de preparación. `ARCA_ALLOW_PRODUCTION` está desactivado; las rutas configuradas de certificado/clave no existen. Estos conteos describen sólo la DB local y no prueban el estado externo de ARCA.

**Puntaje: 52/100. Veredicto: D. BLOQUEADO.** No es seguro ejecutar WSAA en el estado actual porque falta una credencial utilizable y no están cerrados los controles de aislamiento/custodia. Una prueba WSAA aislada no emite comprobantes y podrá ser el primer contacto externo una vez cumplidas las precondiciones del plan.

## Convenciones

- Estados permitidos: **SÍ**, **PARCIAL**, **NO**, **NO APLICA**, **NO SE PUDO VERIFICAR**.
- Severidad se refiere al riesgo residual: Bloqueante, Alta, Media o Baja.
- Cuando una fila agrupa preguntas, se informa el estado de cada número.
- “Config local” nunca revela valores sensibles; sólo presencia, booleanos, conteos y existencia de rutas.
- Las líneas corresponden al worktree auditado. Abreviaturas usadas en las tablas: `base.py` = `flexs_project/settings/base.py`; `arca_client.py` = `core/services/arca_client.py`; `fiscal_emission.py` = `core/services/fiscal_emission.py`; `fiscal_documents.py` = `core/services/fiscal_documents.py`; `tasks.py` = `core/tasks.py`; `authorization.py` = `core/services/authorization.py`; `models.py` en filas fiscales = `core/models.py`; `fiscal.py` = `admin_panel/views/fiscal.py` salvo cuando se escribe `core/services/fiscal.py`; y `detail.html`, `list.html`, `print.html` = `admin_panel/templates/admin_panel/fiscal/` más el nombre indicado.

## Fuentes oficiales vigentes contrastadas

- El manual oficial WSFEv1 v4.5 enumera `FEDummy`, `FECompUltimoAutorizado`, `FECompConsultar`, parámetros y puntos de venta; contempla aprobaciones con observaciones y prescribe `FECompConsultar` tras un timeout cuyo resultado se desconoce: [manual WSFEv1 RG 4291](https://www.afip.gob.ar/fe/ayuda/documentos/wsfev1-RG-4291.pdf).
- El mismo manual incluye `CondicionIVAReceptorId` y el error excluyente 10246 por ausencia conforme RG 5616.
- WSAA requiere certificado X.509, trámite/relación previa, TRA y TA: [especificación WSAA 1.2.2](https://www.afip.gob.ar/ws/WSAA/Especificacion_Tecnica_WSAA_1.2.2.pdf).
- La especificación oficial vigente de QR usa `https://www.arca.gob.ar/fe/qr/`: [especificación QR](https://www.arca.gob.ar/fe/qr/documentos/QRespecificaciones.pdf).

## 1. Arquitectura general

| Pregunta | Estado | Evidencia y archivo:líneas | Riesgo | Cambio necesario | Sev. |
|---|---|---|---|---|---|
| 1 stack | SÍ | Django 5/DRF/Celery/Redis/PostgreSQL en `requirements.txt:1-15`; templates/JS servidor. | Dependencias sin pin exacto. | Lockfile/SCA. | Media |
| 2–4 backend, lógica sensible, plataforma | 2 SÍ; 3 SÍ; 4 SÍ | Backend Django; emisión en `core/services/fiscal_emission.py:189-405`; despliegue Gunicorn/Celery en `flexs_project/settings/base.py:408-431`. No Firebase/Node como backend fiscal. | Operación depende de OpenSSL del host. | Validar runtime reproducible. | Media |
| 5–6 capa de servicios/módulo aislado | 5 SÍ; 6 SÍ | `core/services/arca_client.py:1-834`, `fiscal_documents.py:1-499`, `fiscal_emission.py:1-405`. | Cliente duplicado/stub genera ambigüedad. | Una única interfaz/adaptador ARCA. | Alta |
| 7 entidades | PARCIAL | Clientes `accounts/models.py:91-226`; pedidos en `orders`; productos en `catalog`; documentos/pagos en `core/models.py:505-1241` y accounts. | “Factura” se usa en varias capas y faltan algunos datos fiscales. | Mapa de agregados y contratos. | Media |
| 8 duplicación/acoplamiento | SÍ | Cliente real `core/services/arca_client.py:1-834` y stub `core/integrations/arca/client.py:1-14`; PDF lee modelos mutables. | Implementación equivocada/imports divergentes. | Eliminar stub o convertirlo en interfaz única. | Alta |
| 9 transacciones/bloqueos | SÍ | `transaction.atomic`/`select_for_update` en `core/services/fiscal_emission.py:110-131,189-260`. | SQLite local no reproduce locks de PostgreSQL. | Tests de concurrencia en PostgreSQL. | Alta |
| 10 anti número duplicado | PARCIAL | Unique DB `core/models.py:808-979` y lock de serie `fiscal_emission.py:110-131`; recuperación insegura `core/tasks.py:111-174`. | Duplicación lógica/CAE perdido tras timeout. | BLK-01/02. | Bloqueante |

## 2. Backend obligatorio

| Pregunta | Estado | Evidencia | Riesgo | Cambio | Sev. |
|---|---|---|---|---|---|
| 1–4 backend exclusivo/ausencia de secretos en frontend | 1 SÍ; 2 NO; 3 SÍ; 4 NO | ARCA sólo se importa en servicios backend; credenciales por configuración servidor `arca_client.py:187-301`; JS de CUIT sólo llama endpoint `clients/form.html:740-788`. | No hay secreto fiscal en bundle observado. | Mantener boundary y prueba de escaneo. | Baja |
| 5–6 endpoint autenticado/permisos | 5 SÍ; 6 SÍ | POST staff, compañía y capability en `admin_panel/views/fiscal.py:610-653`; permisos `core/services/authorization.py:11-103`. | Capability demasiado amplia para notas/reintentos. | Permisos fiscales granulares. | Alta |
| 7 recálculo backend | SÍ | `fiscal_documents.py:130-228,380-499`. | Regla/tolerancia y mapeo tributario incompletos. | Validación fiscal exacta. | Alta |
| 8–9 SOAP/CMS/HTTPS | 8 SÍ; 9 PARCIAL | SOAP manual `arca_client.py:303-343,557-623`; CMS vía OpenSSL `256-301`. | Sin WSDL/contrato, passphrase ni prueba del runtime. | Cliente contractual y preflight OpenSSL. | Alta |
| 10 límites de tiempo | PARCIAL | Timeout configurable `base.py:280-291`; urllib `arca_client.py:303-343`; Celery. | No se verificaron límites del hosting/proxy y el timeout dispara reintento inseguro. | Presupuestos y protocolo incierto. | Bloqueante |

## 3. Separación entre entornos

| Pregunta | Estado | Evidencia | Riesgo | Cambio | Sev. |
|---|---|---|---|---|---|
| 1–3 local/homo/prod y URLs | 1 PARCIAL; 2 PARCIAL; 3 SÍ | Settings local/prod `flexs_project/settings/local.py:13-28`, `production.py:10-41`; POS con ambiente `core/models.py:443-481`; URLs `base.py:259-277`. | Homologación no es un settings/DB aislado propio. | Perfil de test y DB separados. | Alta |
| 4 URL prod hard-coded | SÍ | Defaults de homologación y producción en `base.py:259-274`. | Host prod está en código/config y puede asignarse al slot homo. | Allowlist y binding host-ambiente. | Bloqueante |
| 5 interruptor | SÍ | Ambiente en POS y `ARCA_ALLOW_PRODUCTION=false`, `base.py:254-277`; guard `arca_client.py:187-203`. | No valida hostname. | BLK-03. | Bloqueante |
| 6 fail-fast | PARCIAL | Producción valida secret/Redis `production.py:10-30`; credenciales se validan al uso `arca_client.py:215-237`. | App inicia sin configuración fiscal completa. | System check/preflight explícito por modo. | Alta |
| 7/12 imposible prod/protección adicional | 7 NO; 12 PARCIAL | Guard por etiqueta de ambiente, sin allowlist `arca_client.py:187-203`. | Acceso accidental a producción. | Deny-by-default de hosts productivos. | Bloqueante |
| 8 Firebase separado | NO APLICA | No hay Firebase en la arquitectura revisada. | Ninguno. | Ninguno. | Baja |
| 9 DB prueba separada | PARCIAL | Tests usan DB temporal; local usa SQLite `local.py:13-28`. | No hay perfil homolog dedicado ni prueba de aislamiento operativo. | DB/tenant exclusivo y datos sintéticos. | Alta |
| 10 identificación de prueba | PARCIAL | POS guarda ambiente `core/models.py:443-481`; documento no congela ambiente; PDF no marca homo. | Prueba confundible con oficial. | Ambiente inmutable en doc + watermark. | Alta |
| 11 prueba puede modificar reales | PARCIAL | Emisión afecta documento/stock al CAE `core/test_commercial_rules.py:114-...`; scoping por empresa. | Si se usa DB real, homologación puede afectar stock/ventas. | DB sintética y side-effects deshabilitados. | Alta |

## 4. Certificados y secretos

| Pregunta | Estado | Evidencia | Riesgo | Cambio | Sev. |
|---|---|---|---|---|---|
| 1 estrategia de carga | PARCIAL | JSON/env con CUIT/rutas `base.py:277`; lectura `arca_client.py:215-237`. Sin passphrase. | Rutas locales inexistentes; P12/passphrase no soportado. | Gestor/mount + formatos definidos. | Bloqueante |
| 2 gestor seguro | NO | Sólo env/rutas; no integración de secret manager. | Copias manuales y permisos débiles. | Vault/secret store del hosting. | Alta |
| 3 env server-side | SÍ | Config Django, no bundle; `.env` ignorado. | Un `.env` sigue siendo archivo suelto. | Usar sólo desarrollo local. | Media |
| 4 archivos versionados | NO | Escaneo workspace/historial por nombres no encontró cert/key; `.gitignore:11-27`. | El escaneo histórico no sustituye gitleaks. | CI secret scanning. | Media |
| 5 gitignore | SÍ | `.gitignore:11-27` cubre key/pem/p12/pfx/crt/cer/certs/.env. | Patrones no evitan `git add -f`. | Hook/CI. | Baja |
| 6 logs sensibles | PARCIAL | Redacción `core/services/sensitive_data.py:13-105`; Sentry sin PII. `core/tasks.py:60-108` conserva excepciones genéricas. | Vías no sanitizadas podrían filtrar texto. | Sanitizar en boundary único y tests. | Alta |
| 7–10 rotación/vencimiento/metadatos/intercambio | 7 NO; 8 NO; 9 NO; 10 NO | Sólo existencia de archivos `arca_client.py:215-237`; no runbook ni parse X.509. | Expiración o distribución insegura. | Lifecycle completo, huella/expiry no sensible. | Alta |
| 11 aislamiento homo/prod | PARCIAL | Config por ambiente `base.py:254-318`. | Mismo mecanismo, sin store/política separada. | Identidades y stores separados. | Alta |
| 12 secretos en commits pasados | NO SE PUDO VERIFICAR | No se hallaron nombres de cert/.env en Git; no hay escáner dedicado instalado. | Posible secreto histórico no detectado. | Gitleaks/trufflehog sobre todo el historial. | Alta |

## 5. Autenticación con WSAA

| Pregunta | Estado | Evidencia | Riesgo | Cambio | Sev. |
|---|---|---|---|---|---|
| 1 implementación | SÍ | `core/services/arca_client.py:239-437`. | Sin tests. | Suite contractual. | Alta |
| 2–3 TRA/IDs/fechas | 2 PARCIAL; 3 PARCIAL | UTC -5/+10 min e ID por segundos `239-254`. | Colisión concurrente; no se validó contra WSAA. | UUID/contador seguro y tests. | Alta |
| 4 reloj servidor | NO SE PUDO VERIFICAR | No hay chequeo NTP/monitor. | Rechazo WSAA. | NTP y alerta de drift. | Alta |
| 5 firma CMS | PARCIAL | OpenSSL subprocess `256-301`. | No passphrase/compatibilidad probada. | Cert efímero + fixtures y control permisos. | Alta |
| 6 servicio | SÍ | `service=wsfe` en TRA `239-254`. | Asociación externa no verificable. | Confirmar en ARCA. | Bloqueante |
| 7 envío homologación | PARCIAL | URL configurable y SOAP `303-343`; no ejecutado. | Host no ligado al ambiente. | BLK-03/05. | Bloqueante |
| 8 parse XML | PARCIAL | ElementTree `345-437`. | Sólo fixtures implícitos; error puede incluir XML antes de redacción. | Tests de faults/malformed. | Alta |
| 9 extracción | PARCIAL | Extrae token/sign/expiration, no `generationTime`, `345-437`. | Validación temporal incompleta. | Extraer/validar ambos tiempos. | Media |
| 10 backend-only | SÍ | Cache servidor; request persistido sanitiza Auth `754-789`. | Cache LocMem posible. | Cache compartido protegido. | Media |
| 11–13 caché/reuso/renovación | 11 SÍ; 12 SÍ; 13 SÍ | TTL/margen `345-437`; cache config `base.py:376-393`. | No lock. | Singleflight distribuido. | Alta |
| 14 concurrencia renovación | NO | No lock en `345-437`. | Tickets repetidos/IDs colisionados. | Lock + uniqueId. | Alta |
| 15 errores | PARCIAL | Clases temporales/config `arca_client.py:24-53,303-437`. | HTTP 4xx/5xx se tratan temporalmente igual. | Taxonomía y códigos. | Alta |
| 16 logging seguro | PARCIAL | Sanitizador `sensitive_data.py:13-105`. | Cobertura no total. | Sanitización obligatoria y scan logs. | Alta |
| 17 tests TRA | NO | Tests no invocan `ArcaWsfeClient`. | Regresión silenciosa. | Tests unitarios. | Alta |
| 18 prueba WSAA sin emitir | PARCIAL | `_login` está separado, pero es privado; preflight también consulta WSFE `arca_client.py:811-834`. | Operador podría usar flujo incorrecto. | Comando WSAA-only sin import de emisión. | Alta |

## 6. Conexión con WSFEv1

| Pregunta | Estado | Evidencia | Riesgo | Cambio | Sev. |
|---|---|---|---|---|---|
| 1 decisión WSFEv1 | SÍ | `docs/arca/ARCA_PROJECT_AUDIT.md:1-356`; cliente usa namespace FEV1. | Documentación no es test. | ADR/versionado de contrato. | Media |
| 2–3 librería/WSDL | 2 PARCIAL; 3 NO | SOAP XML manual con urllib `arca_client.py:303-343,557-623`; no consume WSDL. | Drift de esquema. | Generación/validación contractual. | Alta |
| 4–5 URLs/timeout | 4 SÍ; 5 SÍ | `base.py:259-291`. | Centralizado pero host no validado. | Allowlist. | Bloqueante |
| 6 taxonomía respuestas | PARCIAL | `arca_client.py:676-752` mezcla Errors/Obs; no Events/A+obs. | Estado/mensaje equivocado. | Separar Fault/Errors/Obs/Events/result. | Alta |
| 7 estado servicio | NO | No `FEDummy`; preflight autentica y consulta números `811-834`. | “ok” puede ser verdadero con consultas fallidas. | Implementar FEDummy y fail-closed. | Alta |
| 8 último autorizado | SÍ | `625-675`. | Sync opcional/fail-open. | BLK-02. | Bloqueante |
| 9 comprobante existente | NO | No `FECompConsultar`. | Reintento duplicado/CAE perdido. | BLK-01. | Bloqueante |
| 10 parámetros | NO | Sin métodos paramétricos. | Mapeos obsoletos. | Métodos oficiales/caché con vigencia. | Alta |
| 11 tablas manuales | SÍ | IVA map fijo `arca_client.py:116-123`. | Omisión/fallback 21 %. | Rechazar desconocido y sincronizar. | Alta |
| 12 estrategia actualización | NO | No job/versionado paramétrico. | Drift fiscal. | Sync supervisado. | Alta |
| 13 CUIT/ticket | PARCIAL | Mismo config CUIT en Auth `754-789`; no compara contenido del TA. | Representación incorrecta no detectada localmente. | Validar identidad/asociación mediante consulta. | Alta |
| 14 request/response | PARCIAL | Campos en `FiscalDocument`/attempts `core/models.py:808-1113`, sanitización `fiscal_emission.py:315-320`. | Se sobrescribe resumen y no es append-only. | Intentos inmutables con hash/correlation. | Alta |

## 7. Configuración fiscal de la empresa

| Pregunta | Estado | Evidencia | Riesgo | Cambio | Sev. |
|---|---|---|---|---|---|
| 1–4 CUIT/razón/IVA/domicilio | SÍ | `core/models.py:164-220`. | CUIT sólo longitud en algunos flujos. | Checksum y normalización centrales. | Alta |
| 5 inicio actividades | NO | No campo en `Company`. | Documento incompleto. | Modelar y validar. | Alta |
| 6–7 punto/empresa/ambiente | SÍ | `FiscalPointOfSale`, `core/models.py:443-481`. | Ambiente mutable y no congelado. | Congelar en comprobante. | Alta |
| 8 tipos habilitados | SÍ | `SalesDocumentType`, `548-717`. | Mapeo no contrastado con ARCA. | Reconciliar con parámetros. | Alta |
| 9 matriz A/B/C/M/NC/ND | PARCIAL | Tipos incluyen A/B/C y NC/ND `core/models.py:16-35`; emisión restringida a FA/FB/NCA/NCB `fiscal_emission.py:58-68`. | Config muestra más que cliente soporta. | Capability explícita por emisor/ARCA. | Alta |
| 10–11 protección/lectura vendedores | 10 SÍ; 11 PARCIAL | Config/preflight sólo superadmin `admin_panel/views/fiscal.py:1138-1514`; vistas fiscales staff. | Django admin puede eludir restricciones. | Permisos de modelo granulares. | Alta |
| 12 readiness | PARCIAL | `core/services/fiscal.py:190-309`. | Omite inicio, checksum, condición oficial y endpoint host. | Checklist fail-closed completo. | Bloqueante |
| 13 decisiones faltantes | SÍ | `docs/arca/ARCA_OPEN_QUESTIONS.md:7-...`; `.ai/QUESTIONS_FOR_USER.md`. | Datos inventados causarían rechazo. | Resolver preguntas sin compartir secretos. | Bloqueante |

## 8. Clientes y consulta por CUIT

| Pregunta | Estado | Evidencia | Riesgo | Cambio | Sev. |
|---|---|---|---|---|---|
| 1 entidad | SÍ | `accounts/models.py:91-226`. | Campos fiscales parciales. | Extender esquema. | Media |
| 2–4 normalización/checksum/duplicados | 2 NO; 3 PARCIAL; 4 PARCIAL | Validador existe `accounts/services/fiscal_review.py:9-50`, pero alta/edición guardan raw `clients.py:1304-1458,1650-1766`; sin unique DB. | CUIT inválido/duplicado por rutas alternas. | Regla central + constraint. | Alta |
| 5 sin CUIT | SÍ | Documento/tipo opcional en perfil; readiness decide según caso. | Reglas consumidor final incompletas. | Matriz fiscal definida. | Alta |
| 6 campos separados | PARCIAL | Razón, doc, IVA, domicilio/localidad/provincia/CP/teléfono en perfil; email en User. Falta nombre comercial separado. | Ambigüedad identidad/contacto. | Snapshot/campos definidos. | Media |
| 7–9 lookup desacoplado/falla/corrección | 7 SÍ; 8 SÍ; 9 SÍ | Endpoint fallback/manual `clients.py:2315-2400`; formulario editable. | Datos no son oficiales y pueden quedar sin verificar. | Proveedor/SLA/provenance. | Alta |
| 10 evidencia usada | SÍ | Snapshot `fiscal_documents.py:231-353`. | Emisión luego lee perfil vivo para SOAP. | Payload sólo desde snapshot. | Alta |
| 11–12 histórico/inmutable | 11 PARCIAL; 12 PARCIAL | Snapshot se conserva, pero PDF/ARCA leen mutable `arca_client.py:465-555`; `pdf_generator.py:16-83`. | Histórico/regeneración cambian. | Snapshot autorizado como única fuente. | Alta |
| 13 condición IVA/tipo | NO | No ID oficial ni matriz `CondicionIVAReceptorId`. | Rechazo o comprobante incorrecto. | BLK-04. | Bloqueante |
| 14 secretos lookup frontend | SÍ | JS sólo consume endpoint staff `clients/form.html:740-788`. | No se observó token. | Mantener proxy backend. | Baja |

## 9. Cotizaciones frente a facturas oficiales

| Pregunta | Estado | Evidencia | Riesgo | Cambio | Sev. |
|---|---|---|---|---|---|
| 1 distinción | SÍ | Behaviors/modes `core/models.py:548-717`; `InternalDocument` y `FiscalDocument` separados `808-1241`. | Terminología UI variable. | Estados/nombres uniformes. | Media |
| 2–5 seguridad cotización | 2 SÍ; 3 SÍ; 4 SÍ; 5 SÍ | ARCA sólo desde emisión fiscal; cotización dice “Documento informativo. No fiscal” `admin_panel/views/orders.py:1013`. | Bajo. | Tests de plantilla. | Baja |
| 6–8 conversión/nuevo registro/referencia | 6 SÍ; 7 SÍ; 8 SÍ | Creación fiscal por source/order `fiscal_documents.py:57-76,380-499`; FK order `core/models.py:808-979`. | Un único source key puede limitar correcciones válidas. | Definir ciclo documental. | Media |
| 9 cotización y serie | SÍ | Serie sólo se reserva en emisión `fiscal_emission.py:110-260`. | Bajo. | Mantener. | Baja |
| 10 recálculo | SÍ | `fiscal_documents.py:130-228`. | Tolerancia ARS 2. | Política exacta. | Alta |
| 11–12 edición post-CAE | 11 SÍ; 12 PARCIAL | Campos/modelos editables; vista custom bloquea algunas acciones `fiscal.py:305-323`; admin no. | Alteración/borrado autorizado. | BLK-06. | Bloqueante |
| 13 rechazo vs autorizado | SÍ | Estados controlados `core/models.py:53-77`. | Observaciones/incertidumbre faltan. | Ampliar estados. | Alta |
| 14 mensaje antes de aprobación | PARCIAL | UI puede crear/llamar “factura” al registro local; print afirma autorizado sin CAE `print.html:509-521`. | Engaño operativo. | Mensajes y PDF por estado. | Alta |

## 10. Cálculos fiscales

| Pregunta | Estado | Evidencia | Riesgo | Cambio | Sev. |
|---|---|---|---|---|---|
| 1–3 backend/Decimal/no JS fiscal | 1 SÍ; 2 SÍ; 3 SÍ | `fiscal_documents.py:130-228`; modelos Decimal. | Conversión posterior a float en PDF/SOAP format. | Mantener Decimal hasta serialización. | Media |
| 4 redondeo | NO | `Decimal.quantize` sin política documentada `130-152`. | Diferencias centavos. | Decisión contable + tests. | Alta |
| 5–6 reconciliación de totales | PARCIAL | Recalcula líneas/total `155-228`; payload fuerza exento/no gravado/tributos 0 `arca_client.py:465-555`. | Identidad fiscal incorrecta. | Validador de ecuación completa. | Alta |
| 7 múltiples IVA | PARCIAL | Agrupa alícuotas `arca_client.py:439-501`; sólo test 21 %. | Alícuota desconocida omitida/fallback. | Casos mixtos y rechazo. | Alta |
| 8 precios con/sin IVA | SÍ | Decisión documentada sin IVA `docs/arca/ARCA_OPEN_QUESTIONS.md:7-20`; modos de cálculo. | Puede divergir de datos importados. | Validar origen de precios. | Media |
| 9 doble IVA | PARCIAL | Base/gross diferenciados `fiscal_documents.py:130-210`. | Cobertura insuficiente. | Invariantes/tests. | Alta |
| 10 descuentos | PARCIAL | Snapshots de descuento en pedido/migración; cálculo del total. | Orden de redondeo no documentado. | Política por línea. | Alta |
| 11 recalcula envío | SÍ | Creación fiscal backend; readiness antes de emitir. | Snapshot y perfil pueden divergir al emitir. | Congelar/validar hash. | Alta |
| 12 matriz tests | PARCIAL | `core/test_commercial_rules.py:34-112` sólo 21 %, faltante y tipo; faltan 10,5/mixto/descuento/decimal/redondeo/cero/negativo. | Regresiones tributarias. | Matriz completa. | Alta |
| 13 frontend=backend | NO SE PUDO VERIFICAR | No test contractual de igualdad UI/backend. | Diferencia visual/emitida. | API retorna breakdown autoritativo. | Media |
| 14 manipulación navegador | SÍ | Importes se reconstruyen desde pedido/producto `fiscal_documents.py:155-228,380-499`. | Tolerancia amplia. | Rechazo estricto/hash. | Alta |

## 11. Numeración y correlatividad

| Pregunta | Estado | Evidencia | Riesgo | Cambio | Sev. |
|---|---|---|---|---|---|
| 1 clave de serie | PARCIAL | Serie por POS+tipo `core/models.py:737-805`; POS pertenece a empresa, no congela CUIT/ambiente. | Cambio de CUIT/ambiente altera significado. | Clave explícita `(CUIT,env,POS,tipo)`. | Alta |
| 2 consulta previa | PARCIAL | Política `first`, opcional `fiscal_emission.py:78-107`; settings `base.py:280-291`. | Serie desincronizada. | BLK-02. | Bloqueante |
| 3–4 siguiente server/no frontend | 3 SÍ; 4 NO | Reserva backend `110-131`; vista no acepta número `fiscal.py:610-653`. | Bajo salvo sync. | Mantener boundary. | Baja |
| 5–6 lock/dos vendedores | 5 PARCIAL; 6 PARCIAL | `select_for_update`, unique DB `models.py:737-979`. PostgreSQL sí; SQLite no simula. | Carreras no testeadas. | Tests PostgreSQL/worker. | Alta |
| 7–9 timeout/consulta/no retry ciego | 7 NO; 8 NO; 9 NO | `pending_retry` y auto-reenvío `fiscal_emission.py:271-314`; `core/tasks.py:111-174`; no `FECompConsultar`. | Duplicación lógica/CAE perdido. | BLK-01. | Bloqueante |
| 10 idempotencia | SÍ | `source_key` unique `fiscal_documents.py:57-76`; modelo unique. | No cubre incertidumbre remota. | Vincular key/hash a intentos. | Alta |
| 11 retomar | NO | Retoma reenviando, no reconciliando. | Doble solicitud. | State machine de recuperación. | Bloqueante |
| 12 resincronización | PARCIAL | Puede consultar último, pero fail-open `fiscal_emission.py:78-107`. | Pérdida correlatividad. | Flujo administrativo auditado. | Bloqueante |
| 13 interno vs fiscal | SÍ | `DocumentSeries`/`InternalDocument` vs `FiscalDocumentSeries`/`FiscalDocument` `models.py:505-1241`. | Bajo. | Mantener. | Baja |
| 14 inmutabilidad número | PARCIAL | Vista protege; admin/modelo no `core/admin.py:48-103`. | Alteración. | BLK-06. | Bloqueante |
| 15 auditoría intentos | PARCIAL | `FiscalEmissionAttempt` `models.py:1071-1113`. | Editable/cascade/sin unique attempt. | Append-only + constraint. | Alta |

## 12. Estados de una factura

| Pregunta | Estado | Evidencia | Riesgo | Cambio | Sev. |
|---|---|---|---|---|---|
| 1 controlados | SÍ | Choices `core/models.py:53-77`. | No es máquina formal. | Transiciones explícitas. | Media |
| 2 cobertura mínima | PARCIAL | Hay ready/submitting/authorized/pending_retry/rejected/voided/external; faltan draft, A+obs, uncertain, replaced. | Estados ambiguos. | Ampliar. | Alta |
| 3 backend | SÍ | Cambios en servicios/tasks. | Admin puede editar. | Readonly/admin protection. | Alta |
| 4 datos autorizado | PARCIAL | CAE/vto/número/POS/tipo/issued_at `models.py:808-979`. | `issued_at` no necesariamente fecha ARCA; ambiente no congelado. | Campos de proceso/autorización. | Alta |
| 5 códigos rechazo | PARCIAL | `response_payload/error_code/error_message`; parse mezcla Obs `arca_client.py:676-752`. | Pérdida semántica. | Estructura separada. | Alta |
| 6 técnico vs fiscal | PARCIAL | Excepciones temporales vs result rejected. | Todo inesperado va a retry. | Taxonomía. | Alta |
| 7 incierto | NO | Timeout → `pending_retry`. | Reenvío inseguro. | BLK-01. | Bloqueante |
| 8 retry seguro | NO | Rejected y pending_retry son retryables `fiscal_emission.py:58-68`. | Repetición indebida. | Policy por causa+consulta. | Bloqueante |
| 9 revisión admin | PARCIAL | Detalle/intentos y admin visibles `core/admin.py:48-103`. | Sin herramienta de reconciliación. | Consola segura. | Alta |
| 10 no borrar | PARCIAL | Custom guard `admin_panel/views/fiscal.py:305-323`; Django admin/cascade eluden. | Pérdida legal. | BLK-06. | Bloqueante |
| 11 actor | PARCIAL | `triggered_by`, snapshot actor, audit log `models.py:1071-1113,1509-1542`. | Reintento automático actor nulo, sin correlation. | Actor de sistema/correlation. | Media |

## 13. Notas de crédito y correcciones

| Pregunta | Estado | Evidencia | Riesgo | Cambio | Sev. |
|---|---|---|---|---|---|
| 1 inmutabilidad conceptual | SÍ | Servicio impide void autorizado y exige NC `fiscal_documents.py:649-745`. | No se aplica en modelo/admin. | BLK-06. | Bloqueante |
| 2–4 entidad/relación/datos asociados | 2 SÍ; 3 SÍ; 4 SÍ | Tipos NC y `related_document`; validación `core/services/fiscal.py:312-371`; payload asociados `arca_client.py:503-519`. | Cobertura A/B limitada. | Tests/contrato. | Alta |
| 5 no borrar autorizado | PARCIAL | Guard custom, no DB/admin. | Borrado/cascade. | BLK-06. | Bloqueante |
| 6 UX distingue | PARCIAL | Flujos de void/reopen/NC existen. | Terminología y permisos no completos. | Acciones explícitas por estado. | Media |
| 7 permiso NC | NO | Misma capability general `issue_documents`. | Vendedor puede corregir sin aprobación separada. | Permiso/aprobación dedicado. | Alta |
| 8 motivo auditado | PARCIAL | Campos de void/relación y actor; no motivo obligatorio integral. | Corrección sin justificación. | Motivo requerido e inmutable. | Alta |

## 14. Roles y permisos

| Pregunta | Estado | Evidencia | Riesgo | Cambio | Sev. |
|---|---|---|---|---|---|
| 1–3 backend/admin/vendedor | 1 SÍ; 2 SÍ; 3 SÍ | Roles/capabilities `authorization.py:47-103`. | Hay roles adicionales y superuser bypass. | Matriz fiscal formal. | Media |
| 4 capacidades vendedor | PARCIAL | Ventas puede clientes/cotizaciones pero no emitir; facturación sí; vistas staff. | “Vendedor” requerido no coincide exactamente con rol. | Confirmar mapping de negocio. | Alta |
| 5 visibilidad ajena | SÍ | Decidido “todos ven ventas” `docs/arca/ARCA_OPEN_QUESTIONS.md:7-20`; lista por empresa `fiscal.py:326-405`. | Exposición fiscal más amplia no decidida. | Separar resumen/detalle/auditoría. | Media |
| 6–8 cambiar POS/CUIT/condición | 6 NO; 7 NO; 8 NO | Config fiscal sólo primary superadmin `fiscal.py:1138-1514`. | Django admin con permisos podría hacerlo. | Restringir modelos/admin. | Alta |
| 9 borrar facturas | PARCIAL | Custom permiso/guard; admin/modelo alterno. | Borrado. | BLK-06. | Bloqueante |
| 10 retry incierto | NO | No estado/permiso; retry automático. | Nadie puede gobernar correctamente. | Reconciliación admin-only. | Bloqueante |
| 11 operaciones críticas | PARCIAL | Capability de emisión y superadmin config. | No separa NC/retry/reconcile. | Capabilities granulares + 4-eyes inicial. | Alta |
| 12 actores | PARCIAL | order assigned_to, attempt triggered_by, audit log. | Automatismos sin identidad/correlation. | Actor sistema y cadena completa. | Media |
| 13 admin revisa todo | SÍ | Lista/detalle por empresa y superadmin. | Admin Django puede modificar. | Read-only fiscal. | Alta |
| 14 Firebase Rules | NO APLICA | No Firebase. | Ninguno. | Ninguno. | Baja |
| 15 bypass endpoint | PARCIAL | Endpoints revisados validan; llamadas directas a servicios/model/admin no aplican siempre capability/inmutabilidad. | Bypass interno/admin. | Enforcement en dominio/modelo. | Alta |

## 15. Seguridad

| Pregunta | Estado | Evidencia | Riesgo | Cambio | Sev. |
|---|---|---|---|---|---|
| 1–2 auth/sesión | SÍ | `staff_member_required`, CSRF POST, capability en `fiscal.py:610-653`; settings auth `base.py:320-342`. | Revisar todas las rutas nuevas. | Tests de autorización exhaustivos. | Media |
| 3–4 abuso/rate limit | 3 PARCIAL; 4 NO | Estado/locks reducen duplicados; no throttle fiscal. | Queue flood/doble click. | Throttle/idempotency/circuit breaker. | Alta |
| 5 esquemas | PARCIAL | Validación manual/servicios, no schema SOAP/API central. | Inputs inconsistentes. | DTO/schema tipado. | Alta |
| 6 no confiar cliente | SÍ | Recalcula backend. | Datos maestros mutables. | Snapshot/hash. | Alta |
| 7 redacción logs | PARCIAL | `sensitive_data.py:13-105`; logging `base.py:443-464`. | Excepciones/vías no cubiertas. | Redaction filter central. | Alta |
| 8 acceso logs | NO SE PUDO VERIFICAR | No política/ACL de infraestructura en repo. | Acceso a PII/diagnóstico. | RBAC/retención/auditoría externa. | Alta |
| 9 acceso DB fiscal | PARCIAL | App scoping por empresa; DB credential única. | Superuser/admin amplio. | Least privilege y cifrado. | Alta |
| 10 eventos seguridad | PARCIAL | `AdminAuditLog` `models.py:1509-1542`, Sentry. | No eventos de certificado/ARCA específicos. | Audit security stream. | Media |
| 11 duplicados | PARCIAL | source key/locks; timeout inseguro. | BLK-01. | Reconciliación. | Bloqueante |
| 12 origen solicitudes | SÍ | CSRF/session; POST. | No allowlist/rate per fiscal endpoint. | Defense-in-depth. | Media |
| 13 errores navegador | PARCIAL | `DEBUG=False` prod `production.py:10-30`; servicios devuelven mensajes sanitizados en parte. | Excepción cruda posible. | Error IDs y sanitización. | Alta |
| 14 vulnerabilidades | NO SE PUDO VERIFICAR | `pip check` pasa; `pip-audit` ausente; `requirements.txt:1-15` usa mínimos. | CVEs desconocidas. | SCA + pins. | Alta |
| 15 actualización deps | NO | Sin Dependabot/Renovate/política/lock. | Drift y supply chain. | Política y CI. | Alta |
| 16 revocar certificado | NO | Sin runbook/kill switch por huella. | Credencial comprometida activa. | Revocación externa + disable local inmediato. | Alta |

## 16. Persistencia y auditoría

| Pregunta | Estado | Evidencia | Riesgo | Cambio | Sev. |
|---|---|---|---|---|---|
| 1–2 tabla fiscal/separación pedido | SÍ | `FiscalDocument` separado con FK order `models.py:808-979`. | Bajo. | Mantener. | Baja |
| 3 copia inmutable | PARCIAL | Snapshot/items `fiscal_documents.py:231-353`; editables/cascade. | Alteración/pérdida. | BLK-06. | Bloqueante |
| 4–5 request/response | PARCIAL | JSON en documento/intentos `models.py:808-1113`. | Resumen sobrescribible; raw sanitizado no versionado. | Append-only/hash/schema version. | Alta |
| 6 códigos/obs | PARCIAL | error fields/response; parser mezcla semántica. | Diagnóstico incompleto. | Estructuras separadas. | Alta |
| 7 intentos separados | SÍ | `FiscalEmissionAttempt` `1071-1113`. | Editable/cascade/sin unique. | Inmutabilidad/constraint. | Alta |
| 8 dimensiones auditoría | PARCIAL | Usuario/fecha/result, order seller; ambiente sólo por POS mutable; sin correlation/operation formal. | Cadena incompleta. | Event log. | Alta |
| 9 trazabilidad pedido-factura | SÍ | FK order/client/company/source key. | Cambios/cascadas. | PROTECT. | Alta |
| 10 minimización sensible | PARCIAL | Sanitizador elimina Auth/XML. | PII completa en snapshots y acceso amplio. | Clasificación/retención/RBAC. | Alta |
| 11 catálogo histórico | SÍ | Items snapshot `models.py:1026-1065`. | Cascade desde doc. | Proteger. | Alta |
| 12 cliente histórico | PARCIAL | Snapshot sí; emisión/PDF vivo. | Divergencia. | Fuente snapshot. | Alta |
| 13 backup | NO SE PUDO VERIFICAR | Config genérica `base.py:433-435`; servicios de backup, sin evidencia de ejecución/restore fiscal. | Pérdida. | Restore drill. | Alta |
| 14 conservación | NO | Sin política fiscal verificable. | Incumplimiento/pérdida. | Política asesorada. | Alta |
| 15 cascade | SÍ | Items/intentos `on_delete=CASCADE`, `models.py:1026-1113`; doc eliminable por admin. | Pérdida completa. | PROTECT/inmutabilidad. | Bloqueante |

## 17. Documento de factura

| Pregunta | Estado | Evidencia | Riesgo | Cambio | Sev. |
|---|---|---|---|---|---|
| 1 generador | SÍ | Print HTML y PDF `admin_panel/views/fiscal.py:902-1102`; `pdf_generator.py:16-83`. | Dos caminos pueden divergir. | Fuente/render único. | Alta |
| 2 fuente autorizada | PARCIAL | Vista usa parte de snapshot, pero empresa/cliente/order/settings mutables. | Histórico cambia. | Snapshot autorizado. | Alta |
| 3–5 letra/POS/número/CAE | SÍ | `print.html:398-547`. | Se muestra estructura oficial aun sin autorización. | Condicionar por estado. | Alta |
| 6–7 QR previsto/fuente | 6 SÍ; 7 PARCIAL | QR sólo con CAE `pdf_generator.py:16-83`, pero datos mutables y URL AFIP histórica. | QR inconsistente. | Snapshot + URL oficial ARCA. | Alta |
| 8 contenido | PARCIAL | Emisor/receptor/items/totales presentes. | Fecha inicio/IIBB y breakdown pueden faltar. | Checklist legal. | Alta |
| 9 regeneración estable | NO | Lee modelos vivos y float. | Documento histórico distinto. | Hash/version/snapshot. | Alta |
| 10 PDF no autorizado oficial | NO | Vista no exige authorized y pie siempre “autorizado” `print.html:509-521`. | Documento engañoso. | ALT-01. | Alta |
| 11 cotización distinta | SÍ | Texto no fiscal en cotización `orders.py:1013`. | Bajo. | Mantener tests. | Baja |
| 12 correo/descarga | SÍ | Campos de email y vistas de descarga en flujo fiscal. | Entrega/consentimiento no definidos. | Política. | Media |
| 13 vendedores editan PDF post-CAE | PARCIAL | No editan archivo directo, pero fuentes/modelos pueden cambiar vía permisos/admin. | Regeneración alterada. | Inmutabilidad. | Alta |

## 18. Manejo de errores y recuperación

| Pregunta | Estado | Evidencia | Riesgo | Cambio | Sev. |
|---|---|---|---|---|---|
| 1 centralizado | PARCIAL | Clases en `arca_client.py:24-53`; lógica en emission/tasks. | Reglas repartidas. | Orquestador/state machine. | Alta |
| 2 recuperable/no | PARCIAL | Temporary/config/response errors. | HTTP/rejected/unexpected se clasifican mal. | Taxonomía. | Alta |
| 3 mensajes usuario | PARCIAL | Vistas muestran errores sanitizados; textos técnicos persisten. | Confusión/filtración. | Catálogo con correlation ID. | Media |
| 4 código ARCA | PARCIAL | `error_code` y response; parser fusiona. | Se pierde código por observación/evento. | Persistencia estructurada. | Alta |
| 5–6 timeout/consulta | 5 NO; 6 NO | Timeout → retry; no consulta `fiscal_emission.py:271-314`, `tasks.py:111-174`. | Doble solicitud. | BLK-01. | Bloqueante |
| 7 backoff | PARCIAL | Celery retry/countdown y beat `tasks.py:60-174`; settings. | Política uniforme e insegura. | Backoff sólo tras clasificación/consulta. | Bloqueante |
| 8 máximo | SÍ | `ARCA_EMISSION_MAX_RETRIES` `base.py:280-291`. | Límite no hace seguro el retry. | Condicionar. | Alta |
| 9 backend retry | SÍ | Celery. | Ciego. | Reconcile-first. | Bloqueante |
| 10 reload duplica | PARCIAL | POST/status/locks/source key. | Puede encolar duplicados, aunque lock frena varios casos. | Idempotency HTTP/UI disable. | Alta |
| 11 caída frontend | SÍ | Worker backend continúa. | Feedback tardío. | Poll/status claro. | Baja |
| 12 caída backend recupera | PARCIAL | Stale submitting recovery `tasks.py:111-174`. | Recupera de forma peligrosa. | Consulta remota. | Bloqueante |
| 13 diagnóstico admin | PARCIAL | Detalle/intentos/preflight. | Preflight puede decir ok con queries fallidas. | Consola read-only/reconcile. | Alta |
| 14 correlation ID | NO | No ID transversal. | Trazabilidad pobre. | UUID por operación/intento. | Alta |
| 15 ARCA no disponible | PARCIAL | `pending_retry` conserva documento. | No distingue indisponible de incierto. | Estados separados/circuit breaker. | Alta |

## 19. Pruebas automatizadas

| Pregunta | Estado | Evidencia | Riesgo | Cambio | Sev. |
|---|---|---|---|---|---|
| 1 infraestructura | SÍ | Django TestCase y CI `.github/workflows/ci.yml:30-42`. | Suite completa excedió 10 min. | Perf/sharding/timeout visible. | Media |
| 2 cálculos | PARCIAL | `core/test_commercial_rules.py:34-112`. | Cobertura tributaria mínima. | Matriz completa. | Alta |
| 3 CUIT | PARCIAL | `admin_panel/test_security_hardening.py:336-423`; no cubre todas las rutas. | Alta directa inválida. | Tests modelo/import/API/admin. | Alta |
| 4 permisos | SÍ | `core/test_security_hardening.py:20-...` y tests admin. | Faltan permisos fiscales granulares. | Matriz nueva. | Alta |
| 5 estados | PARCIAL | Snapshots/print/tipos; no state machine ARCA. | Transiciones críticas sin test. | Tests exhaustivos. | Alta |
| 6–11 concurrencia/idempotencia/timeout/rechazo/autorización/mocks | 6 NO; 7 NO; 8 NO; 9 NO; 10 NO; 11 NO | Ningún test invoca `ArcaWsfeClient`/WSAA/WSFE. | Fallas críticas invisibles. | Suite offline. | Alta |
| 12 sin cert real | SÍ | Tests actuales no requieren cert; futuros mocks pueden usar cert efímero. | No existe suite ARCA aún. | Fixtures seguros. | Media |
| 13 prueba homologación | NO | No test opt-in. | Sin smoke controlado. | Comando separado/manual. | Alta |
| 14 deshabilitada default | NO APLICA | No existe prueba externa. | Al crearla podría correr en CI. | Marcador/env opt-in + host guard. | Alta |
| 15 CI sin secretos | SÍ | Copia `.env.example`, ejecuta tests `ci.yml:30-42`. | Sin SCA/secret scan. | Agregar scans. | Alta |
| 16 datos reales fixtures | NO | No se hallaron certs/fixtures fiscales reales; ejemplos/placeholders. | No se verificó historial con scanner. | Datos sintéticos documentados. | Media |
| 17 flujo E2E | NO | No test create→CAE simulado→persist→UI. | Integración no validada. | E2E offline con SOAP mock. | Alta |

Ejecución auditada: 19 pruebas focalizadas pasaron; `manage.py check` y `pip check` pasaron. La suite completa no terminó en 10 minutos, por lo que su estado es **NO SE PUDO VERIFICAR**, no “fallida”.

## 20. Experiencia de usuario

| Pregunta | Estado | Evidencia | Riesgo | Cambio | Sev. |
|---|---|---|---|---|---|
| 1 confirmación | PARCIAL | Detalle pide confirmación `fiscal/detail.html:470-474`; lista emite sin confirm `fiscal/list.html:112-115`. | Emisión accidental. | Confirmación uniforme/resumen. | Alta |
| 2–5 tipo/POS/cliente/totales | PARCIAL | Detalle/print muestran datos; no siempre breakdown completo previo. | Usuario confirma datos incompletos. | Pantalla preflight autoritativa. | Alta |
| 6 botón bloqueado | NO | No disable inmediato robusto observado. | Doble cola. | Disable + request id. | Alta |
| 7 doble clic | PARCIAL | Puede encolar dos; locks/status suelen frenar segundo. | Carga/edge races. | Idempotency endpoint. | Alta |
| 8 reload | SÍ | Emisión es POST; reload GET no reenvía. | Re-submit del formulario aún posible. | PRG/idempotency. | Media |
| 9 estados visibles | PARCIAL | Processing/authorized/rejected/pending; no A+obs ni incierto. | Usuario reintenta mal. | Estados/acciones seguros. | Bloqueante |
| 10 CAE post-aprobación | SÍ | Se guarda CAE sólo en authorized `fiscal_emission.py:327-405`. | Parse de observaciones incompleto. | Tests. | Alta |
| 11 historial | SÍ | Documento se crea antes de emisión y lista lo muestra. | Puede llamarse factura antes del CAE. | Etiqueta “preparado/no autorizado”. | Alta |
| 12 detalle auditoría | PARCIAL | Detalle e intentos. | No correlation/reconcile. | Timeline read-only. | Alta |
| 13 filtro vendedor | NO | Lista por empresa, no asignado `fiscal.py:326-405`. | Requisito UX no resuelto, aunque visibilidad general fue decidida. | Filtro opcional “mías”. | Baja |
| 14 admin todas | SÍ | Scope por empresa y acceso admin. | Multicompany requiere selección. | Mantener. | Baja |
| 15 advertencia homo | NO | No badge consistente en detalle/print. | Confusión de ambiente. | Banner persistente. | Alta |
| 16 marca sin validez | NO | `print.html:398-547`; pie incluso afirma autorizado. | Documento de prueba confundible. | Watermark obligatorio. | Alta |

## 21. Datos externos que no dependen del código

| Elemento | Necesario | ¿Ya existe? | ¿Repo lo verifica? | Acción externa del usuario | ¿Bloquea primera prueba? |
|---|---|---|---|---|---|
| 1 clave fiscal ARCA | SÍ | NO SE PUDO VERIFICAR | NO | Obtener/custodiar acceso. | SÍ, gestión previa |
| 2 adhesión/relación WSAA/`wsfe` (el texto decía WSASS) | SÍ | NO SE PUDO VERIFICAR | NO | Alta CEE y relación del servicio. | SÍ WSAA |
| 3 certificado homologación | SÍ | NO; rutas locales inexistentes | PARCIAL, sólo rutas | Crear/obtener y montar seguro. | SÍ WSAA |
| 4 clave privada correspondiente | SÍ | NO; ruta inexistente | PARCIAL | Custodiar/montar read-only. | SÍ WSAA |
| 5 asociación certificado-WSFEv1 | SÍ | NO SE PUDO VERIFICAR | NO | Administrador de relaciones ARCA. | SÍ WSAA/WSFE |
| 6 CUIT representado/habilitado | SÍ | Hay valores configurados, validez NO SE PUDO VERIFICAR | PARCIAL | Confirmar titular/representación. | SÍ |
| 7 punto venta de prueba | SÍ | 2 POS locales homo; alta externa NO SE PUDO VERIFICAR | PARCIAL | Alta Web Services y consulta oficial. | SÍ WSFE |
| 8 tipos habilitados | SÍ | Config local, habilitación externa NO SE PUDO VERIFICAR | PARCIAL | Confirmar matriz ARCA. | SÍ autorización |
| 9 datos emisor completos | SÍ | PARCIAL | SÍ local | Completar/validar con asesor. | SÍ autorización |
| 10 condición IVA emisor | SÍ | Hay texto local; exactitud NO SE PUDO VERIFICAR | PARCIAL | Confirmar oficialmente. | SÍ autorización |
| 11 comprobantes iniciales | SÍ | SÍ: A/B + NCA/NCB; falta cuál primero | SÍ, `docs/arca/ARCA_OPEN_QUESTIONS.md` | Elegir primer caso. | SÍ autorización |
| 12 receptores permitidos | SÍ | NO SE PUDO VERIFICAR | NO | Proveer datos sintéticos permitidos. | SÍ autorización |

## 22. Primera prueba recomendada

El procedimiento exacto, precondiciones, acciones, resultados, evidencia, criterios de detención y recuperación está en `.ai/ARCA_TEST_PLAN.md`. Orden:

1. **Offline:** tests contractuales, seguridad, cálculo, concurrencia e incertidumbre sin red.
2. **Conectividad:** DNS/TLS/NTP/timeout y `FEDummy` sólo homologación, sin TA/CAE.
3. **WSAA:** comando aislado, TRA/CMS/TA, sin importar ruta de emisión.
4. **Consultas WSFE:** parámetros, POS, condición IVA, último número y consulta existente; sin CAE.
5. **Autorización:** sólo con todos los bloqueos cerrados, aprobación admin, una solicitud y consulta posterior.
6. **Timeout:** marcar incierto, bloquear serie, `FECompConsultar`, jamás reenviar hasta confirmar inexistencia.

## 23. Evaluación de riesgos

| ID | Riesgo | Evidencia | Prob. | Impacto | Severidad | Solución |
|---|---|---|---|---|---|---|
| R01 | Exposición de certificado | Env/rutas sin secret manager `base.py:277` | Media | Crítico | Alta | Secret store, ACL, rotación |
| R02 | Uso accidental de producción | Host no ligado a ambiente `arca_client.py:187-203` | Media | Crítico | Bloqueante | Allowlist deny-by-default |
| R03 | Emisión/reintento duplicado | `tasks.py:111-174` | Alta | Crítico | Bloqueante | FECompConsultar primero |
| R04 | Pérdida correlatividad | sync `first`, fail-open `fiscal_emission.py:78-107` | Alta | Crítico | Bloqueante | Reconciliación fail-closed |
| R05 | Cálculo fiscal incorrecto | fallback 21/IVA omitido `arca_client.py:439-501` | Media | Alto | Alta | Validación + matriz tests |
| R06 | Permisos insuficientes | una capability para emitir/NC/retry | Media | Alto | Alta | RBAC granular |
| R07 | Edición/borrado autorizado | `core/admin.py:48-103`, cascadas | Media | Crítico | Bloqueante | Inmutabilidad/PROTECT |
| R08 | Mezcla cotización/factura | Separación existe, pero print sin CAE parece oficial | Media | Alto | Alta | Gate por estado/watermark |
| R09 | Pérdida trazabilidad | intentos editables, sin correlation | Media | Alto | Alta | Ledger append-only |
| R10 | Falta de backend | Backend sí existe | Baja | Alto | Baja | Mantener boundary |
| R11 | Secretos en frontend | No observados | Baja | Crítico | Baja | CI scan/CSP |
| R12 | Concurrencia vendedores | Lock DB sin test PostgreSQL | Media | Crítico | Alta | Tests multiproceso |
| R13 | Datos fiscales incompletos | Falta inicio/condición ID | Alta | Alto | Bloqueante | Completar/validar |
| R14 | Datos navegador manipulados | Backend recalcula | Baja | Alto | Media | Rechazo estricto/hash |
| R15 | PDF falso autorizado | `print.html:509-521` | Alta | Alto | Alta | No render fiscal sin CAE |

## 24. Preguntas para el usuario

Existen 18 decisiones reales pendientes y están, sin repetir las ya documentadas, en `.ai/QUESTIONS_FOR_USER.md`. Incluyen emisor/custodia/infraestructura, POS, primer caso, consumidor final, aprobación, permiso de NC, redondeo, tolerancia, proveedor CUIT, respuesta incierta, PDF, entrega y conservación.

## 25. Veredicto final

# D. BLOQUEADO

1. **Resumen:** arquitectura aprovechable, controles parciales y modelos avanzados, pero no puede autenticarse con la configuración local ni autorizar de forma segura.
2. **Bloqueantes:** BLK-01 a BLK-06 en `.ai/ARCA_BLOCKERS.md`.
3. **Riesgos altos:** 9 adicionales; destacan PDF no autorizado, datos mutables, IVA silencioso, redondeo, estados, falta de tests, CUIT y cache WSAA.
4. **Correcciones mínimas:** host allowlist; estado incierto+`FECompConsultar`; correlatividad fail-closed; campo/tabla condición IVA; inmutabilidad; suite offline; PDF homologación.
5. **Externos faltantes:** certificado/clave de homologación utilizables, CEE/relación `wsfe`, CUIT, POS/tipos y receptores confirmados.
6. **Orden:** seguridad de ambiente → dominio/estado/reconciliación → contrato fiscal/cálculo → inmutabilidad/RBAC → tests → credenciales externas → WSAA → consultas → autorización.
7. **Tiempo técnico relativo:** **grande** para llegar a autorización controlada; **mediano** para dejar lista una prueba WSAA-only segura, más la gestión externa.
8. **¿Es seguro comenzar únicamente con WSAA ahora?** **NO.** WSAA por sí solo no emite comprobantes, pero en el estado auditado faltan credenciales utilizables y garantías de ambiente/custodia/renovación. Será seguro después de cerrar BLK-03 y BLK-05, implementar el comando aislado del plan, validar NTP/logs/lock y aprobar la etapa offline. No es necesario cerrar el payload WSFE para esa prueba aislada.

## 26. Puntaje de preparación

| Categoría | Máximo | Obtenido | Fundamento |
|---|---:|---:|---|
| Arquitectura backend | 15 | 12 | Backend real y servicios; duplicación/acoplamiento |
| Seguridad y secretos | 15 | 8 | Backend-only y redacción; sin secret manager/lifecycle/SCA |
| Separación de entornos | 10 | 4 | Flag/POS existen; host no ligado y sin DB homo aislada |
| Modelo de datos fiscal | 10 | 7 | Documento/snapshot/intentos; estados/inmutabilidad incompletos |
| Cálculos y validaciones | 10 | 5 | Decimal/recalculo; redondeo/matriz/IVA incompletos |
| Numeración e idempotencia | 15 | 4 | Locks/source key; timeout y sync bloqueantes |
| Roles y permisos | 10 | 6 | Backend capability; granularidad/admin bypass |
| Manejo de errores | 5 | 2 | Clases/retries; incertidumbre incorrecta |
| Auditoría y persistencia | 5 | 3 | Intentos/snapshot; no append-only/correlation/retención |
| Pruebas automatizadas | 5 | 1 | Infraestructura local; sin suite ARCA |
| **Total** | **100** | **52** | **Requiere trabajo importante; bloqueantes prevalecen** |

## Plan de corrección

1. **P0 seguridad:** BLK-03, aislamiento de DB/egress, secret store/lifecycle, fail-fast.
2. **P0 integridad:** BLK-01/02, estado incierto, `FECompConsultar`, serie remota y correlation/idempotency.
3. **P0 contrato:** BLK-04, tablas paramétricas, Errors/Obs/Events, IVA/totales/redondeo.
4. **P0 persistencia:** BLK-06, `PROTECT`, admin read-only, snapshots como única fuente.
5. **P1 pruebas/UX:** suite offline, PostgreSQL concurrency, watermark/badges, confirmaciones.
6. **P1 externo:** resolver preguntas y gestionar credenciales/relaciones sin incorporarlas al repo.
7. **Gates:** ejecutar etapas de `.ai/ARCA_TEST_PLAN.md` una por una y conservar evidencia sanitizada.

## Archivos generados por esta auditoría

- `.ai/ARCA_READINESS_REPORT.md`
- `.ai/ARCA_BLOCKERS.md`
- `.ai/QUESTIONS_FOR_USER.md`
- `.ai/ARCA_TEST_PLAN.md`
