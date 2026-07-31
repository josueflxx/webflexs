# Modelo de datos propuesto para ARCA

Fecha: 24 de julio de 2026

## 1. Objetivos

- conservar snapshots fiscales inmutables;
- separar credenciales, configuración, requests y resultados;
- soportar multiempresa y dos ambientes;
- garantizar idempotencia y correlatividad;
- representar estado incierto;
- auditar consulta de CUIT sin sobreexponer datos;
- permitir reconstruir por qué se emitió un comprobante.

No es necesario reemplazar de inmediato las tablas existentes. Se propone una migración evolutiva.

## 2. Entidades existentes que se conservan

- `Company`;
- `AdminCompanyAccess`;
- `FiscalPointOfSale`;
- `FiscalDocument`;
- `FiscalDocumentItem`;
- `FiscalEmissionAttempt`, temporalmente;
- `ClientProfile`;
- `ClientCompany`;
- `ClientFiscalReview`;
- `Order` y `OrderItem`;
- `AdminAuditLog`.

## 3. Nuevas entidades

### 3.1 `ArcaIssuer`

Representa un CUIT emisor en un ambiente.

| Campo | Tipo | Regla |
|---|---|---|
| `id` | bigint/UUID | PK |
| `company_id` | FK | PROTECT |
| `environment` | enum | homologation/production |
| `cuit` | char(11) | solo dígitos |
| `legal_name_snapshot` | varchar | dato de configuración |
| `tax_condition` | enum | inicialmente responsable inscripto |
| `certificate_ref` | varchar | referencia, nunca contenido |
| `private_key_ref` | varchar | referencia, nunca contenido |
| `certificate_serial` | varchar | metadata |
| `certificate_not_before` | datetime | metadata |
| `certificate_not_after` | datetime | alerta |
| `certificate_fingerprint_sha256` | char(64) | no secreto |
| `is_enabled` | bool | default false |
| `production_approved_at` | datetime nullable | gate |
| `production_approved_by_id` | FK nullable | auditoría |
| timestamps | datetime | |

Restricción única: `(company_id, environment, cuit)`.

No guardar clave privada ni passphrase en esta tabla.

### 3.2 `ArcaServiceAuthorization`

| Campo | Tipo |
|---|---|
| `issuer_id` | FK |
| `service_name` | enum/string |
| `is_expected` | bool |
| `last_verified_at` | datetime nullable |
| `last_status` | enum |
| `last_error_code` | varchar |

Servicios iniciales: `wsfe`, `ws_sr_constancia_inscripcion`, opcional `ws_sr_padron_a4`.

### 3.3 `ArcaPointOfSale`

Puede evolucionar `FiscalPointOfSale` o ser un perfil uno a uno.

Campos adicionales:

- `issuer_id`;
- `arca_number` entero;
- `emission_type`;
- `arca_status`;
- `last_remote_check_at`;
- `last_remote_payload_version`;
- `is_issue_enabled`;
- `last_successful_preflight_at`.

Restricción única: `(issuer_id, arca_number)`.

### 3.4 `FiscalNumberSeries`

Evolución de `FiscalDocumentSeries`.

| Campo | Uso |
|---|---|
| `issuer_id` | separa CUIT y ambiente |
| `point_of_sale_id` | serie |
| `cbte_type` | código ARCA |
| `local_last_reserved` | último número reservado |
| `remote_last_observed` | última lectura ARCA |
| `remote_observed_at` | vigencia |
| `version` | optimistic concurrency |
| `reconciliation_status` | synced/diverged/unknown |

Restricción única: `(issuer_id, point_of_sale_id, cbte_type)`.

### 3.5 `FiscalDocumentSnapshot`

Snapshot inmutable que se emite.

| Campo | Tipo |
|---|---|
| `fiscal_document_id` | OneToOne/versión |
| `schema_version` | int |
| `snapshot_json` | JSON |
| `canonical_json` | text o JSON estable |
| `sha256` | char(64) |
| `created_by_id` | FK |
| `created_at` | datetime |
| `sealed_at` | datetime nullable |

Reglas:

- un snapshot sellado no se actualiza;
- cualquier cambio antes de emisión crea una versión nueva;
- el request ARCA se deriva exclusivamente del snapshot sellado;
- al asignar número se puede crear una versión final numerada, también sellada.

Contenido mínimo:

