# Revisión de seguridad

**Fecha:** 22 de julio de 2026  
**Alcance:** análisis estático y controles locales no destructivos. No se probaron credenciales, endpoints privados ni producción.  
**Criterio:** P0 bloquea homologación/producción; P1 bloquea producción; P2 debe entrar en el roadmap; P3 es endurecimiento.

## 1. Resumen

La aplicación tiene buenas defensas base —autenticación backend, CSRF, cookies seguras en producción, HSTS, throttling, capacidades y alcance multiempresa en buena parte de la API—, pero conserva rutas legacy y flujos fiscales con fallas críticas. El mayor riesgo inmediato no es un endpoint público anónimo, sino que un usuario interno autenticado exceda su alcance o que una llamada ARCA deje secretos/estado fiscal inconsistente.

No se encontraron claves privadas/certificados ni `.env` real versionados. Sí hay valores estáticos de credenciales/defaults en scripts versionados. Los valores no se reproducen aquí; si fueron utilizados deben considerarse comprometidos y rotarse.

## 2. Hallazgos prioritarios

| ID | Prioridad | Hallazgo | Impacto | Evidencia principal |
|---|---|---|---|---|
| S-01 | P0 Crítico | Autorización horizontal incompleta en rutas admin | Operador accede/modifica otra empresa/cliente por ID | `admin_panel/views/orders.py:3336`, `:3441`, `:3572`; edición global en `views/clients.py:1450` |
| S-02 | P0 Crítico | Datos WSAA sensibles pueden persistirse | Robo/reutilización de TA, exposición en DB/log/Sentry | `arca_client.py` guarda XML `raw`, ticket y previews de token/sign |
| S-03 | P0 Crítico | Credenciales/defaults estáticos versionados | Acceso no autorizado si fueron usados | `setup_vps.sh:12`; scripts `create_admins.py`, `create_operators.py`, `create_superuser.py` |
| S-04 | P0 Crítico | Reintento fiscal inseguro sin recuperación exacta | Doble emisión o divergencia ARCA/local | sin `FECompConsultar`; `pending_retry` reenvía |
| S-05 | P0 Crítico | Request WSFE omite campo obligatorio | Rechazo sistemático/incompatibilidad fiscal | builder sin `CondicionIVAReceptorId` |
| S-06 | P0 Crítico | Precio manual con permiso insuficiente | Fraude/error de precio y margen | ítems aceptan POST `price`; middleware exige `manage_orders` |
| S-07 | P0 Crítico | Stock puede mutar al crear borrador fiscal | Existencia incorrecta antes de CAE | `apply_sales_document_type_to_fiscal_document` |
| S-08 | P1 Alto | Factura autorizada no es inmutable | Alteración del registro legal | `FiscalDocument.save()` no protege; ítems `CASCADE`; PDF dinámico |
| S-09 | P1 Alto | Efectos contables/fiscales fallan silenciosamente | CAE sin ledger/estado derivado | varios `except Exception: pass` después de guardar |
| S-10 | P1 Alto | Worker/Beat ausentes del deploy | emisión/recovery/backups detenidos | `setup_vps.sh` sólo instala Gunicorn/Nginx/Redis |
| S-11 | P1 Alto | Backup local sin cifrado/restore probado | pérdida o fuga masiva de datos | `core/services/backups.py` |
| S-12 | P1 Alto | CUIT no es único ni validado | cliente/factura asignados a identidad errónea | 48 grupos duplicados y 364 faltantes locales |
| S-13 | P1 Alto | Auditoría fail-open y modificable | acciones sensibles sin evidencia | `core/services/audit.py` silencia errores |
| S-14 | P1 Alto | Payload fiscal/PII sin minimización/retención | exposición y sobre-retención | JSON request/response y XML crudo |
| S-15 | P2 Medio | Tokens DRF permanentes y sin scopes | persistencia de acceso tras filtración | `TokenAuthentication` estándar |
| S-16 | P2 Medio | Secretos de webhook en texto plano | falsificación de eventos si se extrae DB | `WebhookEndpoint.secret` CharField |
| S-17 | P2 Medio | CSP permite inline/eval | aumenta impacto de XSS | configuración CSP base |
| S-18 | P2 Medio | Build .NET/React sin fuente/pipeline | código no auditable y supply-chain | `catalogopro_build/` sólo compilado |
| S-19 | P2 Medio | `.gitignore` no cubre extensiones fiscales | commit accidental de clave/cert | falta regla para `.key/.pem/.p12/.pfx/.crt` |
| S-20 | P3 Bajo | Terminología/URLs legacy AFIP dispersas | error operativo y phishing/confusión | código/templates/docs |

