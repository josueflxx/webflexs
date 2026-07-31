# Plan detallado de implementación ARCA

Fecha: 24 de julio de 2026
Estrategia: homologación primero. Ninguna fase autoriza por sí sola el uso en producción.

## 1. Objetivo

Entregar en FLEXS:

1. consulta oficial de clientes por CUIT;
2. Factura A/B con CAE;
3. Nota de Crédito A/B con CAE;
4. cálculo de IVA desde precios netos;
5. PDF y QR oficial;
6. idempotencia, correlatividad y recuperación ante incertidumbre;
7. permisos, auditoría, seguridad y monitoreo;
8. migración sin alterar clientes ni el orden actual del sitio oficial.

## 2. Restricciones

- no emitir comprobantes reales durante desarrollo;
- no usar clientes reales si existen datos de prueba;
- no subir certificados al repositorio;
- no activar producción hasta aprobación explícita;
- no modificar comprobantes con CAE;
- no desplegar cambios fiscales junto con cambios visuales no relacionados;
- preservar los datos existentes mediante migraciones aditivas y backups.

## 3. Criterios de prioridad

### P0

- contrato vigente `CondicionIVAReceptorId`;
- estado incierto y reconciliación;
- seguridad de credenciales;
- numeración;
- snapshots;
- pruebas de homologación.

### P1

- alta por CUIT;
- PDF/QR;
- notas de crédito;
- monitoreo;
- interfaz operativa.

### P2

- reportes avanzados;
- A4 complementario;
- automatizaciones de rotación;
- extracción eventual a microservicio.

## 4. Fase 0 — Diseño y línea base

Estado: completada documentalmente con los archivos de `docs/arca`.

### Tareas

- [x] auditar stack real;
- [x] inventariar modelos y servicios fiscales;
- [x] verificar URLs y WSDL;
- [x] verificar cambio de condición IVA;
- [x] identificar riesgo de reintento ciego;
- [x] diseñar arquitectura;
- [x] proponer modelo y seguridad;
- [x] listar preguntas.

### Salida

- siete documentos aprobados;
- respuestas bloqueantes Q1–Q4;
- responsable contable y técnico definidos.

## 5. Fase 1 — Seguridad e infraestructura de homologación

Estimación orientativa: 3 a 5 días técnicos, más tiempos de gestión ante ARCA.

### 5.1 Configuración

1. Crear `ArcaIssuer` y configuración por ambiente.
2. Agregar referencias de certificado/clave, no contenido.
3. Crear feature flags:
   - `ARCA_INTEGRATION_ENABLED`;
   - `ARCA_TAXPAYER_LOOKUP_ENABLED`;
   - `ARCA_ISSUANCE_ENABLED`;
   - `ARCA_PRODUCTION_ENABLED`.
4. Mantener todos en false por defecto.
5. Crear validación de hosts y ambiente.
6. Crear cola Celery `flexs-fiscal`.

### 5.2 Credenciales

1. Generar clave de homologación fuera del repositorio.
2. Obtener certificado por WSASS.
3. Asociar:
   - `wsfe`;
   - `ws_sr_constancia_inscripcion`;
   - A4 solo si se aprueba.
4. Instalar con permisos mínimos.
5. Registrar fingerprint y vencimiento.
6. Probar firma sin persistir CMS.

### 5.3 WSAA

1. Extraer firma a `ArcaSigner`.
2. Implementar `ArcaAuthService`.
3. Caché por emisor/ambiente/servicio.
4. Lock Redis de refresh.
5. Parsear `expirationTime`.
6. Redacción estructural.
7. Métricas y health check.

### Pruebas

- TRA válido;
- firma válida;
- certificado inexistente;
- permisos inseguros;
- reloj desfasado;
- Ticket cacheado;
- refresh concurrente único;
- Ticket separado por servicio;
- token/firma ausentes de logs;
- homologación responde;
- producción bloqueada.

### Criterio de salida

- tres servicios obtienen Ticket cuando están autorizados;
- ningún secreto aparece en DB, logs, Sentry o artefactos;
- certificado de homologación inventariado;
- producción sigue técnicamente bloqueada.

## 6. Fase 2 — Capa SOAP y contratos oficiales

Estimación: 3 a 5 días.

### Tareas

1. Crear transporte SOAP central.
2. Implementar parser XML seguro.
3. Crear errores:
   - configuración;
   - autenticación;
   - transporte;
   - SOAP Fault;
   - contrato;
   - rechazo fiscal;
   - incertidumbre.
