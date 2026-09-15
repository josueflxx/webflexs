# ARCA — cierre de Fase 1 offline

Fecha: 2026-08-11  
Alcance: preparación local de código y pruebas, sin tráfico hacia ARCA.  
Estado: implementación offline completa; configuración runtime local todavía cerrada.

## Veredicto

WebFlexs conserva la integración directa Django/Python y ahora dispone de un proveedor de padrón separado para `ws_sr_constancia_inscripcion`. El cliente normaliza `getPersona_v2`, tolera secciones opcionales y distingue `FOUND`, `NOT_FOUND`, `INACTIVE` y `PARTIAL`. No se agregó Node.js, AFIP SDK ni un segundo runtime.

La preparación funcional offline está aprobada por tests. El entorno local real continúa deshabilitado: el doctor devuelve `WAITING_FOR_USER` y las compuertas de WSFE y padrón devuelven `FAIL` porque las variables privadas/locales y Redis no están configurados. Esto es un cierre seguro, no un fallo de código.

No se ejecutaron DNS, WSDL remoto, WSAA, Ticket de Acceso, `FEDummy`, `dummy`, `FEParamGetPtosVenta`, `getPersona_v2`, `FECompUltimoAutorizado`, `FECAESolicitar` ni deploy.

## Arquitectura implementada

- `TaxpayerRegistryProvider`: contrato de consulta de contribuyentes.
- `FiscalVoucherProvider`: contrato fiscal limitado a lecturas en esta etapa; no expone autorización de comprobantes.
- `DirectArcaTaxpayerRegistryProvider`: adapter directo del padrón.
- `DirectArcaWsfeReadProvider`: adapter del cliente WSFE existente.
- `ArcaWsaaSessionMixin`: firma TRA, login WSAA y parsing de TA reutilizados por ambos clientes.
- Cada cliente solicita su TA con su propio `service_id`.
- La identidad de caché ya discrimina CUIT emisor, ambiente, servicio y fingerprint de credencial, por lo que `wsfe` nunca comparte Ticket/Sign con `ws_sr_constancia_inscripcion`.

## Configuración cerrada

La allowlist sólo incorpora el runtime de homologación del padrón:

- servicio WSAA: `ws_sr_constancia_inscripcion`;
- runtime: `https://awshomo.arca.gob.ar/sr-padron/webservices/personaServiceA5`;
- WSDL esperado: el mismo valor con `?WSDL`;
- operación: `getPersona_v2(token, sign, cuitRepresentada, idPersona)`;
- infraestructura: `dummy()` sin Token/Sign.

No se incorporó un endpoint productivo de padrón. El valor externo aceptado para el ambiente sigue siendo exactamente `homologacion`; producción y emisión deben permanecer en falso.

La compuerta del padrón reutiliza los controles generales de homologación, credenciales, redacción y caché compartida, pero no depende de punto de venta, tipo de comprobante, endpoint WSFE ni `service_id=wsfe`. Así se mantienen desacoplados los dos contratos.

## Normalización de padrón

`TaxpayerLookupResult` conserva:

- CUIT, tipo de persona, estado de clave y nombre de presentación;
- nombre, apellido y razón social por separado;
- domicilio fiscal;
- datos generales y régimen general estructurados;
- impuestos, actividades, monotributo y caracterizaciones;
- advertencias independientes;
- fuente, esquema y hash SHA-256 de la respuesta, sin XML crudo ni credenciales.

Reglas:

- `NOT_FOUND`: `errorConstancia` contiene “No existe persona con ese Id”.
- `INACTIVE`: datos válidos con `estadoClave=INACTIVO`.
- `PARTIAL`: existe información general válida y uno o más errores de sección.
- Un inactivo también puede llevar `is_partial=true` y advertencias.
- XML inválido, SOAP Fault, error de transporte, error WSAA o CUIT devuelta distinta usan el canal técnico.
- No se escribe ni sobrescribe un `Customer`; la adopción de datos queda fuera de este cliente.

## Comandos preparados

- `arca_taxpayer_homologation_gate`: sólo inspección offline; no resuelve DNS ni abre sockets.
- `arca_taxpayer_readonly_probe --company-id ... --taxpayer-cuit ...`: preparado para una autorización futura. Tiene argumentos cerrados, ejecuta `dummy` antes de `getPersona_v2`, enmascara la CUIT, no imprime datos personales ni Token/Sign y limpia el TA exacto al terminar.
- El probe existente de WSFE ya usa `FEParamGetPtosVenta` como catálogo read-only y el adapter fiscal expone `fetch_points_of_sale`. La respuesta oficial deberá ser la fuente de verdad; los dos POS locales no se consideran confirmados.

No se ejecutó ninguno de los probes.

## Redis y Ticket de Acceso

Para una prueba futura se requiere Redis compartido mediante la caché de Django. LocMem, archivo y base de datos siguen rechazados. La clave lógica del TA incorpora:

`CUIT emisor | ambiente | service_id | fingerprint de credencial`

La renovación usa singleflight y margen de vencimiento. El probe elimina únicamente su clave exacta; no enumera ni borra otras entradas. Token y Sign no se guardan en archivos, documentación ni logs.

## Verificación offline

- `manage.py check`: aprobado; sólo informa que ARCA está deshabilitada por defecto.
- `makemigrations --check --dry-run`: sin cambios.
- Suite dirigida: 139 casos; 131 aprobados y 8 omitidos por requerir PostgreSQL.
- Tests nuevos de padrón: 20 aprobados.
- Doctor local real: `WAITING_FOR_USER`.
- Gate WSFE local real: `FAIL` seguro.
- Gate padrón local real: `FAIL` seguro.
- Red ARCA: no utilizada.

Los tests cubren allowlists, flags ambiguos, desacoplamiento WSFE/padrón, fixtures `FOUND`/`NOT_FOUND`/`INACTIVE`/`PARTIAL`, campos opcionales, fallos técnicos, estructura del request, ausencia de secretos en resultados, separación multi-service de TA y comandos con red bloqueada.

## Configuración final esperada

Los nombres y valores públicos están documentados en `docs/arca/arca-homologacion.env.example`. Para que doctor y gates puedan aprobar, el entorno privado deberá aportar sin compartirlos por chat:

- alias de credencial, CUIT emisor y rutas absolutas al `.crt`/`.key` fuera del checkout;
- Redis local/privado alcanzable y prefijo exclusivo;
- empresa local seleccionada;
- punto de venta y tipo sólo para el gate WSFE;
- flags read-only explícitos y ambas autorizaciones WSASS confirmadas;
- endpoints/servicios exactos allowlisted;
- emisión y producción en falso.

El certificado y la key indicados por el usuario permanecen fuera del repositorio. Esta ejecución no copió, imprimió ni documentó su contenido.

## Pendientes antes de pedir autorización de red

1. Configurar el entorno local privado sin commitearlo.
2. Confirmar que Redis está disponible para el proceso Django.
3. Ejecutar doctor, gate WSFE y gate padrón hasta obtener `PASS` con salida sanitizada.
4. Confirmar la empresa local que representa a la CUIT autorizada.
5. Definir una CUIT de prueba permitida para `getPersona_v2` sin publicarla innecesariamente.
6. Pedir una autorización explícita y acotada para el primer probe read-only.

## Decisión de avance

Todavía no es seguro ejecutar ni pedir como acción inmediata el primer probe: la configuración real devuelve `WAITING_FOR_USER`/`FAIL`. Sí es seguro completar manualmente la configuración privada y repetir sólo los diagnósticos offline. `FECAESolicitar` continúa prohibido y bloqueado.

