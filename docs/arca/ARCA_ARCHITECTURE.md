# Arquitectura propuesta para ARCA

Fecha: 24 de julio de 2026
Principio rector: homologación primero, backend-only, fiscalidad inmutable y recuperación explícita ante incertidumbre.

## 1. Decisión arquitectónica

Mantener el monolito Django y crear límites internos claros. No se recomienda migrar a Firebase ni crear inicialmente un microservicio separado porque:

- PostgreSQL, Redis y Celery ya resuelven persistencia, concurrencia y procesos de fondo;
- los modelos comerciales y fiscales ya están integrados;
- separar demasiado pronto introduciría consistencia distribuida entre pedidos, stock, cuenta corriente y CAE;
- el volumen esperado no justifica por sí mismo una plataforma adicional.

La integración se diseñará como un conjunto de puertos y adaptadores dentro de Django. Esto permite extraerla a otro servicio en el futuro sin acoplar las vistas al XML.

## 2. Límites de módulos

```text
core/integrations/arca/
├── auth/
│   ├── service.py          # WSAA, TRA, CMS, Ticket de Acceso
│   ├── signer.py           # interfaz de firma
│   └── ticket_cache.py     # caché y lock por emisor/ambiente/servicio
├── transport/
│   ├── soap.py             # HTTP, TLS, timeout, correlación
│   ├── errors.py
│   └── redaction.py
├── wsfe/
│   ├── client.py           # operaciones SOAP
│   ├── mapper.py           # dominio <-> contrato ARCA
│   ├── parser.py
│   ├── parameters.py       # catálogos ARCA
│   └── reconciliation.py
├── taxpayer/
│   ├── constancia.py       # getPersona_v2
│   ├── padron_a4.py        # opcional
│   ├── mapper.py
│   └── cache.py
└── contracts/
    ├── enums.py
    └── result.py

core/services/fiscal/
├── drafts.py
├── validation.py
├── numbering.py
├── issuance.py
├── reconciliation.py
├── credit_notes.py
└── representation.py
```

El archivo real `core/services/arca_client.py` se migrará gradualmente al adaptador `wsfe/client.py`. El stub `core/integrations/arca/client.py` se elimina o convierte en la única interfaz pública; no deben quedar dos implementaciones candidatas.

## 3. Responsabilidades

### 3.1 `ArcaAuthService`

- recibe emisor, ambiente y servicio;
- obtiene referencia segura a certificado/clave;
- crea TRA con reloj UTC;
- firma CMS a través de `Signer`;
- llama WSAA;
- valida token, firma y expiración;
- cachea por `(issuer, environment, service)`;
- coordina refresh con un lock Redis;
- nunca expone token o firma fuera del adaptador.

Interfaz:

```python
ticket = auth.get_ticket(
    issuer_id=...,
    environment="homologation",
    service="wsfe",
)
```

### 3.2 `SoapTransport`

- allowlist estricta de hosts ARCA;
- TLS verificado;
- timeouts separados de conexión y lectura;
- tamaño máximo de respuesta;
- `request_id` y `interaction_id`;
- clasificación de fallos de DNS, conexión, TLS, HTTP, SOAP Fault y parseo;
- sanitización antes de logs o persistencia;
- no realiza reintentos por sí solo en operaciones mutantes.

### 3.3 `ArcaWsfeClient`

Operaciones iniciales:

- `dummy()`;
- `get_points_of_sale()`;
- `get_last_authorized()`;
- `get_vat_receiver_conditions()`;
- `get_document_types()`;
- `get_document_id_types()`;
- `get_vat_rates()`;
- `request_cae()`;
- `get_voucher()`.

Devuelve objetos tipados. No cambia modelos Django ni decide reintentos.

### 3.4 `FiscalDocumentService`

- crea borrador inmutable/versionado;
- valida empresa, receptor, ítems, totales y asociaciones;
- determina tipo A o B según reglas confirmadas;
- genera idempotency key;
- inicia orquestación;
- aplica resultado autorizado;
- impide modificación posterior al CAE;
- genera nota de crédito para corrección.

### 3.5 `FiscalIssuanceOrchestrator`

- bloquea documento;
- verifica estado;
- adquiere lock distribuido de serie;
- obtiene parámetros vigentes;
- consulta último autorizado;
- asigna número;
- crea request canónico y hash;
- persiste intento antes de transmitir;
- llama a ARCA fuera de una transacción larga;
- persiste resultado;
- si no hay certeza, pasa a `uncertain`;
- programa reconciliación, no reemisión.

### 3.6 `FiscalReconciliationService`

- consulta `FECompConsultar`;
- contrasta tipo, punto, número, importe, documento receptor, fecha y código;
- si coincide y está autorizado, reconstruye el resultado local;
- si no existe y la correlatividad permite reenviar, autoriza una reemisión controlada del mismo request;
- si hay contradicción, pasa a `manual_review`;
- produce auditoría append-only.

