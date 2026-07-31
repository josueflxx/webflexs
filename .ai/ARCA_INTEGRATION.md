# Integración ARCA verificada

**Fecha de verificación:** 22 de julio de 2026  
**Regla:** confirmar nuevamente manual, WSDL y habilitaciones en el momento de implementar y antes de cada salida a producción. No se realizó ninguna llamada con certificado real.

## 1. Resultado de la verificación

La integración técnicamente adecuada es:

- **WSAA** para autenticación/autorización y obtención de `token`/`sign` temporales.
- **WSFEv1** para parámetros, numeración, solicitud de CAE y consulta/recuperación de comprobantes.
- **`ws_sr_constancia_inscripcion`** para consulta de constancia de inscripción por CUIT; reemplaza al deprecado `ws_sr_padron_a5`.
- Especificación oficial de **QR de factura electrónica** para construir el enlace/payload después del CAE.

La implementación existente tiene elementos reutilizables, pero no es compatible con producción actual: `core/services/arca_client.py` no envía `CondicionIVAReceptorId`, campo obligatorio por RG 5616 desde el 15 de abril de 2025. Además, no implementa `FECompConsultar` y persiste información que puede incluir credenciales.

## 2. Fuentes oficiales consultadas

- [ARCA — Arquitectura general de Web Services](https://www.arca.gob.ar/ws/documentacion/arquitectura-general.asp)
- [ARCA — WSAA, certificados, asociación y endpoints](https://www.arca.gob.ar/ws/documentacion/wsaa.asp)
- [ARCA — Factura electrónica / Web Services](https://www.arca.gob.ar/fe/ayuda/webservice.asp)
- [ARCA — Manual WSFEv1 RG 4291](https://www.arca.gob.ar/fe/ayuda/documentos/wsfev1-RG-4291.pdf)
- [ARCA — RG 5616/2024 vigente](https://biblioteca.arca.gob.ar/search/query/norma.aspx?p=t%3ARAG%7Cn%3A5616%7Co%3A9%7Ca%3A2024%7Cf%3A17%2F12%2F2024)
- [ARCA — Catálogo de Web Services](https://www.arca.gob.ar/ws/documentacion/catalogo.asp)
- [ARCA — Manual Constancia de Inscripción v4.1](https://www.arca.gob.ar/ws/WSCI/manual_ws_sr_ws_constancia_inscripcion.pdf)
- [ARCA — QR, conceptos generales](https://www.arca.gob.ar/fe/qr/conceptos-generales.asp)
- [ARCA — Especificación técnica del QR](https://www.arca.gob.ar/fe/qr/documentos/QRespecificaciones.pdf)

Observación de control documental: el PDF WSFE servido por el sitio oficial al momento de la revisión se identifica como v4.5 y muestra “Revisión correspondiente al 1 de Septiembre de 2026”, fecha posterior al corte de este análisis. Por esa inconsistencia editorial no debe tomarse el número de versión como única fuente; se deben contrastar RG 5616, WSDL oficial y comportamiento de homologación. Los métodos y el campo obligatorio usados en este plan aparecen en el documento oficial y la obligación tiene respaldo normativo vigente independiente.

## 3. Autenticación WSAA

ARCA documenta Web Services SOAP sobre HTTPS. Cada Web Service de Negocio requiere un Ticket de Acceso específico, actualmente con validez limitada de 12 horas. La autenticación usa certificado X.509 y una solicitud TRA firmada como CMS/PKCS#7; WSAA devuelve `token` y `sign`.

Endpoints publicados oficialmente:

| Ambiente | WSAA LoginCms |
|---|---|
| Testing | `https://wsaahomo.afip.gov.ar/ws/services/LoginCms` |
| Producción | `https://wsaa.afip.gov.ar/ws/services/LoginCms` |

Aunque conservan dominio `afip.gov.ar`, son los endpoints que publica actualmente el sitio oficial de ARCA; no deben renombrarse por intuición.

### 3.1 Certificados y asociación

- Testing: certificado gestionado mediante WSASS.
- Producción: certificado mediante “Administrador de Certificados Digitales”.
- Testing: asociación al WSN mediante WSASS.
- Producción: asociación mediante “Administrador de Relaciones de Clave Fiscal”.
- Cada servicio (`wsfe`, `ws_sr_constancia_inscripcion`, u otro) debe estar efectivamente autorizado para la CUIT representada.
- El certificado de testing y el de producción, sus puntos de venta, variables y bases permanecen separados.

### 3.2 Custodia recomendada

- secret manager o vault del entorno; no Git, base general, media ni static;
- permisos de archivo/proceso mínimos si se monta temporalmente;
- referencia por empresa y ambiente, nunca contenido entregado al navegador;
- metadata: fingerprint, serial, subject y vencimiento;
- alerta a 60/30/15/7 días y runbook de rotación;
- rotación con solapamiento controlado y preflight;
- prohibido registrar TRA, CMS, clave, token o sign.

### 3.3 Cache seguro del Ticket de Acceso

Clave de cache: `ambiente + servicio + CUIT representada + fingerprint de certificado`. Guardar cifrado en Redis o memoria de proceso protegida, con TTL anterior al vencimiento y margen de reloj. Usar lock distribuido para evitar múltiples logins simultáneos. Si WSAA invalida la credencial, hacer una renovación controlada; nunca un loop ilimitado.

La aplicación debe monitorear NTP: la deriva del reloj causa fallos de autenticación.

## 4. Consulta de cliente por CUIT

### 4.1 Servicio vigente

El catálogo oficial identifica **Consulta a Padrón Constancia de Inscripción** con ID `ws_sr_constancia_inscripcion`. El antiguo `ws_sr_padron_a5` está deprecado y fue reemplazado.

El manual oficial v4.1 (marzo de 2026) publica:

| Ambiente | WSDL |
|---|---|
| Testing | `https://awshomo.arca.gob.ar/sr-padron/webservices/personaServiceA5?WSDL` |
| Producción | `https://aws.arca.gob.ar/sr-padron/webservices/personaServiceA5?WSDL` |

El path técnico conserva `personaServiceA5`, pero el ID de servicio vigente para autenticación/asociación es `ws_sr_constancia_inscripcion`. No se debe solicitar un TA usando el nombre deprecado.

### 4.2 Método

Usar `getPersona_v2` para una CUIT. Recibe `token`, `sign`, `cuitRepresentada` e `idPersona`. La CUIT representada debe aparecer en las relaciones del ticket. El manual recomienda v2 y mantiene el método anterior sólo por compatibilidad.

`getPersonaList_v2` permite una lista de hasta 250 claves, útil para una futura reconciliación autorizada; la pantalla interactiva debe usar el método singular y cache.

### 4.3 Datos disponibles

La respuesta puede incluir:

- datos generales: ID/CUIT, nombre, apellido, tipo de persona, estado de clave;
- domicilio fiscal estructurado;
- actividades;
- impuestos del régimen general y estado/motivo;
- monotributo y categorías relacionadas;
- caracterizaciones, incluido el tag opcional `fechaSolicitud` incorporado en 2026;
- metadata y errores parciales por bloque.

La condición IVA no debe inferirse de una sola descripción textual. Normalizar desde impuestos/monotributo y reglas versionadas, conservar estado `NEEDS_REVIEW` cuando sea ambigua y validar la combinación contra `FEParamGetCondicionIvaReceptor` al facturar.

### 4.4 Flujo backend

```text
POST /clientes/consulta-fiscal
  -> permiso clientes.consultar_arca
  -> normalizar y validar CUIT (módulo 11)
  -> búsqueda local exacta
  -> respuesta limitada si pertenece a otro vendedor
  -> idempotency record / cache vigente
  -> WSAA para ws_sr_constancia_inscripcion
  -> getPersona_v2
  -> validar esquema, normalizar y clasificar errores
  -> persistir resultado sanitizado + fecha/fuente/hash
  -> devolver sólo campos autorizados al formulario
```

Si ARCA no responde, no se presenta un resultado vacío como validado. Se ofrece carga manual marcada `MANUAL_UNVERIFIED`, con auditoría y posterior reconsulta. La UI actual devuelve `ok=true/source=fallback`; debe cambiar.

### 4.5 Limitaciones

- requiere TA específico, relación/autorización y CUIT representada correcta;
- testing y producción tienen credenciales y datos distintos;
- puede devolver errores parciales para constancia, régimen o monotributo;
- el manual revisado no establece un SLA ni cuota que habilite consultas irrestrictas: usar cache, timeouts, backoff y confirmar condiciones de acceso con ARCA;
- minimizar PII y aplicar retención/acceso;
- antes de desarrollar debe confirmarse que la empresa puede asociar este WSN; si no, decidir un proveedor autorizado sin exponer credenciales.

## 5. WSFEv1

### 5.1 Endpoints

El manual oficial vigente continúa publicando el servicio WSFEv1 bajo dominios AFIP. Usar WSDL/configuración por ambiente y no permitir override arbitrario desde UI.

| Ambiente | Servicio base observado en documentación/código oficial |
|---|---|
| Homologación | `https://wswhomo.afip.gov.ar/wsfev1/service.asmx` |
| Producción | `https://servicios1.afip.gov.ar/wsfev1/service.asmx` |

Revalidar WSDL y certificado TLS antes de implementación. Una configuración de URL sólo puede modificarse mediante despliegue controlado.

### 5.2 Métodos necesarios

| Objetivo | Método WSFEv1 |
|---|---|
| Estado del servicio | `FEDummy` |
| Último número | `FECompUltimoAutorizado` |
| Solicitar CAE | `FECAESolicitar` |
| Recuperar comprobante exacto | `FECompConsultar` |
| Puntos de venta | `FEParamGetPtosVenta` |
| Tipos de comprobante | `FEParamGetTiposCbte` |
| Conceptos | `FEParamGetTiposConcepto` |
| Tipos de documento | `FEParamGetTiposDoc` |
| Alícuotas IVA | `FEParamGetTiposIva` |
| Monedas | `FEParamGetTiposMonedas` |
| Cotización | `FEParamGetCotizacion` |
| Tributos | `FEParamGetTiposTributos` |
| Condición IVA receptor | `FEParamGetCondicionIvaReceptor` |

Las tablas se consultan y cachean con vigencia/fuente; no se copian como enums definitivos en React.

### 5.3 Campo obligatorio desde 2025

La RG 5616 exige identificar la condición ante IVA del receptor. Para Web Services, la nueva versión es obligatoria desde el **15 de abril de 2025**. WSFE documenta `CondicionIVAReceptorId` dentro de `FECAEDetRequest` y los errores 10242/10243/10246 para valor inválido, incompatibilidad u omisión.

El código actual genera XML entre `core/services/arca_client.py:588-622` sin ese elemento. El sistema no debe intentar homologación hasta agregar:

- campo normalizado/snapshot en cliente y factura;
- consulta de combinaciones permitidas por clase;
- validación backend tipo de comprobante + condición;
- serialización en el request;
- tests contractuales de todos los casos habilitados.

### 5.4 Tipos de comprobante

No asumir que A/B/C son suficientes. El código actual usa un mapa fijo para facturas y notas A/B/C. La configuración objetivo sincroniza tipos oficiales y habilitaciones efectivas por emisor/POS, y combina:

- régimen/condición del emisor;
- condición IVA e identificación del receptor;
- concepto productos/servicios/ambos;
- moneda/cotización;
- operación y comprobante asociado;
- autorizaciones del contribuyente.

Factura M y otros tipos se implementan sólo si ARCA los devuelve como habilitados y se cubren sus reglas. La decisión de alcance inicial se solicita al usuario.

## 6. Cálculo fiscal

Usar `Decimal`, cuantización y política de redondeo versionada. Los totales se calculan en backend desde snapshots de líneas:

- neto gravado por alícuota;
- IVA por alícuota;
- no gravado;
- exento;
- tributos con código/base/alícuota/importe;
- total;
- moneda y cotización.

El código actual calcula IVA desde una tasa default por tipo de documento y modo global neto/bruto. Debe reemplazarse por fiscalidad de producto/operación y tests de sumas. Antes de enviar, comprobar que cabecera, desglose y suma de líneas coinciden exactamente.

Para servicios/ambos deben incluirse fechas de servicio y vencimiento de pago cuando corresponda. No fijar `Concepto=1` para todas las operaciones como hace el cliente actual.

## 7. Numeración y concurrencia

Unidad fiscal: `CUIT emisor + punto de venta + tipo de comprobante`.

Algoritmo:

1. Validar completamente el borrador antes de pedir número.
2. Crear autorización/idempotency key y hash del snapshot.
3. Tomar lock DB sobre la secuencia de esa unidad.
4. Consultar `FECompUltimoAutorizado` según política segura; para la primera salida y recuperación, siempre.
5. Candidato = último ARCA + 1.
6. Verificar constraint local y asociar candidato al hash.
7. Cerrar transacción corta; no mantener lock durante la red.
8. Usar un lease/advisory lock de emisión para evitar otro envío de la misma unidad hasta resolver el resultado.
9. Enviar una vez y guardar resultado.
10. Liberar sólo cuando haya resultado definitivo o se haya transferido a reconciliación.

Con varios workers, la coordinación debe probarse en PostgreSQL. SQLite no reproduce `select_for_update` de forma suficiente.

No prometer “cero saltos” a costa de reusar números dudosos. La prioridad legal es no duplicar ni sobrescribir. Todo candidato incierto se consulta antes de reutilizar/avanzar.

## 8. Idempotencia

Capas:

- UI: deshabilitar botón y mostrar estado;
- HTTP: `Idempotency-Key` obligatorio;
- DB: unique por empresa/comando y hash de request;
- agregado: una autorización activa por invoice/snapshot;
- worker: task ID y estado durable, no confiar sólo en Celery;
- ARCA: mismo número y snapshot; recuperar antes de reenviar;
- efectos: unique por outbox event/consumer.

Si la misma key llega con otro payload: `409 Conflict`. Si llega igual: devolver la misma factura/estado. Nunca crear un segundo comprobante por doble clic.

## 9. Estados y recuperación

### 9.1 Clasificación

- `AUTHORIZED`: CAE válido y persistido.
- `AUTHORIZED_WITH_OBSERVATIONS`: CAE más observaciones.
- `REJECTED`: rechazo de negocio definitivo.
- `REQUIRES_ARCA_QUERY`: request pudo haber llegado, pero no hay respuesta confiable.
- `UNKNOWN_ERROR`: error local antes de determinar si hubo envío; requiere clasificación.

### 9.2 `FECompConsultar`

El manual WSFE incluye `FECompConsultar` con `CbteTipo`, `CbteNro` y `PtoVta`, y devuelve datos del request más resultado, código de autorización, tipo de emisión y vencimiento. Es el mecanismo central ante respuesta perdida.

Recovery:

1. tomar el intento incierto;
2. obtener un TA válido;
3. consultar exactamente POS/tipo/número;
4. si existe, comparar CUIT receptor, fecha, moneda, cotización e importes con el snapshot hash;
5. si coincide y está autorizado, persistir CAE/estado y evento outbox;
6. si existe pero no coincide, bloquear P0 y alertar; nunca apropiarse del comprobante;
7. si ARCA confirma ausencia de forma inequívoca, evaluar reenvío del mismo snapshot/número;
8. si la consulta es incierta, continuar en reconciliación con backoff.

El cliente actual sólo implementa `FECompUltimoAutorizado`; cambiar `pending_retry` a reenvío automático es inseguro. Alcanzar el máximo de reintentos no convierte un estado incierto en `REJECTED`.

## 10. Solicitud, respuesta y trazabilidad

Persistir un modelo semántico sanitizado:

- IDs internos, ambiente, método, emisor, POS/tipo/número;
- hash/version del snapshot;
- parámetros fiscales enviados sin credenciales;
- código de resultado, CAE, vencimiento;
- errores/observaciones estructurados;
- timestamps, duración, correlation ID y actor;
- hash opcional de XML para evidencia.

No persistir:

- token/sign completos ni parciales;
- TRA, ticket WSAA o CMS;
- clave/certificado;
- cabeceras de autorización;
- XML crudo si contiene autenticación.

Si por soporte excepcional se requiere un XML, redactarlo con parser por lista permitida antes de storage cifrado y retención corta. Regex sola no es suficiente.

Hallazgo actual: `emit_fiscal_document` guarda `token_preview` y `sign_preview`; las excepciones y respuestas pueden incluir `raw` XML y hasta ticket WSAA. Todo ello debe eliminarse y, si hubo uso real, sanearse/rotarse.

## 11. Notas de crédito y débito

- la nota es un nuevo comprobante fiscal, no una edición/borrado;
- asociación explícita por tipo/POS/número del original;
- misma empresa y receptor, salvo regla oficial específica validada;
- original autorizado y dentro del alcance permitido;
- motivo, creador y aprobador internos;
- control acumulado para ajuste total/parcial;
- líneas/impuestos snapshot;
- efectos compensatorios en ledger, stock y comisión según motivo/regla.

La implementación actual genera `CbtesAsoc` para un único `related_document`, pero necesita validaciones acumuladas, aprobación, motivo y recuperación equivalentes a la factura.

## 12. QR y PDF

La especificación oficial actual indica:

```text
https://www.arca.gob.ar/fe/qr/?p={JSON_EN_BASE64}
```

JSON versión 1:

- `ver`, `fecha`, `cuit`, `ptoVta`, `tipoCmp`, `nroCmp`;
- `importe`, `moneda`, `ctz`;
- `tipoDocRec` y `nroDocRec` cuando corresponda;
- `tipoCodAut` (`E` para CAE, `A` para CAEA);
- `codAut`.

El código actual usa la URL legacy `www.afip.gob.ar`, fuerza moneda `PES` aunque la factura tenga otra moneda y convierte importes a `float`. Debe usar el snapshot autorizado, representación decimal estable y URL publicada actualmente.

Después del CAE:

1. construir JSON desde la factura inmutable;
2. validar schema/códigos y generar Base64;
3. generar QR;
4. renderizar PDF con template versionado;
5. persistir bytes en storage privado/inmutable;
6. guardar SHA-256, tamaño, versión y fecha;
7. descargar/reenviar ese artifact.

El PDF actual se regenera on-demand y el email no adjunta el archivo. Ambos deben cambiar. El QR sólo se genera para documento autorizado; nunca para un borrador con número 0.

## 13. Homologación

### Gates de entrada

- decisiones fiscales respondidas;
- datos de emisor/receptor completos;
- certificado de testing y servicios asociados;
- POS de homologación;
- secret manager, Redis, worker y beat operativos;
- redacción de logs verificada;
- tests unitarios/contractuales/concurrencia aprobados;
- kill switch y panel de reconciliación.

### Casos mínimos

- cada clase/tipo habilitado y condición IVA compatible;
- productos/servicios/ambos si están en alcance;
- alícuotas múltiples, exento/no gravado/tributos si aplican;
- rechazo y observación;
- doble clic y task duplicada;
- dos workers para misma secuencia;
- timeout antes/después del envío;
- XML inválido/respuesta perdida y recuperación por consulta;
- token/cert vencido;
- nota total/parcial y asociaciones inválidas;
- QR decodificado y comparado con factura;
- scanner de secretos sobre logs, DB de intentos y Sentry.

No usar CUIT/certificado/POS de producción ni datos personales reales cuando no sea necesario.

## 14. Producción controlada

Producción exige aprobación explícita, checklist firmado y separación total. `ARCA_ALLOW_PRODUCTION` permanece false hasta el corte.

Controles:

- base, Redis, colas, secretos, POS y certificados independientes;
- identificación visual roja de producción y clara de homologación;
- backup cifrado + restore ensayado;
- worker único/canary inicial y volumen limitado;
- alertas por certificado, cola, estados inciertos, secuencia y divergencia;
- reconciliación previa y posterior al corte;
- exportación contable validada;
- rollback que bloquea nuevas emisiones pero preserva consulta/recuperación.

Una factura autorizada durante el canary no puede revertirse técnicamente; sólo corregirse mediante comprobante fiscal correspondiente.

## 15. Matriz de brechas del código actual

| Hallazgo | Evidencia | Prioridad |
|---|---|---|
| Falta condición IVA receptor | builder XML en `arca_client.py` | P0 |
| Sin recuperación exacta | sólo `FECompUltimoAutorizado` | P0 |
| Credenciales/payload sensible persistible | `raw`, ticket y previews | P0 |
| IVA global por tipo | settings + builder actual | P0 |
| `Concepto=1` fijo | `_build_wsfe_payload` | P0/P1 |
| Tipos A/B/C hardcodeados | mapas locales | P1 |
| Reintento incierto reenvía | `pending_retry` + Celery | P0 |
| Máximo de reintentos pasa a rechazado | `fiscal_emission.py` | P0 |
| PDF mutable | render on-demand | P1 |
| QR legacy/fuerza PES/float | `pdf_generator.py` | P1 |
| Email sin attachment | `fiscal_notifications.py` | P1 |
| Workers no instalados en deploy | `setup_vps.sh` | P0 operativo |

## 16. Criterio de aceptación

La integración ARCA sólo se considera lista cuando un corte simulado demuestra que, ante caída en cualquier punto, cada operación termina en uno de tres resultados auditables: no enviada, rechazada definitivamente o autorizada/recuperada con CAE y registro local. No puede existir un camino de reenvío automático mientras el resultado anterior sea incierto.