## 3. Detalle y mitigaciones

### S-01 — autorización horizontal

`StaffCapabilityMiddleware` valida capacidades por nombre de ruta. En operaciones de ítems exige `manage_orders`, pero las vistas cargan `Order` por PK sin filtrar por `get_user_companies()` o empresa activa. En clientes, se carga `ClientProfile` global; elegir una empresa autorizada no prueba que el cliente esté vinculado a ella antes de modificar campos globales.

Mitigación:

- crear `authorized_queryset(actor_context)` por entidad;
- resolver siempre `get_object_or_404(authorized_queryset, pk=...)`;
- separar permisos `ver_propios`, `ver_todos`, `editar_propios`, `editar_todos`;
- comprobar asignación de vendedor y empresa en el servicio, no sólo la vista;
- responder 404 para objetos fuera de alcance cuando convenga evitar enumeración;
- tests de IDOR para cada GET/POST/API y combinaciones multiempresa;
- incluir acceso a PDF, exportaciones, búsqueda y auditoría.

### S-02/S-14 — secretos y PII en payloads

El cliente ARCA inserta token/sign en SOAP. Ante errores conserva `response_payload={"raw": ...}` y en un caso ticket WSAA; en éxito construye `token_preview`/`sign_preview`. `FiscalEmissionAttempt` y `FiscalDocument` guardan esos diccionarios. Una excepción puede además alcanzar logs/Sentry.

Mitigación P0:

- eliminar previews y XML crudo de persistencia;
- construir un DTO semántico por lista permitida;
- redactor recursivo central antes de DB/log/Sentry;
- filtros de logging y `before_send` de Sentry;
- cifrado/ACL/retención si se conserva evidencia técnica excepcional;
- job de detección/saneamiento de históricos en modo reporte antes de modificar;
- rotar TA/certificado si se confirma exposición real.

### S-03 — credenciales versionadas

Ubicaciones, sin valores:

| Ubicación | Riesgo |
|---|---|
| `setup_vps.sh:12` | contraseña DB estática en script |
| `create_admins.py` | contraseñas/defaults de cuentas en código |
| `create_operators.py` | contraseñas/defaults de cuentas en código |
| `create_superuser.py` | fallback/operación de credenciales en código |
| `flexs_project/settings/base.py:44` | fallback de secret de desarrollo; no usar fuera de local |
| `catalogopro_build/api/appsettings.json` | connection SQLite configurada y JWT vacío; revisar artefacto |

`.env` local está ignorado y contiene nombres sensibles esperables (`DJANGO_SECRET_KEY`, password email, configuración/ruta ARCA), sin valores expuestos en esta revisión. `.env.example` está versionado y debe contener sólo placeholders.

Acciones:

- retirar toda contraseña literal y exigir variables/secret manager o prompt seguro;
- invalidar/rotar las que alguna vez se usaron;
- revisar historial Git con herramienta de secret scanning sin imprimir secretos;
- añadir CI con detección de secretos y bloqueo de archivos de clave;
- documentar bootstrap de usuarios de un solo uso con cambio obligatorio/MFA.

### S-04/S-05 — seguridad fiscal

Sin `FECompConsultar`, un timeout después de que ARCA autoriza deja resultado incierto y el retry vuelve a enviar. Al llegar al máximo, el código puede marcar rechazo aunque ARCA haya autorizado. Además, el request actual omite condición IVA del receptor.

Mitigación: implementar la máquina de estados y recovery de `ARCA_INTEGRATION.md`; ningún reenvío desde estado incierto; lock por emisor/POS/tipo; idempotency key y snapshot hash; tests en PostgreSQL/homologación; kill switch.

### S-06 — manipulación de precio

Alta/edición de ítems acepta precio manual desde POST. No exige `change_prices`, no compara lista vigente, máximo descuento ni costo, y guarda descuento cero en la edición.

Mitigación:

- comandos backend reciben producto/cantidad y, si existe, una intención de override;
- pricing recalcula precio/lista/impuestos desde DB;
- override exige `ventas.aplicar_descuento`/`productos.editar_precios` según caso;
- venta bajo costo exige permiso explícito y motivo/aprobación;
- guardar regla/version/base/override/actor;
- rechazo de totales o seller IDs enviados por navegador;
- tests modificando requests y API directamente.

### S-07/S-09 — efectos parciales

Crear/asignar documento fiscal puede crear movimientos de stock. Después de CAE, cuenta corriente se sincroniza fuera de la transacción y los errores se silencian. El mismo patrón aparece en documentos manuales.

Mitigación:

