# Auditoría del proyecto para integración con ARCA

Fecha de corte: 24 de julio de 2026
Alcance: análisis local del repositorio. No se accedió a producción, no se emitieron comprobantes y no se usaron credenciales reales.

## 1. Dictamen ejecutivo

El proyecto no es Firebase ni una SPA desacoplada: es un monolito Django con plantillas server-side, PostgreSQL en producción, Redis, Celery, Gunicorn y Nginx. Ya existe una implementación parcial de WSAA y `wsfev1`, además de modelos fiscales, estados, PDF, QR, permisos y tareas de reintento.

La base es aprovechable, pero el emisor actual no debe pasar a homologación real sin corregir primero estos bloqueos:

1. No envía `CondicionIVAReceptorId`, obligatorio para `wsfev1` desde el 15 de abril de 2025.
2. Un timeout o un proceso detenido en `submitting` termina en un reenvío automático sin consultar antes `FECompConsultar`.
3. La numeración remota se sincroniza solo según una política configurable cuyo valor por defecto es `first`; para emisión real falta un protocolo de reserva, reconciliación y bloqueo distribuido.
4. No existe la consulta oficial de clientes por CUIT. El endpoint actual devuelve datos vacíos y marca la verificación oficial como pendiente.
5. La configuración de certificados usa rutas provistas por JSON de entorno, pero no hay un componente de ciclo de vida de credenciales, rotación, alertas de vencimiento ni secreto administrado.
6. El QR usa todavía el dominio histórico `www.afip.gob.ar`; la especificación publicada actualmente usa `https://www.arca.gob.ar/fe/qr/`.

Conclusión: el proyecto está en una fase de prototipo fiscal avanzado, no en una fase segura para emitir comprobantes oficiales.

## 2. Estado del repositorio auditado

- Rama: `codex/production-fiscal-client-upgrade-20260723`.
- Commit base observado: `1ce43cd`.
- El árbol de trabajo ya contiene numerosos cambios del usuario y trabajos previos. Esta auditoría no los modifica.
- No se detectaron certificados ni claves privadas seguidos por Git.
- `.gitignore` excluye `*.key`, `*.pem`, `*.p12`, `*.pfx`, `*.crt` y directorios de certificados.
- Existe un cliente real en `core/services/arca_client.py` y un segundo stub en `core/integrations/arca/client.py`; mantener ambos genera ambigüedad.

## 3. Stack real

| Área | Implementación actual | Observación para ARCA |
|---|---|---|
| Aplicación | Django 5, plantillas server-side | Correcto para mantener toda comunicación SOAP en backend |
| API | Django REST Framework | Útil para endpoints internos; no debe exponer token, firma ni XML sensible |
| Base de datos | PostgreSQL en producción; SQLite local | PostgreSQL permite bloqueos transaccionales por fila |
| Caché | Redis obligatorio en producción | Apto para Ticket de Acceso y locks, pero hace falta diseño explícito |
| Tareas | Celery con Redis y Celery Beat | Apto para reconciliación y reintentos controlados |
| Servidor | Gunicorn detrás de Nginx | Adecuado; se debe separar timeout HTTP del estado fiscal |
| PDF | WeasyPrint | Reutilizable |
| QR | `qrcode` | Reutilizable corrigiendo URL, snapshot y validaciones |
| Observabilidad | Logging y Sentry opcional sin PII por defecto | Falta telemetría fiscal estructurada y alertas |
| Despliegue | VPS, Git pull, migraciones, `collectstatic`, systemd | No hay una plataforma administrada de secretos |

Dependencias relevantes: Django, DRF, Celery, Redis, PostgreSQL, WeasyPrint y qrcode. No hay una librería SOAP declarada; el cliente actual arma XML con cadenas y usa `urllib`.

## 4. Arquitectura actual observada

```text
Panel Django
   |
   +-- Pedido / cliente / empresa
   |
   +-- FiscalDocument en PostgreSQL
           |
           +-- Celery -> fiscal_emission.py
                          |
                          +-- arca_client.py
                                  +-- OpenSSL CMS
                                  +-- WSAA
                                  +-- wsfev1
           |
           +-- PDF/QR local
           +-- cuenta corriente
           +-- movimientos de stock posteriores al CAE
```

