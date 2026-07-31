# Investigación oficial vigente de ARCA

Fecha de verificación: 24 de julio de 2026
Fuentes: exclusivamente sitios oficiales de ARCA/AFIP y Argentina.gob.ar.

## 1. Resultado de la verificación

Para el alcance de FLEXS se deben usar:

- WSAA para autenticación y autorización;
- `wsfev1` para Factura A/B y Nota de Crédito A/B;
- `ws_sr_constancia_inscripcion` para alta de clientes por CUIT;
- `ws_sr_padron_a4` solo como complemento si la constancia no cubre un dato necesario;
- QR versión 1 con URL oficial de ARCA.

No se debe usar el antiguo `wsfe` V0. La página oficial indica que `wsfev1` lo reemplazó y soporta comprobantes A, B, C y M sin detalle de ítems. FLEXS mantendrá su alcance inicial en A, B, NCA y NCB.

## 2. WSAA

### Identificadores de servicio

| Servicio de negocio | Valor de `<service>` en TRA |
|---|---|
| `wsfev1` | `wsfe` |
| Constancia por CUIT | `ws_sr_constancia_inscripcion` |
| Padrón A4 | `ws_sr_padron_a4` |

El manual oficial de `wsfev1` indica expresamente que el tag `service` para obtener el Ticket de Acceso es `wsfe`.

### Endpoints verificados

| Ambiente | Endpoint WSAA |
|---|---|
| Homologación | `https://wsaahomo.afip.gov.ar/ws/services/LoginCms` |
| Producción | `https://wsaa.afip.gov.ar/ws/services/LoginCms` |

Ambos endpoints respondieron HTTP 200 durante la verificación. ARCA documenta que los certificados de testing se gestionan por WSASS y los de producción por “Administración de Certificados Digitales”, con posterior asociación al Web Service de negocio.

### Reglas técnicas

- Generar un `loginTicketRequest` por servicio.
- Firmarlo como CMS `SignedData` con certificado X.509 y clave privada.
- Codificar el CMS en Base64.
- Invocar `loginCms`.
- Cachear token y firma hasta antes del vencimiento.
- Mantener Tickets separados por emisor, ambiente y servicio.
- No persistir token o firma en logs.
- Sincronizar reloj del servidor.

El manual de `wsfev1` informa una duración de Ticket de Acceso de 12 horas. La aplicación no debe asumir que todos los servicios tienen idéntica duración: debe obedecer `expirationTime`.

Fuentes:

- [Documentación oficial de WSAA](https://www.afip.gob.ar/ws/documentacion/wsaa.asp)
- [Especificación técnica WSAA 1.2.2](https://www.afip.gob.ar/ws/WSAA/Especificacion_Tecnica_WSAA_1.2.2.pdf)
- [Certificados para homologación y producción](https://www.afip.gob.ar/ws/documentacion/certificados.asp)

## 3. wsfev1

### Endpoints y WSDL verificados

| Ambiente | Servicio | WSDL |
|---|---|---|
| Homologación | `https://wswhomo.afip.gov.ar/wsfev1/service.asmx` | `https://wswhomo.afip.gov.ar/wsfev1/service.asmx?WSDL` |
| Producción | `https://servicios1.afip.gov.ar/wsfev1/service.asmx` | `https://servicios1.afip.gov.ar/wsfev1/service.asmx?WSDL` |

Los dos WSDL respondieron HTTP 200 y publican, entre otros:

- `FEDummy`;
- `FECAESolicitar`;
- `FECompConsultar`;
- `FECompUltimoAutorizado`;
- `FECompTotXRequest`;
- `FEParamGetPtosVenta`;
- `FEParamGetTiposCbte`;
- `FEParamGetTiposDoc`;
- `FEParamGetTiposIva`;
- `FEParamGetCondicionIvaReceptor`.

Fuentes:

- [Servicio wsfev1 de homologación](https://wswhomo.afip.gov.ar/wsfev1/service.asmx)
- [WSDL wsfev1 de homologación](https://wswhomo.afip.gov.ar/wsfev1/service.asmx?WSDL)
- [WSDL wsfev1 de producción](https://servicios1.afip.gov.ar/wsfev1/service.asmx?WSDL)
- [Manual vigente de wsfev1](https://www.afip.gob.ar/fe/ayuda/documentos/wsfev1-RG-4291.pdf)
- [Página oficial de Web Services de factura electrónica](https://www.afip.gob.ar/ws/documentacion/ws-factura-electronica.asp)

### Métodos mínimos de FLEXS

#### `FEDummy`

Prueba el estado de AppServer, DbServer y AuthServer. Sirve para salud técnica, no valida certificado ni permisos del emisor.

#### `FEParamGetPtosVenta`

Debe usarse en preflight para comprobar que el punto de venta esperado exista y esté habilitado.

#### `FEParamGetCondicionIvaReceptor`

Devuelve `Id`, descripción y clase de comprobante admitida. El catálogo se debe cachear con versión/fecha y no codificar a ciegas valores sin validación.

#### `FECompUltimoAutorizado`

Devuelve el último número autorizado para CUIT, punto de venta y tipo de comprobante. Es una fuente de verdad para numeración y reconciliación, pero no reemplaza `FECompConsultar`.

#### `FECompConsultar`

Consulta un comprobante puntual por tipo, número y punto de venta y devuelve, entre otros, resultado, código de autorización, tipo de emisión, vencimiento y observaciones. Es obligatorio en el algoritmo local de estado incierto.

#### `FECAESolicitar`

Solicita CAE para uno o más comprobantes. FLEXS comenzará con un comprobante por request para simplificar idempotencia, trazabilidad y recuperación.

### Tipos de comprobante del alcance

| Documento | `CbteTipo` |
|---|---:|
| Factura A | 1 |
| Nota de Débito A | 2 |
| Nota de Crédito A | 3 |
| Factura B | 6 |
| Nota de Débito B | 7 |
| Nota de Crédito B | 8 |

La primera entrega solo habilitará 1, 3, 6 y 8. Las notas de débito quedan fuera hasta una decisión funcional posterior.

### Documento receptor

| Tipo | Código usual |
|---|---:|
| CUIT | 80 |
| CUIL | 86 |
| CDI | 87 |
| Pasaporte | 94 |
| DNI | 96 |
| Otro | 99 |

Los códigos deben refrescarse por `FEParamGetTiposDoc`; la tabla local es un valor inicial, no la autoridad.

### IVA

Alícuotas usadas por el código actual:

| Alícuota | ID |
|---:|---:|
| 0 % | 3 |
| 10,5 % | 4 |
| 21 % | 5 |
| 27 % | 6 |
| 5 % | 8 |
| 2,5 % | 9 |

Antes de emitir se debe comparar contra `FEParamGetTiposIva`. Una tasa desconocida debe bloquear la emisión.

### Condición IVA del receptor: cambio obligatorio

La Resolución General 5616/2024 dispuso que los comprobantes electrónicos identifiquen la condición frente al IVA del cliente. La norma fijó el uso obligatorio de la nueva versión de Web Service desde el 15 de abril de 2025.

El WSDL vigente contiene:

- campo `CondicionIVAReceptorId` dentro de la solicitud;
- operación `FEParamGetCondicionIvaReceptor`.

Por tanto, cualquier request de FLEXS que no incluya este campo está desactualizado y no debe llegar a homologación como candidato válido.

Fuentes:

- [Resolución General 5616/2024, texto oficial](https://biblioteca.afip.gob.ar/search/query/norma.aspx?p=t%3ARAG%7Cn%3A5616%7Co%3A9%7Ca%3A2024%7Cf%3A17%2F12%2F2024)
- [Operación FEParamGetCondicionIvaReceptor en homologación](https://wswhomo.afip.gov.ar/wsfev1/service.asmx?op=FEParamGetCondicionIvaReceptor)

### Correlatividad y estado incierto

ARCA exige correlatividad por punto de venta y tipo. El flujo seguro es:

1. consultar el último autorizado;
2. bloquear la serie local y la clave distribuida;
3. construir un request canónico;
4. enviar;
5. si la respuesta es concluyente, persistirla;
6. si la conexión se interrumpe, marcar `uncertain`;
7. consultar el comprobante puntual;
8. recién entonces decidir si corresponde reenviar.

La operación oficial `FECompConsultar` existe específicamente para consultar el comprobante emitido y su código:

- [FECompConsultar en homologación](https://wswhomo.afip.gov.ar/wsfev1/service.asmx?op=FECompConsultar)
- [FECompUltimoAutorizado en homologación](https://wswhomo.afip.gov.ar/wsfev1/service.asmx?op=FECompUltimoAutorizado)

## 4. Consulta de constancia de inscripción

### Servicio principal

Identificador WSAA: `ws_sr_constancia_inscripcion`.

El catálogo oficial informa que reemplaza al deprecado `ws_sr_padron_a5`. El método elegido es `getPersona_v2`; `getPersonaList_v2` admite consultas múltiples, pero no es necesario para el alta interactiva inicial.

### Endpoints y WSDL verificados

| Ambiente | Servicio | WSDL |
|---|---|---|
| Homologación | `https://awshomo.afip.gov.ar/sr-padron/webservices/personaServiceA5` | `https://awshomo.afip.gov.ar/sr-padron/webservices/personaServiceA5?WSDL` |
| Producción | `https://aws.afip.gov.ar/sr-padron/webservices/personaServiceA5` | `https://aws.afip.gov.ar/sr-padron/webservices/personaServiceA5?WSDL` |

Los dos WSDL respondieron HTTP 200 y publican:

- `dummy`;
- `getPersona`;
- `getPersona_v2`;
- `getPersonaList`;
- `getPersonaList_v2`.

El nombre técnico del endpoint conserva `personaServiceA5` por compatibilidad; no se debe solicitar a WSAA el servicio deprecado. El ID correcto continúa siendo `ws_sr_constancia_inscripcion`.

### Solicitud `getPersona_v2`

Campos:

- `token`;
- `sign`;
- `cuitRepresentada`;
- `idPersona`.

`cuitRepresentada` es el CUIT del emisor/representado autorizado y `idPersona` es el CUIT consultado.

### Datos de respuesta relevantes

La respuesta puede incluir:

- metadata de procesamiento;
- CUIT, tipo de clave, estado de clave y tipo de persona;
- nombre y apellido para persona humana;
- razón social para persona jurídica;
- mes de cierre;
- domicilio fiscal;
- dependencia;
- caracterizaciones;
- impuestos y regímenes de régimen general;
- actividades;
- datos de monotributo, categoría y actividades;
- errores separados de constancia, régimen general y monotributo.

No todos los nodos tienen multiplicidad uno. El parser debe tolerar omisiones, múltiples actividades/caracterizaciones y extensiones compatibles.

### Cambio vigente en 2026

El catálogo oficial informa que desde el 11 de febrero de 2026 `getPersona_v2` incorpora el tag opcional `fechaSolicitud` dentro de caracterización. El parser de FLEXS debe ignorar de forma segura campos desconocidos y conservar la respuesta normalizada/versionada.

Fuentes:

- [Catálogo oficial de Web Services, sección constancia](https://www.afip.gob.ar/ws/documentacion/catalogo.asp)
- [Manual vigente de constancia de inscripción](https://www.afip.gob.ar/ws/WSCI/manual_ws_sr_ws_constancia_inscripcion.pdf)
- [WSDL de homologación](https://awshomo.afip.gov.ar/sr-padron/webservices/personaServiceA5?WSDL)
- [WSDL de producción](https://aws.afip.gov.ar/sr-padron/webservices/personaServiceA5?WSDL)

## 5. Padrón A4

Identificador WSAA: `ws_sr_padron_a4`.

ARCA lo describe como acceso a situación tributaria, incluyendo impuestos y regímenes. Se mantiene como consulta complementaria, no como primera opción, para evitar complejidad y duplicación de datos.

Fuente:

- [Catálogo y manual oficial de Padrón A4](https://www.afip.gob.ar/ws/documentacion/catalogo.asp)
- [Manual Padrón A4 v1.3](https://www.afip.gob.ar/ws/ws_sr_padron_a4/manual_ws_sr_padron_a4_v1.3.pdf)

## 6. QR oficial

La especificación vigente define:

```text
https://www.arca.gob.ar/fe/qr/?p={JSON_BASE64}
```

JSON versión 1:

- `ver`;
- `fecha`;
- `cuit`;
- `ptoVta`;
- `tipoCmp`;
- `nroCmp`;
- `importe`;
- `moneda`;
- `ctz`;
- `tipoDocRec`, si corresponde;
- `nroDocRec`, si corresponde;
- `tipoCodAut`, `E` para CAE;
- `codAut`.

El QR se genera solo con un documento autorizado y debe derivarse del snapshot final persistido. No debe construirse solo con el CAE.

Fuente:

- [Especificaciones oficiales del QR](https://www.afip.gob.ar/fe/qr/documentos/QRespecificaciones.pdf)
- [Micrositio oficial del QR](https://www.afip.gob.ar/fe/qr/)

## 7. Representación impresa y transparencia fiscal

Además de los requisitos ordinarios de emisor, receptor, tipo, numeración, fecha, importes y autorización, el PDF debe revisarse con un profesional contable para incorporar correctamente el Régimen de Transparencia Fiscal al Consumidor cuando corresponda.

ARCA informa que los comprobantes deben detallar el componente impositivo y la leyenda correspondiente. Esta regla afecta la representación del comprobante, no solo el XML de autorización.

Fuentes:

- [Transparencia fiscal al consumidor](https://servicioscf.afip.gob.ar/publico/sitio/contenido/novedad/ver.aspx?id=4709)
- [Normativa de facturación](https://www.afip.gob.ar/facturacion/ayuda/normativa.asp)

## 8. Datos personales

La consulta por CUIT y la facturación tratan datos personales y tributarios. La Ley 25.326 exige calidad, finalidad, seguridad y confidencialidad, además de medidas técnicas y organizativas para evitar acceso, modificación o tratamiento no autorizado.

Aplicación concreta:

- recolectar solo datos necesarios;
- informar finalidad;
- limitar acceso;
- mantener exactitud y fecha de consulta;
- permitir corrección;
- definir retención;
- no registrar token, firma ni clave privada;
- auditar consultas sin volcar la respuesta completa en logs.

Fuente:

- [Ley 25.326, texto actualizado](https://www.argentina.gob.ar/normativa/nacional/ley-25326-64790/actualizacion)

## 9. Hallazgos que requieren confirmación de homologación

Aunque las URLs y contratos publicados fueron verificados, estos puntos solo se consideran cerrados después de pruebas con credenciales de homologación:

- habilitación concreta de cada CUIT representado;
- permisos asignados a `wsfe`, `ws_sr_constancia_inscripcion` y, si aplica, A4;
- punto de venta disponible;
- códigos de condición IVA admitidos para A y B;
- casos de prueba de CUIT;
- códigos exactos de error operativos;
- comportamiento ante reenvío del mismo comprobante;
- contenido del PDF validado por contador.

## 10. Regla de mantenimiento documental

Antes de cada liberación fiscal:

1. comparar hash/fecha del WSDL de homologación y producción;
2. revisar el manual vigente enlazado desde el catálogo, no una copia antigua;
3. revisar novedades del catálogo de Web Services;
4. ejecutar contract tests;
5. actualizar esta fecha de corte y registrar cambios.