4. Agregar correlación UUID.
5. Definir timeouts.
6. Descargar WSDL solo para contract tests/build; runtime usa endpoints allowlisted.
7. Crear fixtures sintéticos de responses.
8. Decidir si se mantiene XML controlado o se incorpora una librería SOAP fijada por versión.

### Pruebas

- XML con namespaces distintos;
- nodos opcionales;
- múltiples `Errors`, `Events` y `Obs`;
- XML malformado;
- entity expansion bloqueada;
- response demasiado grande;
- HTTP 4xx/5xx;
- timeout antes/después de enviar;
- TLS inválido;
- redacción.

### Criterio de salida

- ninguna vista construye XML;
- ningún adaptador cambia modelos;
- fixtures cubren todos los resultados;
- fallos se clasifican sin convertirlos todos en reintentables.

## 7. Fase 3 — Parámetros y preflight WSFE

Estimación: 2 a 4 días.

### Operaciones

- `FEDummy`;
- `FEParamGetPtosVenta`;
- `FEParamGetTiposCbte`;
- `FEParamGetTiposDoc`;
- `FEParamGetTiposIva`;
- `FEParamGetCondicionIvaReceptor`;
- `FECompUltimoAutorizado`.

### Tareas

1. Crear `ArcaParameterSnapshot`.
2. Implementar caché con fecha/hash.
3. Mostrar preflight por empresa/POS.
4. Validar punto habilitado.
5. Mapear condición local a ID ARCA.
6. Bloquear tipos/tasas no soportados.
7. Alertar cambios del catálogo.

### Criterio de salida

- panel muestra salud de WSAA/WSFE/POS;
- condición IVA se obtiene de ARCA;
- el request de prueba contiene `CondicionIVAReceptorId`;
- ninguna emisión se habilita si el preflight falla.

## 8. Fase 4 — Consulta oficial por CUIT

Estimación: 4 a 6 días.

### Backend

1. Implementar `getPersona_v2`.
2. Validar CUIT y dígito.
3. Parsear:
   - datos generales;
   - domicilio fiscal;
   - impuestos;
   - actividades;
   - monotributo;
   - caracterizaciones;
   - errores parciales.
4. Tolerar `fechaSolicitud` opcional.
5. Normalizar razón social:
   - jurídica: `razonSocial`;
   - física: `apellido, nombre`;
   - fallback controlado.
6. Determinar condición IVA candidata con reglas auditables.
7. Guardar `TaxpayerLookupSnapshot`.
8. Cachear.
9. Integrar `ClientFiscalReview`.

### Frontend

1. Campo CUIT con validación.
2. Botón “Consultar en ARCA”.
3. Estado de consulta.
4. Comparación ARCA/local por campo.
5. Fuente y fecha.
6. Confirmar alta/actualización.
7. Mantener dato local con observación.
8. Enviar a revisión manual.

### Reglas

- no sobrescribir automáticamente;
- respuesta parcial no borra;
- duplicado devuelve 409 y revisión;
- error técnico permite carga manual marcada como no verificada;
- facturación puede exigir verificación según política.

### Pruebas

- CUIT inválido;
- persona física;
- jurídica;
- monotributista;
- responsable inscripto;
- exento;
- no encontrado;
- respuesta parcial;
- campo nuevo desconocido;
- duplicado;
- conflicto;
- caché;
- consulta forzada;
- permisos y multiempresa.

### Criterio de salida

- alta por CUIT funciona con casos oficiales de homologación;
- la cola manual conserva conflictos;
- auditoría registra usuario y fecha sin loguear respuesta completa.

## 9. Fase 5 — Modelo fiscal v2 y borradores

Estimación: 5 a 8 días.

### Migraciones

1. Crear `ArcaIssuer`.
2. Evolucionar punto/serie.
3. Crear snapshots sellados.
4. Crear desglose tributario.
5. Crear asociaciones.
6. Crear interacción/reconciliación.
7. Agregar campos de estado/idempotencia.
8. Backfill de documentos existentes.

### Dominio

1. Crear borrador.
2. Calcular precios netos + IVA.
3. Aplicar descuento de categoría antes de IVA.
4. Aplicar override bajo costo solo con observación.
5. Validar ecuación:

```text
ImpTotal = ImpTotConc + ImpNeto + ImpOpEx + ImpIVA + ImpTrib
```