### 3.7 `ArcaTaxpayerLookupService`

- valida CUIT localmente;
- revisa caché;
- obtiene Ticket para `ws_sr_constancia_inscripcion`;
- llama `getPersona_v2`;
- parsea campos opcionales;
- normaliza persona y domicilio;
- determina una condición IVA candidata;
- conserva snapshot y fecha;
- compara con clientes locales;
- crea revisión manual ante duplicado/conflicto.

No crea o modifica automáticamente un cliente hasta que el usuario confirme la previsualización.

### 3.8 `InvoiceRepresentationService`

- usa exclusivamente snapshot autorizado;
- genera HTML/PDF;
- genera QR oficial;
- añade CAE y vencimiento;
- incluye leyendas vigentes según configuración y validación contable;
- verifica el QR decodificándolo en pruebas.

## 4. Flujo de consulta por CUIT

```text
Operador
  |
  | CUIT
  v
API interna
  |
  +-- valida formato y dígito
  +-- busca duplicados locales
  +-- consulta caché vigente
  |
  v
ArcaTaxpayerLookupService
  |
  +-- WSAA(service=ws_sr_constancia_inscripcion)
  +-- getPersona_v2
  +-- normaliza y guarda snapshot
  |
  v
Vista de comparación
  |
  +-- confirmar alta/actualización
  +-- mantener campo local justificado
  +-- enviar a revisión manual
```

Reglas:

- caché sugerida: 24 horas para uso interactivo, configurable;
- “actualizar desde ARCA” fuerza consulta;
- la condición IVA usada para facturar se vuelve a validar si el snapshot está vencido;
- la interfaz muestra fuente y fecha por campo;
- las respuestas parciales no borran datos locales.

## 5. Flujo de emisión

```text
Borrador validado
     |
     v
QUEUED
     |
     v
lock documento + lock (emisor, ambiente, POS, tipo)
     |
     +-- preflight de parámetros
     +-- último autorizado
     +-- número inmediato siguiente
     +-- request canónico + hash
     |
     v
SUBMITTING
     |
     +-- respuesta concluyente --> AUTHORIZED o REJECTED
     |
     +-- timeout/desconexión --> UNCERTAIN
                                |
                                v
                        FECompConsultar
                          |          |
                     encontrado    no encontrado
                          |          |
                     reconcile    decidir reenvío seguro
                          |          |
                     AUTHORIZED   SUBMITTING o MANUAL_REVIEW
```

## 6. Máquina de estados

| Estado | Significado | Salidas válidas |
|---|---|---|
| `draft` | Editable, sin número oficial | `validated`, `cancelled` |
| `validated` | Snapshot cerrado y validado | `queued`, `draft`, `cancelled` |
| `queued` | Espera worker | `submitting`, `cancelled` |
| `submitting` | Solicitud en curso | `authorized`, `rejected`, `uncertain` |
| `uncertain` | Se desconoce si ARCA procesó | `reconciling` |
| `reconciling` | Consulta oficial en curso | `authorized`, `safe_to_retry`, `manual_review` |
| `safe_to_retry` | ARCA no lo registra y el request no cambió | `queued` |
| `authorized` | CAE persistido | nota de crédito asociada |
| `rejected` | Respuesta fiscal concluyente | `draft` solo si no hay autorización/número comprometido |
| `manual_review` | Contradicción o límite de automatización | resolución auditada |
| `cancelled` | Borrador descartado | terminal |

No usar `rejected` como sinónimo de “cualquier excepción”. No usar `pending_retry` para estado incierto.

## 7. Idempotencia

### Clave de negocio

Para factura:

```text
invoice:{company_id}:{order_id}:{sales_document_type_id}
```

Para nota de crédito:

```text
credit-note:{company_id}:{original_fiscal_document_id}:{credit_operation_uuid}
```

La clave debe ser única y no depender del número ARCA.

### Request canónico

Una vez asignado el número:

1. serializar campos fiscales en JSON con orden estable;
2. normalizar decimales como strings;
3. excluir token y firma;
4. calcular SHA-256;
5. persistir snapshot, versión de esquema y hash;
6. todo reenvío debe usar el mismo hash.

Si el payload cambia, no es un reintento: es otra operación que exige revisión.

## 8. Concurrencia

Dos niveles:

1. `SELECT ... FOR UPDATE` sobre documento y serie en PostgreSQL.
2. Lock Redis por:

```text
arca:numbering:{issuer_id}:{environment}:{pos}:{cbte_type}
```

Propiedades del lock:

- token propietario aleatorio;
- TTL mayor que el timeout de emisión;
- renovación solo por propietario;
- liberación atómica;
- si vence durante una solicitud, el documento queda `uncertain`;
- ningún worker puede saltarse el lock.

