# Plan de prueba controlada ARCA — no ejecutar todavía

Fecha: 2026-07-28  
Estado: diseño. Ninguna etapa externa fue ejecutada durante la auditoría.

## Reglas inviolables

- Usar sólo CUIT, certificado, clave, punto de venta, receptores y endpoints de **homologación**.
- Ejecutar desde backend aislado con `ARCA_ALLOW_PRODUCTION=false`, egress limitado por allowlist y una base de datos de prueba sin clientes, ventas ni stock reales.
- No guardar certificados, clave, token, sign ni XML de autenticación en Git, consola, capturas o artefactos.
- Etapas secuenciales con aprobación del responsable; detenerse ante cualquier duda de ambiente, correlatividad o resultado.
- No habilitar `FECAESolicitar` hasta cerrar BLK-01 a BLK-06 de `ARCA_BLOCKERS.md`.

## Etapa 0 — prueba local offline

| Campo | Definición |
|---|---|
| Precondiciones | Branch/commit identificados; DB temporal; sin egress a ARCA; fixtures SOAP sin datos reales; secretos ausentes. |
| Acción | Ejecutar `python manage.py check`, `python -m pip check` y la futura suite `test_arca_offline`; probar construcción/parseo de TRA, CMS mediante certificado efímero, requests/responses WSFE, permisos, cálculo, concurrencia e idempotencia. |
| Esperado | Todo pasa sin red ni credenciales; snapshots no contienen secretos; una operación incierta nunca se reenvía. |
| Evidencia | Commit, versión de Python/OpenSSL, salida de tests, hashes de fixtures, matriz de escenarios. |
| Detener | Falla de sanitización, host productivo presente, request sin campos vigentes, cálculo no reconciliado o carrera. |
| Recuperación | Corregir offline, borrar únicamente la DB/credenciales efímeras y repetir desde cero. |

## Etapa 1 — conectividad

| Campo | Definición |
|---|---|
| Precondiciones | Allowlist de hosts de homologación revisada por dos personas; DNS/TLS/NTP monitorizados; egress de producción denegado; no se carga todavía ninguna venta. |
| Acción | Desde el runtime backend resolver DNS, abrir TLS y realizar una comprobación HTTP/WSDL o `FEDummy` únicamente contra homologación, con timeout corto. No pedir TA ni CAE. |
| Esperado | DNS y cadena TLS válidos; latencia/timeout registrados; host final pertenece a homologación; `FEDummy` informa infraestructura disponible si se usa. |
| Evidencia | Timestamp UTC, host/IP, emisor/huella pública del certificado TLS, versión TLS, latencia y respuesta sanitizada de `FEDummy`. |
| Detener | Redirección, DNS o certificado inesperado; URL productiva; reloj fuera de tolerancia; respuesta no SOAP. |
| Recuperación | Cerrar egress, revisar DNS/proxy/configuración; no avanzar por un fallback de URL. |

## Etapa 2 — autenticación WSAA

| Campo | Definición |
|---|---|
| Precondiciones | Certificado y clave exclusivos de homologación montados read-only fuera del repo; correspondencia clave/certificado, vigencia, CUIT, CEE y relación `wsfe` confirmadas; logs redacted; lock de renovación activo. |
| Acción | Mediante un comando de gestión dedicado y auditable: generar TRA con `service=wsfe`, firmarlo CMS/PKCS#7 y llamar una sola vez a WSAA homologación. Este comando no debe importar ni poder invocar la autorización de comprobantes. |
| Esperado | TA parseable con token/sign no vacíos, generación y vencimiento coherentes; sólo backend los conserva cifrados/en caché hasta antes del vencimiento. |
| Evidencia | ID de ejecución, timestamp, servicio, CUIT parcialmente enmascarado, serial/huella pública y vencimiento del certificado, expiración del TA y hash del TRA; nunca token/sign/XML crudo. |
| Detener | Error de firma/reloj/autorización; token en log; certificado no-homologación; más de una renovación concurrente. |
| Recuperación | Invalidar caché de forma segura, revisar reloj/relación/certificado y repetir sólo WSAA con nueva aprobación. |

## Etapa 3 — consultas WSFEv1