6. Fallar ante alícuota desconocida.
7. Sellar snapshot.
8. Seleccionar A/B.
9. Asociar nota de crédito.
10. Impedir mutación tras sellado/CAE según estado.

### Pruebas

- redondeos;
- múltiples alícuotas;
- descuentos;
- precio neto;
- stock opcional;
- snapshots no cambian al editar cliente/producto;
- constraints;
- idempotencia;
- multiempresa.

### Criterio de salida

- todo request fiscal se deriva de snapshot;
- totales coinciden al centavo;
- documentos históricos quedan intactos.

## 10. Fase 6 — Numeración, emisión y estado incierto

Estimación: 7 a 10 días.

### 10.1 Locks y serie

1. Implementar lock Redis propietario.
2. Bloqueo PostgreSQL.
3. Consultar último autorizado en cada nueva secuencia.
4. Reservar número.
5. Guardar request canónico y hash.
6. Crear interacción `started` antes de enviar.

### 10.2 FECAESolicitar

Campos mínimos:

- auth;
- cabecera;
- concepto;
- documento receptor;
- número;
- fecha;
- importes;
- moneda/cotización;
- `CondicionIVAReceptorId`;
- IVA;
- comprobante asociado para notas.

Eliminar:

- fallback automático al 21 %;
- lectura del cliente vivo;
- clasificación genérica de errores.

### 10.3 Resultado

- autorizado: guardar CAE, vencimiento, fecha y observaciones;
- rechazado concluyente: guardar todos los códigos;
- fallo técnico ambiguo: `uncertain`;
- nunca volver a `ready` automáticamente.

### 10.4 Reconciliación

1. Implementar `FECompConsultar`.
2. Comparar campos.
3. Reconstruir autorización.
4. Consultar último autorizado como contexto.
5. Decidir `safe_to_retry` o revisión.
6. Reusar exactamente el mismo hash.
7. Límite y backoff.

### 10.5 Reemplazo de tarea antigua

- desactivar el paso que convierte `submitting` directamente en `pending_retry`;
- nueva tarea convierte stale a `uncertain`;
- la siguiente acción es reconciliar;
- un rechazo fiscal no se reintenta automáticamente.

### Pruebas unitarias

- doble clic;
- dos workers;
- dos servidores;
- mismo pedido;
- series diferentes;
- timeout antes de response;
- ARCA autorizó y response se perdió;
- ARCA no recibió;
- contradicción;
- lock vencido;
- worker muerto;
- Ticket expirado;
- error 4xx/5xx;
- request hash distinto.

### Pruebas de homologación

- Factura A;
- Factura B;
- rechazo por condición IVA;
- rechazo por correlatividad;
- consulta del autorizado;
- recuperación de response perdido simulada;
- observaciones;
- tipos de documento.

### Criterio de salida

- cero reintentos ciegos;
- todo timeout termina en reconciliación;
- doble envío concurrente produce un solo documento;
- CAE recuperable por consulta.

## 11. Fase 7 — Cuenta corriente, stock y efectos

Estimación: 3 a 5 días.

### Tareas

1. Definir claves idempotentes de proyección.
2. Aplicar cuenta corriente después de CAE.
3. Aplicar stock después de CAE solo si producto controla stock.
4. No revertir CAE si falla proyección.
5. Crear `projection_status`.
6. Crear tarea de reparación.
7. Dashboard de proyecciones pendientes.

### Nota de crédito

- movimiento inverso según motivo;
- no reponer stock automáticamente si el motivo no implica devolución física;
- decisión explícita;
- límite por saldo acreditable.

### Criterio de salida

- reejecutar proyección no duplica;
- CAE siempre permanece;
- fallos quedan visibles y reparables.

## 12. Fase 8 — PDF, QR y envío

Estimación: 3 a 5 días más validación contable.

### Tareas

1. Cambiar URL base a `https://www.arca.gob.ar/fe/qr/`.
2. Generar QR desde snapshot.
3. Respetar moneda y cotización.
4. Validar campos obligatorios.
5. Actualizar textos AFIP a ARCA.
6. Mostrar:
   - emisor;
   - receptor;
   - condición IVA;
   - tipo/letra;
   - POS/número;
   - fecha;
   - ítems;
   - neto;
   - IVA;
   - total;
   - CAE/vencimiento;
   - QR;
   - leyendas aprobadas.
7. Incorporar transparencia fiscal según revisión contable.
8. Guardar hash del PDF o regenerarlo determinísticamente.
9. Email solo tras autorización.

