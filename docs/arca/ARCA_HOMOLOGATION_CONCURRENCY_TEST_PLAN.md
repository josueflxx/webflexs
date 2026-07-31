# Plan de pruebas de concurrencia e idempotencia ARCA

## Alcance

Este plan se ejecutará únicamente después de aprobar credenciales de
homologación. No autoriza uso de producción, certificados productivos ni
modificación de datos reales.

La primera pasada debe usar PostgreSQL aislado y dobles de WSAA/WSFE. La pasada
contra homologación se habilitará en una revisión posterior y sólo repetirá los
casos explícitamente aprobados.

## Preparación verificable

1. Crear una base PostgreSQL vacía y aplicar todas las migraciones.
2. Crear una empresa ficticia, un punto de venta de homologación y dos tipos de
   comprobante.
3. Confirmar que la integración está en `homologation`, nunca en `production`.
4. Registrar el estado inicial de la serie, documentos e intentos.
5. Ejecutar cada escenario con un `correlation_id` propio y conservar logs
   sanitizados, sin tokens ni certificados.

## Escenarios

### Misma operación concurrente

- Lanzar 20 solicitudes simultáneas con la misma `source_key` e
  `idempotency_key`.
- Sincronizar los workers con una barrera para que entren al mismo tiempo.
- Resultado esperado: un solo `FiscalDocument`, una sola operación activa y,
  como máximo, un despacho de autorización.
- Repetir después de resuelta la operación: debe devolverse el mismo documento
  y no emitirse otro comprobante.

### Numeración concurrente de una serie

- Lanzar 20 órdenes distintas contra el mismo CUIT, entorno, punto de venta y
  tipo de comprobante.
- Resultado esperado: números únicos, sin huecos creados por reintentos locales,
  y `next_number` igual al máximo reservado más uno.
- Verificar que no pueda insertarse manualmente una identidad
  `CUIT + entorno + punto de venta + tipo + número` duplicada.

### Fallo antes del despacho

- Inyectar un error antes de ejecutar el transporte.
- Resultado esperado: el intento queda como no despachado y la operación puede
  volver a `ready_to_issue` sin crear otro documento ni consumir otro número.

### Fallo después del despacho

- Simular respuesta autorizada y provocar un fallo al persistir el resultado.
- Resultado esperado: intento marcado como posiblemente despachado, documento
  en estado incierto y serie bloqueada.
- El siguiente paso debe ser exclusivamente consulta/recuperación; nunca una
  segunda autorización.

### Resultado incierto y recuperación

- Simular timeout después de enviar y luego una consulta que devuelve el
  comprobante autorizado.
- Resultado esperado: transición a `recovered_authorized`, CAE persistido y
  ningún segundo despacho.
- Simular también “no encontrado”: debe respetar el límite de consultas y pasar
  a revisión manual sin reemitir automáticamente.

### Reinicio de worker

- Detener el worker entre la marca de despacho y la persistencia final.
- Reiniciar y procesar la tarea nuevamente.
- Resultado esperado: la clave idempotente identifica la operación previa y el
  flujo entra en recuperación.

## Evidencia a conservar

- Conteo de documentos por `source_key` e `idempotency_key`.
- Conteo de intentos por documento, operación y número de intento.
- Identidades fiscales y números asignados.
- Estado y versión de la serie antes y después.
- Timestamps de preparación, despacho, resolución y recuperación.
- Hashes de snapshot y payload.
- Resultado de constraints e `IntegrityError` esperados.
- Logs sanitizados correlacionados por UUID.

## Criterios de aprobación

- Cero documentos duplicados.
- Cero números fiscales duplicados.
- Cero reemisiones después de un despacho posible o confirmado.
- Toda operación incierta entra en consulta o revisión manual.
- Los 20 callers de una misma operación convergen en un único documento.
- La serie conserva monotonía y queda bloqueada ante cualquier ambigüedad.
- Los tests pasan en PostgreSQL al menos tres veces consecutivas.
- La ejecución de homologación, cuando se autorice, coincide con la evidencia
  obtenida usando mocks.

## Implementación recomendada

Agregar un `TransactionTestCase` exclusivo de PostgreSQL con conexiones por
thread, `threading.Barrier` y cierre explícito de cada conexión. Los dobles de
WSFE deben contar llamadas de autorización y consulta por separado. El test
debe fallar si el backend es SQLite para evitar una falsa validación de
locking.
