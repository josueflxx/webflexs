# Seguridad para la integración con ARCA

Fecha: 24 de julio de 2026
Clasificación: diseño de seguridad previo a homologación.

## 1. Objetivos

- proteger claves privadas, certificados y Tickets de Acceso;
- impedir emisión no autorizada;
- evitar mezcla de empresas o ambientes;
- limitar exposición de datos fiscales y personales;
- mantener trazabilidad sin registrar secretos;
- detectar fraude, error operativo e indisponibilidad;
- permitir recuperación sin alterar comprobantes autorizados.

## 2. Clasificación de activos

| Activo | Clasificación | Regla |
|---|---|---|
| Clave privada ARCA | Secreto crítico | Nunca DB, repo, log, backup general ni chat |
| Passphrase | Secreto crítico | Secret store separado |
| Token y firma WSAA | Secreto efímero | Redis cifrado/aislado, TTL; nunca persistencia |
| CMS/TRA firmado | Secreto operativo | Memoria o archivo temporal seguro; destrucción inmediata |
| Certificado público | Confidencial operativo | Fuera del repo; fingerprint visible |
| Respuesta padrón | Dato personal/tributario | Minimización, cifrado y acceso restringido |
| CUIT | Dato personal/identificador | Enmascarar según contexto |
| Request fiscal sin credenciales | Registro fiscal | Snapshot inmutable y acceso contable |
| CAE/comprobante | Registro fiscal | Integridad y conservación |
| Logs | Dato operativo | Redactados y con retención |

## 3. Gestión de secretos

### Requisito mínimo para el VPS actual

Si no se adopta de inmediato un gestor administrado:

- directorio dedicado, por ejemplo `/etc/flexs/arca`;
- propietario exclusivo del usuario del servicio;
- directorio modo `0700`;
- clave privada modo `0600`;
- certificado modo `0644` o más restrictivo;
- systemd referencia rutas, no contenido;
- `.env` solo contiene identificadores/rutas y flags;
- excluir el directorio de backups generales;
- backup de la clave en medio cifrado separado con acceso documentado;
- validar permisos al iniciar y fallar cerrado.

### Objetivo recomendado

Usar un almacén de secretos con:

- control de acceso por identidad de workload;
- cifrado en reposo;
- versionado;
- auditoría;
- rotación;
- recuperación controlada.

Si la librería de firma exige un archivo, materializar la clave solo en un `tmpfs` privado durante el proceso, con permisos estrictos y borrado garantizado. No pasar la clave por argumentos de proceso.

### Prohibiciones

- no pegar certificado o clave en formularios ordinarios;
- no guardar PEM en PostgreSQL sin un diseño criptográfico específico;
- no incluirlos en imágenes, repositorio o artefactos CI;
- no copiarlos a Sentry;
- no incluir el path completo en mensajes mostrados a operadores;
- no usar el mismo certificado para homologación y producción;
- no compartir claves entre empresas.

## 4. Ciclo de vida de certificados

Inventario por emisor:

- ambiente;
- serial;
- fingerprint SHA-256;
- fecha de inicio;
- vencimiento;
- servicios asociados;
- responsable;
- última validación;
- estado.

Alertas:

- 60 días;
- 30 días;
- 15 días;
- 7 días;
- vencido.

Rotación:

1. generar nueva clave de forma segura;
2. obtener certificado;
3. asociar servicios;
4. probar en preflight;
5. activar por versión;
6. mantener rollback corto del certificado anterior si sigue vigente;
7. revocar/retirar;
8. registrar auditoría.

## 5. Separación de ambientes

Controles obligatorios:

- credenciales diferentes;
- puntos de venta diferentes;
- configuración separada;
- claves de caché separadas;
- prefijo visual “HOMOLOGACIÓN”;
- `ARCA_ALLOW_PRODUCTION=False` por defecto;
- producción requiere aprobación persistida y flag;
- tests nunca pueden resolver endpoints de producción;
- allowlist de hosts por ambiente;
- el worker de homologación no recibe secretos de producción si es viable.

Un punto marcado como homologación jamás puede usar la URL de producción, aunque una variable esté mal configurada.

## 6. Autenticación y autorización interna

Capacidades propuestas:

| Capacidad | Admin | Ventas | Contabilidad | Soporte |
|---|---:|---:|---:|---:|
| Crear borrador | Sí | Sí | Sí | No |
| Consultar CUIT | Sí | Sí | Sí | Limitado |
| Confirmar datos oficiales | Sí | Sí | Sí | No |
| Emitir CAE | Sí | No por defecto | Sí | No |
| Crear nota de crédito | Sí | No | Sí | No |
| Reconciliar | Sí | No | Sí | Diagnóstico sin acción |
| Ver payload fiscal redactado | Sí | No | Sí | Parcial |
| Configurar emisor/POS | Sí | No | No | No |
| Rotar certificado | Admin seguridad | No | No | No |
| Habilitar producción | Doble control | No | No | No |

Se recomienda MFA para:

- configuración fiscal;
- habilitación de producción;
- rotación de credenciales;
- exportación masiva de datos;
- cambios de permisos.

## 7. Multiempresa

Cada operación debe resolver empresa activa en servidor y comprobar:

- acceso del usuario a empresa;
- cliente vinculado a empresa;
- pedido vinculado a empresa;
- emisor vinculado a empresa;
- punto vinculado a emisor;
- documento vinculado a todo lo anterior.

Nunca confiar en `company_id` del frontend sin revalidación. El CUIT emisor utilizado se obtiene del `ArcaIssuer` permitido, no del request del usuario.

## 8. Protección de WSAA