La separación conceptual entre pedido, documento comercial interno y documento fiscal es correcta. También es correcta la decisión de aplicar stock luego de obtener CAE y de no permitir modificar un comprobante autorizado.

## 5. Inventario fiscal existente

### 5.1 Empresas y puntos de venta

`Company` ya contiene:

- razón social;
- CUIT;
- condición fiscal;
- domicilio fiscal;
- localidad, provincia y código postal;
- punto de venta por defecto legado;
- separación multiempresa.

`FiscalPointOfSale` ya contiene:

- empresa;
- número;
- ambiente `homologation` o `production`;
- estado activo;
- indicador de punto de venta predeterminado.

Fortalezas:

- relación protegida con la empresa;
- unicidad de empresa y número;
- solo un punto predeterminado por empresa.

Faltantes:

- código de tipo de emisión y configuración técnica versionada;
- control de que el punto esté habilitado en ARCA mediante `FEParamGetPtosVenta`;
- registro del último preflight exitoso;
- separación explícita de configuración de homologación y producción;
- estado de certificado y vencimiento.

### 5.2 Documentos fiscales

`FiscalDocument` ya contiene:

- clave de origen única;
- empresa, cliente, pedido y punto de venta;
- tipo de comprobante y modo de emisión;
- número fiscal;
- estado;
- CAE y vencimiento;
- neto, descuento, IVA, total, moneda y cotización;
- documento relacionado;
- payloads, errores, intentos y próximo reintento;
- restricción única por empresa, punto de venta, tipo y número.

Tipos modelados: facturas A/B/C, notas de crédito A/B/C y notas de débito A/B/C. La emisión electrónica de la interfaz y el servicio de emisión está limitada actualmente a Factura A/B y Nota de Crédito A/B, que coincide con el alcance funcional confirmado por el usuario.

El snapshot inicial conserva datos del emisor, receptor, punto de venta, operador y contexto comercial dentro de `request_payload["snapshot"]`. Sin embargo, el XML de emisión vuelve a leer el perfil vivo del cliente. Un cambio entre creación y emisión podría provocar que la solicitud no coincida con el snapshot.

### 5.3 Ítems e IVA

`FiscalDocumentItem` conserva:

- SKU y descripción;
- cantidad;
- precio unitario neto;
- descuento;
- neto;
- alícuota e importe de IVA;
- total.

El sistema está configurado para que los precios del catálogo sean netos y el IVA se sume al documento electrónico. La agrupación de alícuotas soporta 0, 2,5, 5, 10,5, 21 y 27 por ciento.

Riesgos:

- una alícuota no mapeada se omite silenciosamente;
- si hay IVA total pero no detalle, el cliente fuerza un detalle al 21 %, lo que puede falsear un comprobante;
- `ImpNeto` se deriva como total menos IVA, ignorando conceptualmente no gravado, exento y tributos;
- concepto está fijado en productos (`Concepto=1`);
- no se soportan fechas de servicio;
- no se envía condición IVA del receptor.

### 5.4 Intentos y auditoría

`FiscalEmissionAttempt` registra cada intento con:

- usuario;
- payload sanitizado;
- respuesta sanitizada;
- duración;
- número de intento;
- resultado;
- error y posibilidad de reintento.

`AdminAuditLog` y middleware de contexto ya dan una base de auditoría.

Faltantes:

- identificador de correlación fiscal estable por operación;
- hash del request canónico;
- restricción única por documento y número de intento;
- separación de transporte, parseo, decisión y resultado fiscal;
- estado `uncertain` explícito;
- registro de reconciliaciones;
- append-only real: los JSON consolidados del documento se sobrescriben;
- retención y clasificación de datos.

## 6. Flujo actual de emisión

1. Se crea un `FiscalDocument` local y sus ítems.
2. Se bloquea la fila del documento.
3. Se crea o bloquea una serie fiscal local.
4. Según configuración, se consulta una vez el último número autorizado.
5. Se reserva e incrementa el número local.
6. El documento pasa a `submitting`.
7. Fuera de la transacción se obtiene Ticket de Acceso y se llama `FECAESolicitar`.
8. Se guarda el intento y el resultado.
9. Si hay CAE, se actualiza cuenta corriente y stock.
10. Si falla temporalmente, pasa a `pending_retry`.
11. Celery Beat vuelve a llamar la emisión.