- no producir efectos desde `save` o creación de borrador;
- confirmar CAE + outbox dentro de una transacción;
- consumidores idempotentes y observables;
- estado de procesamiento por efecto;
- reconciliador para CAE sin ledger/artifact/stock/comisión;
- dead-letter queue y alerta; nunca `pass`.

### S-08 — inmutabilidad

El control actual está mayormente en UI/servicios de delete. El modelo permite cambiar campos de un autorizado; sus ítems tienen `on_delete=CASCADE`; PDF/QR se reconstruyen desde datos actuales.

Mitigación:

- snapshot completo y `immutable_at`;
- comandos de transición explícitos;
- override de modelo/manager más trigger o rol DB de sólo inserción para campos legales;
- FKs protegidas, líneas append-only;
- Django Admin fiscal read-only;
- PDF persistido con SHA-256/template version;
- nota fiscal para correcciones;
- tests de update, bulk update, delete, cascada y acceso admin.

### S-10/S-11 — operación y recuperación

El sistema programa reintentos/backups, pero el script VPS no administra Celery worker/beat. Los backups son archivos locales comprimidos sin cifrado.

Mitigación:

- unidades systemd separadas para worker y beat con usuario mínimo;
- health de heartbeat, cola, edad del job y DLQ;
- sólo un scheduler efectivo;
- backup PostgreSQL consistente, media y configuración, cifrados fuera del host;
- almacenamiento inmutable/versionado, retención y acceso auditado;
- restores periódicos automatizados y RPO/RTO aprobados;
- backup previo a migración y rollback ensayado.

### S-12 — identidad fiscal

La base local tiene identificadores faltantes/duplicados; no hay índice único ni módulo 11. Un duplicado puede asociar ventas, facturas o vendedores al cliente incorrecto.

Mitigación:

- normalizador único backend;
- reporte y conciliación humana;
- índice único condicional después del backfill;
- endpoint “existe” con respuesta limitada;
- no fusionar historiales automáticamente;
- auditoría de reasignación/merge y alias de registro legado.

### S-13 — auditoría

`AdminAuditLog` es un buen comienzo, pero `log_admin_action` captura cualquier excepción y continúa. No registra resultado/correlation ID como columnas ni garantiza append-only. Falta cobertura integral de login, ARCA, certificados y permisos.

Mitigación:

- para acciones fiscales/permisos, la auditoría forma parte de la transacción o outbox;
- cola separada sólo para copias externas, no para el registro mínimo local;
- rol DB sin update/delete, retención y export inmutable;
- resultado, motivo, IP, correlation ID y before/after sanitizados;
- alerta si falla el pipeline;
- nunca incluir passwords/token/sign/certificado.

### S-15 — tokens API

DRF TokenAuthentication usa un token persistente por usuario. No hay expiración/scopes por integración ni inventario de sesiones.

Mitigación:

- para usuarios humanos priorizar sesión segura y MFA futuro;
- para integraciones usar credenciales separadas, hash en reposo, scopes/empresa, expiración, rotación y último uso;
- revocación inmediata y rate limit por cliente;
- no reutilizar token humano en editor/automatización.

### S-16 — webhooks

El secret se guarda en texto plano y se muestra sólo al crear/rotar, lo cual es correcto en UI pero no en custodia. La firma HMAC incluye timestamp y body, una buena base.

Mitigación:

- cifrado de campo/secret manager;
- mostrar una vez, rotación con solapamiento;
- prevenir destinos privados/loopback y DNS rebinding si el modelo actual no lo cubre completamente;
- allowlist HTTPS en producción;
- no guardar body/response sensible sin retención;
- documentar tolerancia temporal y replay protection del receptor.

### S-17 — navegador

Producción activa cabeceras seguras, cookies Secure/HttpOnly y HSTS. La CSP permite `unsafe-inline`/`unsafe-eval`, probablemente por scripts legacy.

Mitigación: inventariar scripts, migrar a nonces/hashes, retirar `unsafe-eval`, SRI en recursos externos, `frame-ancestors`, pruebas CSP report-only antes de enforcement.

### S-18 — artefactos compilados

Hay DLL/PDB/.NET y bundles JS/CSS sin fuente asociada. No se puede revisar autenticación, secretos, dependencias ni reproducibilidad.

Mitigación: recuperar repositorio/lockfiles/SBOM/pipeline y firmas; o retirar esos artefactos del camino de producción. Nunca darles acceso a certificados/DB fiscal por confianza implícita.

### S-19 — archivos fiscales

`.gitignore` cubre `.env` y bases locales, pero no todas las extensiones usuales de certificados/claves.