La base de datos conserva la verdad local. Redis evita concurrencia entre procesos/hosts, pero no reemplaza las restricciones únicas.

## 9. Numeración

Protocolo:

1. adquirir lock;
2. consultar `FECompUltimoAutorizado`;
3. comparar con serie local;
4. detectar divergencias;
5. proponer `remote_last + 1`;
6. reservar número en PostgreSQL;
7. construir request;
8. enviar;
9. liberar lock solo después de persistir estado o marcar incertidumbre.

No reutilizar huecos automáticamente. Un número local reservado sin resultado se reconcilia; no se reasigna.

## 10. Reintentos

### Permitidos automáticamente

- fallo DNS/transporte antes de recibir respuesta, pero pasando primero por reconciliación;
- 5xx/timeout, con reconciliación;
- Ticket expirado: renovar una vez y repetir solo si se sabe que el request de negocio no llegó;
- indisponibilidad informada por `FEDummy`, antes de emitir.

### No permitidos automáticamente

- rechazo fiscal de datos;
- XML inválido;
- condición IVA incompatible;
- correlatividad inconsistente;
- importe o asociación inválida;
- respuesta parseada parcialmente;
- contradicción entre `FECompConsultar` y snapshot;
- certificado vencido o no autorizado.

Usar backoff exponencial con jitter para consultas y reconciliación. No realizar reintentos HTTP automáticos opacos de `FECAESolicitar`.

## 11. Circuit breaker

Separado por servicio y ambiente:

- WSAA;
- WSFE;
- Constancia.

El circuito abre ante fallos técnicos consecutivos, no ante rechazos fiscales. Mientras esté abierto:

- se pueden crear borradores;
- no se asignan números nuevos;
- no se emite;
- se mantiene la consulta/reconciliación prioritaria con una política limitada;
- el panel muestra estado degradado.

## 12. API interna

### Clientes

```text
POST /api/internal/arca/taxpayers/lookup
POST /api/internal/arca/taxpayers/{snapshot_id}/apply
GET  /api/internal/arca/taxpayers/{cuit}/history
```

El primer endpoint acepta CUIT y empresa activa. Nunca acepta certificado, clave, token o firma.

### Facturación

```text
POST /api/internal/fiscal/documents
POST /api/internal/fiscal/documents/{id}/validate
POST /api/internal/fiscal/documents/{id}/issue
POST /api/internal/fiscal/documents/{id}/reconcile
POST /api/internal/fiscal/documents/{id}/credit-notes
GET  /api/internal/fiscal/documents/{id}
GET  /api/internal/fiscal/documents/{id}/pdf
```

`issue` debe requerir `Idempotency-Key` y devolver 202 si continúa en background.

## 13. Integración con stock y cuenta corriente

- antes del CAE no hay movimiento fiscal definitivo;
- al autorizar, crear efectos mediante claves idempotentes;
- si falla una proyección posterior al CAE, el comprobante sigue autorizado;
- una tarea de reparación reconcilia stock y cuenta corriente;
- una nota de crédito autorizada genera la reversión correspondiente;
- los errores de proyección aparecen en una cola operativa independiente.

## 14. Configuración por ambiente

Separar:

- URL;
- emisor;
- referencia de certificado;
- referencia de clave;
- punto de venta;
- servicios autorizados;
- feature flag;
- fecha de habilitación;
- operador que habilitó.

Producción debe requerir:

- flag global;
- emisor marcado como production-ready;
- punto validado;
- certificado vigente;
- homologación aprobada;
- checklist firmado;
- backup restaurable;
- monitoreo activo.

## 15. Observabilidad

Métricas:

- latencia WSAA/WSFE/Constancia;
- éxito/rechazo/incertidumbre;
- edad de documentos `uncertain`;
- desvío entre numeración local y remota;
- expiración de certificados;
- edad del snapshot de cliente;
- fallos de PDF/QR;
- proyecciones posteriores a CAE pendientes.

Logs mínimos:

- `request_id`;
- `interaction_id`;
- empresa interna;
- ambiente;
- servicio y operación;
- tipo/POS/número cuando exista;
- resultado normalizado;
- código ARCA;
- duración.

Nunca:

- token;
- firma;
- clave;
- CMS;
- certificado completo;
- respuesta padronal completa;
- domicilio o razón social si no es necesario para diagnosticar.

## 16. Estrategia de migración

1. Introducir modelos y adaptadores nuevos sin activar emisión.
2. Envolver el cliente actual detrás de la interfaz.
3. Implementar lectura de parámetros y padrón.
4. Implementar estado incierto y reconciliación.
5. Migrar emisión para usar request canónico.
6. Ejecutar homologación.
7. Recién después retirar flujo antiguo y habilitar producción.

Durante la transición, un feature flag por empresa selecciona `legacy_disabled` o `arca_v2_homologation`. No habrá doble emisión.