### Problema crítico de estado incierto

Si ARCA autorizó el comprobante pero la respuesta no llegó, el sistema local queda sin CAE. El reintento actual vuelve a invocar `FECAESolicitar` directamente. Antes de cualquier reenvío debe:

1. bloquear el documento;
2. consultar `FECompConsultar` para ese CUIT, punto de venta, tipo y número;
3. si existe, reconstruir localmente la autorización;
4. si no existe, consultar `FECompUltimoAutorizado`;
5. decidir si es seguro reenviar el mismo request canónico;
6. dejar revisión manual si hay contradicción.

El estado `rejected` tampoco debe ser genéricamente reintentable: solo ciertos errores técnicos o corregibles pueden volver a emitirse. Un rechazo fiscal de contenido exige corregir un borrador no autorizado o emitir otro documento, según el caso.

## 7. Cliente WSAA/WSFE actual

Fortalezas:

- genera TRA;
- firma CMS con OpenSSL;
- usa endpoints separados por ambiente;
- cachea token y firma con margen de expiración;
- sanitiza token, firma y material sensible antes de persistir;
- soporta `FECAESolicitar` y `FECompUltimoAutorizado`;
- impide producción salvo `ARCA_ALLOW_PRODUCTION=True`.

Brechas:

- no implementa `FECompConsultar`;
- no implementa `FEDummy`;
- no implementa `FEParamGetPtosVenta`;
- no implementa `FEParamGetCondicionIvaReceptor`;
- no envía `CondicionIVAReceptorId`;
- no interpreta por separado `Errors`, `Events` y todas las `Obs`;
- no valida el XML contra el contrato WSDL;
- construye XML manualmente;
- clasifica todo error HTTP como temporal, incluso respuestas 4xx que pueden ser permanentes;
- conserva el Ticket de Acceso solo en caché y no coordina un refresh con lock distribuido;
- la ventana del TRA es de diez minutos; debe validarse contra reloj sincronizado y documentación vigente;
- el nombre de clase “production-ready” no refleja el estado real.

## 8. Alta de clientes por CUIT

El endpoint `client_cuit_lookup` actualmente:

- normaliza CUIT/DNI;
- valida el dígito verificador del CUIT;
- detecta duplicados dentro de la empresa;
- crea una `ClientFiscalReview`;
- si no hay duplicado, devuelve campos vacíos y `official_verification_pending=True`.

No existe un cliente de `ws_sr_constancia_inscripcion` ni un Ticket de Acceso para ese servicio. Tampoco existe caché fiscal por CUIT, snapshot de respuesta oficial normalizada, fecha de verificación, provenance por campo o flujo de comparación entre dato local y dato oficial.

La cola de revisión manual ya creada es una buena base para:

- CUIT duplicado;
- conflicto entre ARCA y dato local;
- respuesta parcial;
- contribuyente inexistente o inactivo;
- error persistente de consulta.

## 9. Roles y acceso

Existe control granular mediante capacidades:

- `issue_documents`;
- `manage_integrations`;
- `manage_users`;
- `manage_backups`;
- otras capacidades comerciales.

Roles actuales:

- `admin` y `administracion`: todas las capacidades;
- `facturacion`: puede emitir comprobantes;
- `ventas`: no puede emitir;
- `deposito`: no puede emitir.

Recomendaciones:

- separar `fiscal.draft`, `fiscal.issue`, `fiscal.reconcile`, `fiscal.credit_note`, `fiscal.configure` y `fiscal.view_sensitive`;
- exigir autenticación reforzada para configuración, producción y rotación de certificados;
- impedir que soporte vea payloads completos;
- hacer que todo cambio de empresa y punto de venta quede auditado;
- exigir doble confirmación para habilitar producción, no para cada emisión normal.

## 10. PDF y QR

El PDF contiene emisor, receptor, detalle, IVA, totales, CAE y vencimiento. El QR codifica los campos conceptuales principales.