Agregar reglas para `*.key`, `*.pem`, `*.p12`, `*.pfx`, `*.crt`, `*.cer`, TRA/CMS y directorios de secretos, con excepciones únicamente para fixtures públicos sintéticos. Complementar con pre-commit/CI; `.gitignore` solo no protege.

## 4. Permisos objetivo

La evaluación debe ser:

```text
autenticado
AND usuario activo
AND empresa autorizada
AND permiso de acción vigente
AND objeto dentro del alcance propio/asignado/todos
AND regla contextual (descuento, monto, aprobación)
```

El frontend puede ocultar botones después de consultar capacidades, pero el backend repite la policy. Los superusuarios tampoco deben poder editar contenido fiscal autorizado; su capacidad es operar correcciones legales y administrar acceso.

Operaciones de doble control recomendadas:

- asignar permisos críticos;
- rotar certificado de producción;
- habilitar producción/POS/tipo;
- venta bajo costo o descuento extraordinario;
- nota de crédito según umbral;
- anular/revertir pagos y ajustes de stock;
- cerrar liquidación de comisión.

## 5. Base de datos

- PostgreSQL con TLS cuando la red no sea local y credenciales por servicio;
- rol de aplicación sin privilegios de esquema;
- migrador separado;
- constraints únicas/checks/FK como defensa adicional;
- Row Level Security es opcional, no reemplaza policies; evaluar cuando el modelo esté estabilizado;
- cifrado de disco/backup y campos sensibles seleccionados;
- queries parametrizadas mediante ORM; revisar SQL/scripts legacy antes de ejecutar;
- ninguna consola Django/Admin abierta a usuarios operativos;
- registro de cambios de esquema y restore antes de migrar.

## 6. Certificados y secretos

Inventario requerido:

| Secreto | Custodio | Rotación/alerta |
|---|---|---|
| Django secret | secret manager de entorno | por incidente/cambio mayor |
| DB/Redis | secret manager, usuario por servicio | programada y por incidente |
| Email | secret manager | programada |
| Certificado/clave ARCA | vault con ACL fiscal | antes de vencimiento/incidente |
| Tokens WSAA | cache temporal protegida | expiración automática |
| Webhook secrets | vault/campo cifrado | bajo demanda/programada |
| API integration tokens | sólo hash + metadata | expiración/rotación |

Acceso al certificado ARCA sólo para el worker fiscal. La UI, Gunicorn general y jobs no fiscales no necesitan leer la clave si la arquitectura de proceso lo permite.

## 7. Logs, Sentry y privacidad

Usar JSON estructurado con nivel, timestamp, servicio, ambiente, correlation ID, entidad interna, código seguro, duración y resultado. Redactar por lista bloqueada y permitida.

Prohibido:

- passwords, secret keys, API tokens;
- token/sign/TA/CMS;
- clave o certificado;
- request SOAP completo;
- CUIT/email/domicilio completos salvo canal restringido justificado;
- archivos PDF en logs.

Crear tests automatizados que emitan errores sintéticos y busquen patrones de token, sign, private key, passwords y CUIT de fixture en logs/DB/Sentry de prueba.

## 8. Controles positivos existentes

- `IsAuthenticated` por defecto en DRF;
- CSRF/sesiones Django y cookies seguras en producción;
- HSTS, SSL redirect, `nosniff`, referrer/COOP/CORP;
- Redis obligatorio en producción para lockout/throttling coherente;
- capacidades backend y pruebas de algunas denegaciones;
- alcance de empresa en múltiples endpoints API/fiscal;
- costos omitidos sin permiso;
- `ARCA_ALLOW_PRODUCTION=False` por defecto;
- source keys/idempotencia en varios modelos;
- firma HMAC de webhooks;
- manifiesto/hash en backup.

Estos controles deben preservarse durante la refactorización.

## 9. Gates de seguridad

### Antes de implementar ARCA

- S-01, S-02, S-03 y S-06 corregidos y testeados;
- datos fiscales normalizados;
- auditoría crítica confiable;
- secret manager decidido;
- permisos por empresa/alcance vigentes.

### Antes de homologación

- S-04, S-05, S-07 y S-09 corregidos;
- worker/beat/health operativos;
- logs y DB de prueba sin secretos;
- pruebas de concurrencia PostgreSQL;
- kill switch/recovery disponibles.

### Antes de producción

- inmutabilidad y PDF artifact verificados;
- backup cifrado y restore exitoso;
- pentest de IDOR/privilegios/inyección/replay;
- certificados y POS de producción aprobados;
- revisión de configuración Django con `check --deploy`;
- runbook de incidente, reconciliación y rotación firmado.