### Pruebas

- decodificar QR y comparar JSON;
- PDF Factura A/B/NCA/NCB;
- página múltiple;
- caracteres argentinos;
- importes grandes;
- sin logo;
- impresión;
- snapshot histórico;
- no generar QR si no hay CAE.

### Criterio de salida

- contador aprueba cuatro muestras;
- QR abre verificador oficial;
- ningún dato se toma del perfil mutable.

## 13. Fase 9 — Notas de crédito

Estimación: 4 a 7 días, según reglas funcionales.

### Tareas

1. Seleccionar factura autorizada.
2. Validar misma empresa/letra/moneda/receptor.
3. Motivo obligatorio.
4. Total o parcial.
5. Control de saldo acreditable.
6. Snapshot de asociación.
7. `CbtesAsoc`.
8. Emisión con mismo protocolo de incertidumbre.
9. Reversión de cuenta.
10. Decisión de stock.
11. Estadísticas del vendedor netas de notas.

### Criterio de salida

- no se puede acreditar más que la factura;
- nota autorizada es inmutable;
- relación visible en ambos documentos.

## 14. Fase 10 — Interfaz operativa

Estimación: 4 a 6 días.

### Pantallas

- salud ARCA por empresa;
- configuración no sensible;
- alta por CUIT;
- borrador fiscal;
- confirmación antes de emitir;
- progreso asíncrono;
- detalle de respuesta;
- cola de incertidumbre/reconciliación;
- revisiones de cliente;
- certificado próximo a vencer;
- nota de crédito;
- historial/auditoría.

### UX

- sin exponer XML;
- mensajes accionables;
- distinguir rechazo fiscal de caída técnica;
- bloquear doble clic;
- no decir “factura emitida” antes de CAE;
- badge fuerte de homologación;
- confirmación de empresa/POS/tipo/total;
- no usar emojis; usar iconografía profesional.

### Criterio de salida

- pruebas de rol;
- pruebas de navegación;
- usuario entiende qué debe corregir;
- soporte no puede ejecutar acciones fiscales.

## 15. Fase 11 — Observabilidad, runbooks y continuidad

Estimación: 3 a 5 días.

### Métricas y alertas

- éxito/rechazo/incertidumbre;
- latencia;
- salud;
- edad `uncertain`;
- divergencia;
- vencimiento;
- snapshot CUIT vencido;
- proyección pendiente.

### Runbooks

- ARCA caído;
- WSAA rechazado;
- certificado vencido;
- response perdido;
- número divergente;
- CAE sin efecto local;
- error PDF;
- compromiso de credenciales;
- rollback de versión.

### Continuidad

- backup cifrado;
- restore en entorno aislado;
- no restaurar credenciales productivas en testing;
- exportación de evidencia de auditoría.

### Criterio de salida

- simulacro de tres incidentes;
- alertas llegan al responsable;
- restore probado.

## 16. Fase 12 — Homologación integral

Duración: depende de disponibilidad y casos oficiales.

### Matriz mínima

#### Autenticación

- Ticket wsfe;
- Ticket constancia;
- expiración y renovación;
- certificado inválido;
- servicio no asociado.

#### Clientes

- CUIT válido;
- física/jurídica;
- distintas condiciones IVA;
- no encontrado;
- parcial;
- caché;
- duplicado/conflicto.

#### Facturas

- A 21 %;
- B 21 %;
- múltiples tasas si el negocio las usa;
- descuento;
- documento/condición incompatibles;
- correlatividad;
- observación;
- timeout/reconciliación.

#### Notas de crédito

- NCA total/parcial;
- NCB total/parcial;
- asociación inválida;
- exceso de saldo;
- efecto de stock.

#### PDF/QR

- cuatro tipos;
- verificación QR;
- transparencia;
- email.

#### Seguridad

- roles;
- multiempresa;
- redacción;
- secretos;
- producción bloqueada;
- auditoría.

### Evidencia

Por caso:

- ID;
- fecha;
- build/commit;
- emisor de homologación;
- request hash;
- códigos/resultados redactados;
- captura/PDF;
- resultado esperado/obtenido;
- aprobador.

### Criterio de salida

- 100 % de casos P0 aprobados;
- cero incidentes sin reconciliación;
- cero secretos/PII en logs;
- aprobación técnica;
- aprobación contable;
- aprobación del dueño.

## 17. Fase 13 — Preparación de producción