- emisor;
- ambiente;
- punto y tipo;
- receptor y condición IVA;
- fecha;
- concepto;
- importes;
- moneda/cotización;
- ítems;
- alícuotas;
- comprobantes asociados;
- idempotency key;
- versión de parámetros ARCA usada.

### 3.6 `FiscalTaxBreakdown`

| Campo | Tipo |
|---|---|
| `fiscal_document_id` | FK |
| `tax_kind` | iva/non_taxed/exempt/other |
| `arca_id` | int nullable |
| `rate` | decimal |
| `taxable_base` | decimal |
| `amount` | decimal |

Restricción de consistencia: las sumas deben coincidir con los importes de cabecera.

### 3.7 `FiscalDocumentAssociation`

| Campo | Tipo |
|---|---|
| `document_id` | FK |
| `related_document_id` | FK nullable |
| `relation_type` | credit/debit/other |
| `related_cbte_type` | int |
| `related_pos` | int |
| `related_number` | bigint |
| `related_cuit` | char(11) nullable |
| `related_date` | date nullable |

Los campos snapshot permiten asociar comprobantes importados o externos sin depender de una fila mutable.

### 3.8 `ArcaInteraction`

Registro append-only de cada llamada técnica.

| Campo | Tipo |
|---|---|
| `id` | UUID |
| `fiscal_document_id` | FK nullable |
| `taxpayer_snapshot_id` | FK nullable |
| `issuer_id` | FK |
| `service` | string |
| `operation` | string |
| `environment` | enum |
| `request_id` | UUID |
| `idempotency_key` | varchar nullable |
| `request_hash` | char(64) nullable |
| `attempt_number` | int |
| `transport_state` | enum |
| `business_state` | enum |
| `http_status` | int nullable |
| `arca_result` | varchar |
| `error_codes` | JSON |
| `observations` | JSON |
| `request_redacted` | JSON |
| `response_redacted` | JSON |
| `response_hash` | char(64) nullable |
| `started_at`, `finished_at` | datetime |
| `duration_ms` | int |

Restricción única sugerida: `(fiscal_document_id, operation, attempt_number)`, condicionada cuando hay documento.

No almacenar token, firma, CMS ni clave. Para padrón, guardar respuesta completa cifrada en el snapshot específico o un subconjunto normalizado; el interaction mantiene solo diagnóstico redactado.

### 3.9 `FiscalReconciliation`

| Campo | Tipo |
|---|---|
| `fiscal_document_id` | FK |
| `trigger` | timeout/stale/manual/number_divergence |
| `status` | pending/running/matched/not_found/conflict/failed |
| `remote_last_number` | bigint nullable |
| `remote_voucher_found` | bool nullable |
| `matched_fields` | JSON |
| `mismatched_fields` | JSON |
| `decision` | authorized/safe_to_retry/manual_review |
| `interaction_id` | FK nullable |
| `resolved_by_id` | FK nullable |
| timestamps | datetime |

### 3.10 `TaxpayerLookupSnapshot`

| Campo | Tipo |
|---|---|
| `company_id` | FK |
| `issuer_id` | FK |
| `cuit` | char(11) |
| `source_service` | string |
| `method` | string |
| `status` | found/not_found/partial/error |
| `person_type` | enum |
| `key_status` | string |
| `legal_name` | varchar |
| `first_name`, `last_name` | varchar |
| `fiscal_address_json` | JSON |
| `taxes_json` | JSON |
| `activities_json` | JSON |
| `characterizations_json` | JSON |
| `monotributo_json` | JSON |
| `normalized_vat_condition` | enum nullable |
| `raw_encrypted` | binary/text nullable |
| `schema_version` | int |
| `source_fetched_at` | datetime |
| `expires_at` | datetime |
| `response_hash` | char(64) |
| `requested_by_id` | FK |

Índice: `(company_id, cuit, source_fetched_at desc)`.

No imponer unicidad histórica; se necesitan snapshots sucesivos.

### 3.11 `ArcaParameterSnapshot`

| Campo | Tipo |
|---|---|
| `issuer_id` | FK |
| `parameter_type` | string |
| `scope` | string |
| `payload_json` | JSON |
| `payload_hash` | char(64) |
| `valid_from` | datetime |
| `fetched_at` | datetime |
| `expires_at` | datetime |