Brechas:

- URL QR histórica de AFIP;
- moneda del QR fijada en `PES` aunque el documento tenga otra moneda;
- texto alternativo dice “QR AFIP”;
- no hay validación automática de que el QR decodificado coincida con el snapshot autorizado;
- falta revisar leyendas y detalle de transparencia fiscal vigentes;
- el template muestra una nota interna que no debe presentarse como texto fiscal definitivo;
- el PDF debería generarse exclusivamente desde snapshot autorizado, no desde modelos mutables.

## 11. Seguridad y secretos

Situación actual:

- las rutas de certificado y clave se leen desde `ARCA_COMPANY_CONFIG_JSON`;
- los archivos se esperan fuera del repositorio;
- producción está deshabilitada por defecto;
- los payloads se sanitizan;
- Sentry evita PII por defecto.

Brechas:

- JSON de entorno no equivale a gestión de secretos;
- no se valida propietario, permisos o modo de archivo;
- no hay cifrado de clave privada en reposo gestionado por aplicación;
- no hay rotación ni alertas de vencimiento;
- no hay inventario de certificados;
- OpenSSL podría incluir detalle operativo sensible en errores mostrados;
- los archivos temporales del TRA dependen de permisos predeterminados del sistema;
- no hay política documentada de backups con certificados excluidos.

## 12. Matriz de brechas

| Prioridad | Brecha | Consecuencia | Acción |
|---|---|---|---|
| P0 | Falta `CondicionIVAReceptorId` | Rechazo de emisiones actuales | Implementar catálogo, mapeo, snapshot y XML |
| P0 | No existe reconciliación de estado incierto | Duplicidad lógica, número bloqueado o CAE perdido | Implementar `FECompConsultar` antes de reintentar |
| P0 | Reintento automático de `submitting` | Reenvío ciego | Introducir `uncertain` y tarea de reconciliación |
| P0 | No hay consulta oficial por CUIT | Alta de clientes no oficial | Implementar WSAA + `getPersona_v2` |
| P0 | No hay suite de contrato/homologación | Riesgo fiscal alto | Fixtures SOAP y pruebas contra homologación |
| P1 | Numeración local sincronizada solo a veces | Colisiones o huecos no explicados | Lock distribuido + consulta remota + reconciliación |
| P1 | Payload usa cliente vivo | Diferencia entre borrador y emisión | Emitir desde snapshot inmutable |
| P1 | Alícuota desconocida se omite | Totales inválidos | Fallar cerrado |
| P1 | Fallback fuerza IVA 21 % | Información fiscal falsa | Eliminar fallback y exigir desglose válido |
| P1 | QR apunta a AFIP | Especificación desactualizada | Usar URL ARCA oficial |
| P1 | Certificados sin ciclo de vida | Riesgo de indisponibilidad o exposición | Almacén seguro, rotación y alertas |
| P2 | Cliente real y stub duplicados | Confusión de mantenimiento | Unificar puerto/adaptador |
| P2 | Capacidades fiscales demasiado amplias | Exceso de privilegios | Separar permisos |

## 13. Componentes reutilizables

Se conservan:

- `Company`, multiempresa y acceso por empresa;
- `FiscalPointOfSale`;
- `FiscalDocument` y `FiscalDocumentItem`;
- snapshots fiscales, reforzándolos;
- `FiscalEmissionAttempt`, migrándolo a interacciones append-only;
- cálculo neto más IVA;
- relación de nota de crédito;
- stock posterior al CAE;
- cuenta corriente;
- PDF con WeasyPrint;
- QR con qrcode;
- Celery y Redis;
- capacidades y auditoría;
- `ClientFiscalReview`.

## 14. Decisión de avance

No migrar a Firebase ni crear un microservicio por defecto. La opción de menor riesgo es fortalecer el monolito Django con límites internos claros:

- autenticación ARCA;
- consulta de padrón;
- facturación WSFE;
- orquestación fiscal;
- reconciliación;
- representación PDF/QR;
- seguridad y observabilidad.

La emisión real queda prohibida hasta completar los criterios de salida de homologación definidos en `ARCA_IMPLEMENTATION_PLAN.md`.