Estimación técnica: 3 a 5 días, más gestiones.

### Checklist

1. Certificado de producción.
2. Servicios asociados.
3. Punto de venta.
4. Secretos instalados.
5. Migraciones ensayadas.
6. Backup y restore.
7. Feature flags apagados.
8. Dashboard/alertas.
9. Personal capacitado.
10. Ventana aprobada.
11. Plan de rollback.
12. PDF legal aprobado.

### Migración sin afectar datos

1. backup verificado;
2. migraciones aditivas;
3. backfill en lotes;
4. conteos y hashes;
5. deploy con emisión apagada;
6. smoke tests;
7. habilitar consulta CUIT;
8. habilitar borradores;
9. habilitar emisión para una empresa/POS;
10. monitoreo;
11. ampliar.

No reordenar IDs de clientes, pedidos o productos. No reemplazar tablas; vincular por claves existentes.

## 18. Fase 14 — Activación controlada

### Canary

- una empresa;
- un punto de venta;
- usuarios de contabilidad;
- horario supervisado;
- límite operativo inicial;
- revisión de cada resultado.

### Rollback

Rollback seguro significa:

- desactivar nuevas emisiones;
- conservar documentos/CAE ya autorizados;
- mantener reconciliación;
- volver interfaz a modo lectura/borrador;
- no revertir migraciones destructivamente;
- no borrar intentos.

### Criterio de estabilización

- período acordado sin incertidumbres vencidas;
- numeración sincronizada;
- proyecciones completas;
- PDF/QR correctos;
- soporte puede seguir runbooks;
- contador concilia muestras.

## 19. Estrategia de pruebas automatizadas

### Unitarias

- validadores CUIT;
- mappers;
- totales;
- estados;
- redacción;
- canonicalización;
- parser;
- locks;
- decisiones de reintento.

### Contrato

- WSDL vigente;
- operaciones/campos esperados;
- `CondicionIVAReceptorId`;
- namespaces;
- campos opcionales 2026.

### Integración local

- PostgreSQL;
- Redis;
- Celery eager/worker real;
- doble worker;
- fallos inyectados.

### Homologación

- tests etiquetados, nunca en CI general;
- requieren credenciales externas;
- solo casos autorizados;
- evidencia redactada.

### End-to-end

- alta CUIT;
- pedido;
- borrador;
- emisión;
- PDF;
- cuenta/stock;
- nota de crédito.

## 20. Entregables por pull request

1. Modelos y migraciones.
2. Auth/secret baseline.
3. Transporte y contratos.
4. Parámetros/preflight.
5. Padrón.
6. Snapshot y dominio.
7. Numeración/reconciliación.
8. Emisión.
9. Efectos locales.
10. PDF/QR.
11. Notas de crédito.
12. UI/permisos.
13. Observabilidad/runbooks.

Cada PR:

- una responsabilidad;
- migración reversible si aplica;
- tests;
- documentación;
- sin secretos;
- feature flag;
- no activa producción.

## 21. Definición de terminado global

- [ ] CUIT completa datos oficiales con fuente/fecha.
- [ ] Duplicados/conflictos llegan a revisión manual.
- [ ] Factura A/B incluye condición IVA vigente.
- [ ] IVA se calcula desde precio neto.
- [ ] NCA/NCB se asocian correctamente.
- [ ] CAE vuelve el documento inmutable.
- [ ] Todo timeout se reconcilia antes de reenviar.
- [ ] Concurrencia no duplica.
- [ ] Numeración coincide con ARCA.
- [ ] PDF y QR oficial aprobados.
- [ ] Stock opcional se respeta.
- [ ] Cuenta corriente y stock son idempotentes.
- [ ] Roles mínimos.
- [ ] Secretos protegidos.
- [ ] Logs redactados.
- [ ] Backups/restores probados.
- [ ] Homologación completa.
- [ ] Producción requiere activación explícita.

## 22. Primer bloque de implementación recomendado

Orden inmediato cuando el usuario autorice código:

1. agregar tests que demuestren la ausencia de `CondicionIVAReceptorId`;
2. agregar estado `uncertain`;
3. reemplazar reintento ciego por reconciliación;
4. implementar `FECompConsultar`;
5. implementar parámetros de condición IVA;
6. recién después adaptar `FECAESolicitar`;
7. en paralelo lógico, no de emisión, implementar consulta CUIT.

No empezar por el PDF ni por producción: el principal riesgo está en el contrato vigente y la recuperación de estado.