- lock por `(issuer, environment, service)` durante refresh;
- margen de expiración;
- usar `expirationTime` real;
- reloj NTP monitoreado;
- límites de frecuencia;
- token/firma solo en memoria o Redis con TTL;
- cifrado de Redis en tránsito si está fuera del host;
- Redis con autenticación y red privada;
- sanitización de XML antes de persistir o loguear;
- no devolver excepciones crudas de OpenSSL.

## 9. Transporte SOAP

- HTTPS con verificación de certificado;
- no desactivar validación TLS;
- DNS y destinos en allowlist;
- impedir redirects a hosts no permitidos;
- tamaño máximo de body;
- timeout de conexión y lectura;
- límite de concurrencia;
- parser XML seguro: sin entidades externas ni DTD;
- validación de tipos y rangos;
- no interpolar datos sin escape;
- no reintentar automáticamente métodos mutantes desde la capa HTTP.

El XML manual actual debe centralizarse y probarse contra WSDL. Una librería SOAP puede reducir errores de contrato, pero debe evaluarse su seguridad, mantenimiento y control de logs antes de adoptarla.

## 10. Logs y redacción

### Permitido

- UUID de interacción;
- empresa interna;
- ambiente;
- operación;
- tipo/POS/número;
- hash del request;
- resultado y códigos;
- duración;
- intento;
- usuario interno.

### Prohibido

- `Token`;
- `Sign`;
- CMS;
- clave privada;
- passphrase;
- contenido completo de certificados;
- respuesta completa de padrón;
- domicilio completo en logs;
- email/teléfono;
- XML crudo sin sanitizar.

Redacción:

- estructural, no solo regex;
- aplicada antes de persistencia;
- aplicada también a excepciones;
- tests con variantes de namespace, mayúsculas y texto escapado;
- campos desconocidos tratados como sensibles por defecto en autenticación.

## 11. Datos personales

La Ley 25.326 exige calidad, finalidad, seguridad y confidencialidad. Controles:

- aviso de finalidad en alta de cliente;
- consulta solo por personal autorizado;
- mostrar fuente y fecha;
- no usar respuesta para fines ajenos a facturación/relación comercial;
- conservar solo campos necesarios;
- cifrar snapshot crudo si se conserva;
- registrar quién consultó;
- permitir rectificación local con motivo;
- no reemplazar silenciosamente datos locales;
- definir retención y supresión compatible con obligaciones fiscales.

Fuente oficial: [Ley 25.326 actualizada](https://www.argentina.gob.ar/normativa/nacional/ley-25326-64790/actualizacion).

## 12. Integridad fiscal

- snapshot sellado con SHA-256;
- request ARCA derivado del snapshot;
- respuesta normalizada y hasheada;
- CAE solo se escribe por el servicio de emisión/reconciliación;
- comprobante autorizado inmutable;
- corrección mediante nota de crédito;
- constraints de base;
- auditoría append-only;
- clock y zona horaria definidos;
- PDF/QR desde snapshot autorizado.

Campos protegidos tras CAE:

- emisor;
- receptor;
- tipo;
- punto;
- número;
- fechas;
- concepto;
- moneda/cotización;
- importes;
- IVA;
- asociaciones;
- CAE y vencimiento;
- snapshot y hash.

## 13. Idempotencia y fraude

- `Idempotency-Key` generada/validada en servidor;
- clave única en DB;
- hash del payload;
- lock distribuido;
- límites por usuario/empresa;
- alertar intentos repetidos con distinto payload;
- alertar emisión fuera de horario si aplica;
- alertar cambio de punto/emisor;
- alertar divergencia de numeración;
- revisión manual en contradicción.

## 14. Backups

Los backups deben:

- cifrarse;
- tener control de acceso;
- registrar restauraciones;
- probarse;
- incluir DB y snapshots fiscales;
- excluir claves privadas de backups generales;
- conservar una copia separada y cifrada de credenciales si la política lo requiere;
- no restaurar credenciales de producción en entornos de prueba;
- preservar auditoría.

Antes de producción debe ejecutarse una restauración completa en un entorno aislado.

## 15. Monitoreo y respuesta

Alertas P0:

- certificado vencido/próximo;
- documento `uncertain` por encima del SLA;
- numeración divergente;
- CAE autorizado sin proyecciones locales;
- intento de producción desde configuración no aprobada;
- fallos consecutivos WSAA;
- respuestas de ARCA con formato desconocido;
- acceso no autorizado a configuración.

Runbook de estado incierto:

1. detener reemisión automática;
2. identificar emisor/POS/tipo/número;
3. consultar `FECompConsultar`;
4. comparar snapshot;
5. reconstruir autorización o mantener revisión;
6. auditar decisión;
7. nunca editar CAE manualmente sin evidencia oficial.

## 16. Revisión previa a homologación

- [ ] No hay secretos en Git ni historial reciente.
- [ ] Permisos de archivos verificados.
- [ ] Certificado de homologación separado.
- [ ] Hosts en allowlist.
- [ ] Parser XML endurecido.
- [ ] Redacción probada.
- [ ] Estado `uncertain` implementado.
- [ ] Reintento ciego eliminado.
- [ ] Permisos fiscales separados.
- [ ] Auditoría de consulta CUIT.
- [ ] Backups y restauración probados.
- [ ] Sentry/logs revisados con datos sintéticos.

## 17. Revisión previa a producción

- [ ] Homologación aceptada.
- [ ] Certificado producción y servicios asociados.
- [ ] Punto de venta confirmado.
- [ ] MFA y doble control.
- [ ] Secret store o baseline VPS aprobado.
- [ ] Alertas activas.
- [ ] Runbooks ejercitados.
- [ ] Restore probado.
- [ ] Revisión contable del PDF.
- [ ] Prueba de canary sin datos reales fuera del procedimiento aprobado.
- [ ] Plan de rollback que deshabilita nuevas emisiones sin alterar CAE existentes.