| Campo | Definición |
|---|---|
| Precondiciones | Etapa 2 aprobada; cliente implementa y prueba `FEDummy`, `FEParamGetPtosVenta`, `FEParamGetCondicionIvaReceptor`, tablas necesarias, `FECompUltimoAutorizado` y `FECompConsultar`. |
| Acción | Consultar estado, parámetros fiscales vigentes, puntos de venta, último autorizado por cada tipo planificado y un comprobante de prueba conocido si existe. No llamar `FECAESolicitar`. |
| Esperado | Punto de venta/CUIT/tipos consistentes; tablas almacenadas con fecha/origen; último número reconciliado; consulta inexistente se distingue de error técnico. |
| Evidencia | Request normalizado sin Auth, response sanitizada, códigos/observaciones/eventos separados, tabla/fecha de vigencia y resultado de correlatividad. |
| Detener | Punto de venta ausente, tipo no habilitado, CUIT inconsistente, parámetros incompatibles, consulta fallida o discrepancia no explicada. |
| Recuperación | Resolver alta/relación fuera del sistema o reparar mapeos offline; jamás “forzar” la serie local. |

## Etapa 4 — primera autorización en homologación

| Campo | Definición |
|---|---|
| Precondiciones | Todos los bloqueos cerrados; backup de DB de prueba; emisor/receptor ficticio permitido y matriz IVA confirmados; serie reconciliada inmediatamente antes; UI/command muestra `HOMOLOGACIÓN / SIN VALIDEZ FISCAL`; aprobación explícita del administrador. |
| Acción | Crear un pedido mínimo de prueba, congelar snapshot, recalcular backend, mostrar payload sanitizado, registrar idempotency key, enviar **una** solicitud `FECAESolicitar`, persistir intento/resultado y ejecutar `FECompConsultar` de verificación. |
| Esperado | Estado autorizado o autorizado con observaciones; número/CAE/vencimiento coinciden con consulta; PDF desde snapshot con marca de homologación. Un rechazo se conserva y no se reintenta automáticamente. |
| Evidencia | ID interno, actor, ambiente, CUIT enmascarado, punto/tipo/número, hash y versión del payload, timestamps UTC, request/response sanitizados, Errors/Obs/Events, CAE en la DB protegida, consulta posterior y PDF marcado. |
| Detener | Ambiente/host dudoso, cambio de último número, totales no reconciliados, doble ejecución, timeout, respuesta parcial/ininterpretable o falta de persistencia atómica. |
| Recuperación | Aplicar exclusivamente el protocolo de resultado incierto siguiente. |

## Recuperación obligatoria ante timeout o respuesta perdida

1. Persistir el intento como `resultado_incierto`; bloquear emisión y reintento manual/automático para esa serie.
2. Con el mismo `(CUIT, punto, tipo, número)`, llamar `FECompConsultar`.
3. Si existe autorizado: importar y verificar CAE, vencimiento, importes y receptor; cerrar como recuperado, sin reenviar.
4. Si ARCA confirma de forma inequívoca que no existe: consultar `FECompUltimoAutorizado`, reconciliar y sólo entonces permitir un reenvío aprobado que reutilice exactamente la misma clave/payload.
5. Si la consulta falla o la evidencia es contradictoria: mantener bloqueado, escalar al administrador/soporte y no emitir comprobantes posteriores de esa serie.
6. Guardar cada consulta/decisión como evento append-only con actor, UTC y correlation ID.

## Matriz mínima offline antes de abrir la red

- WSAA: TRA válido, reloj adelantado/atrasado, cert vencido, clave incorrecta, servicio no asociado, parseo y renovación concurrente.
- WSFE: A/B y NCA/NCB según decisión; 21 %, 10,5 %, mixto, 0 %, descuento, decimal, redondeo, cero/negativo rechazado o admitido según regla.
- Respuestas: autorizado, autorizado con observaciones, rechazado, SOAP Fault, Events, HTTP 4xx/5xx, malformed XML, timeout antes/después del procesamiento.
- Integridad: dos vendedores simultáneos, doble clic, dos workers, caída después de enviar y antes de persistir, discrepancia de serie, reejecución con misma idempotency key.
- Seguridad: acceso por vendedor/admin, CSRF, redacción de Auth, imposibilidad de editar/borrar autorizado, host productivo rechazado.

## Resultado de pruebas durante esta auditoría

- `python manage.py check`: aprobado.
- `python -m pip check`: aprobado; hubo advertencia local por una distribución inválida `~jango`.
- 19 pruebas focalizadas de reglas fiscales locales, stock post-CAE, aislamiento por empresa, redacción, CUIT, snapshots, tipos y plantilla: aprobadas.
- Suite completa `python manage.py test -v 1`: **NO SE PUDO VERIFICAR**; excedió 10 minutos sin resultado final.
- Escáner de vulnerabilidades: **NO SE PUDO VERIFICAR**; `pip-audit` no está instalado.
- Pruebas externas WSAA/WSFE: no ejecutadas por diseño y por ausencia de credenciales utilizables.