Tipos: puntos de venta, comprobantes, documentos, alícuotas, monedas y condición IVA receptor.

## 4. Cambios en `FiscalDocument`

Agregar o normalizar:

| Campo | Propósito |
|---|---|
| `issuer_id` | emisor/ambiente exacto |
| `cbte_type` | código ARCA, no solo abreviatura |
| `receiver_vat_condition_id` | obligatorio |
| `concept` | productos/servicios/ambos |
| `service_from`, `service_to` | cuando corresponda |
| `idempotency_key` | única |
| `snapshot_id` | versión sellada |
| `request_hash` | control de reenvío |
| `authorization_type` | CAE/CAEA |
| `authorized_at` | fecha de proceso |
| `last_reconciled_at` | operación |
| `projection_status` | cuenta/stock/pdf |

Estado propuesto:

```text
draft
validated
queued
submitting
uncertain
reconciling
safe_to_retry
authorized
rejected
manual_review
cancelled
external_recorded
```

## 5. Restricciones de base de datos

1. `idempotency_key` única.
2. `(issuer, point_of_sale, cbte_type, number)` única si número no es nulo.
3. CAE no vacío cuando estado es `authorized`.
4. `cae_due_date` requerido cuando estado es `authorized`.
5. número requerido desde `submitting`.
6. snapshot sellado requerido desde `queued`.
7. `request_hash` requerido desde `submitting`.
8. documento asociado obligatorio para NCA/NCB dentro del alcance.
9. total mayor a cero para factura; política específica para notas.
10. importes no negativos y ecuación de totales validada en dominio.

Django no cubre toda la inmutabilidad. Se recomienda:

- servicio de dominio como única vía de escritura;
- permisos de aplicación;
- validaciones de modelo;
- constraints PostgreSQL;
- trigger opcional que impida cambiar campos fiscales cuando `authorized`.

## 6. Snapshot del receptor

El documento autorizado no debe depender de `ClientProfile` después de emitirse. Guardar:

- tipo y número de documento;
- razón social/nombre;
- domicilio usado;
- condición IVA normalizada y código ARCA;
- fuente;
- fecha de consulta;
- hash del snapshot de padrón.

Una actualización posterior del cliente no reescribe comprobantes históricos.

## 7. Snapshot del emisor

Guardar:

- CUIT;
- razón social;
- condición IVA;
- domicilio;
- punto de venta;
- ambiente;
- fingerprint del certificado usado, nunca el certificado/clave;
- versión del contrato.

## 8. Migración desde modelos actuales

### Migración 1: aditiva

- crear entidades nuevas;
- agregar campos nulos;
- no cambiar flujo.

### Migración 2: backfill

- crear `ArcaIssuer` por empresa y ambiente configurado;
- vincular puntos;
- transformar `request_payload["snapshot"]` a `FiscalDocumentSnapshot`;
- calcular hashes;
- convertir intentos a `ArcaInteraction`.

### Migración 3: escritura dual temporal

- nuevo flujo escribe tablas nuevas;
- vistas antiguas leen compatiblemente;
- comprobar conteos y hashes.

### Migración 4: corte

- activar nuevo orquestador solo en homologación;
- deshabilitar reintento fiscal antiguo;
- migrar reportes;
- hacer obligatorios los campos nuevos.

### Migración 5: limpieza

- retirar stub y campos JSON consolidados que ya no sean fuente de verdad;
- conservar compatibilidad histórica según política de retención.

## 9. Retención

Definir con contador y asesor legal:

- comprobantes, snapshots y asociaciones: plazo fiscal/legal aplicable;
- interacciones redactadas: plazo operativo y de auditoría;
- respuestas crudas padronales: mínimo necesario, cifradas, con expiración;
- Tickets: solo caché hasta vencimiento;
- archivos temporales: destrucción inmediata;
- auditoría de acceso: plazo de seguridad.

La supresión de un cliente no debe destruir comprobantes que exista obligación legal de conservar; debe separarse el dato maestro del snapshot fiscal.

## 10. Consultas operativas necesarias

Índices para:

- documentos por empresa/estado/fecha;
- `uncertain` por antigüedad;
- serie por emisor/POS/tipo;
- interacciones por documento y operación;
- snapshots CUIT por empresa y vigencia;
- certificados por fecha de vencimiento;
- proyecciones posteriores a CAE pendientes;
- revisiones manuales abiertas.
