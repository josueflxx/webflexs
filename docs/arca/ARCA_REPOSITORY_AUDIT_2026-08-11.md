# Auditoría inicial del repositorio para integración ARCA

Fecha: 2026-08-11  
Etapa: Fase 0 — auditoría inicial  
Alcance: repositorio local, configuración segura offline y evidencia pública del host.  
No realizado: conexión a WSAA/WSFE/padrón, obtención de TA, consulta de CUIT, emisión, deploy, merge o cambio de secretos.

## Veredicto ejecutivo

WebFlexs ya tiene una base fiscal importante y una integración directa WSAA/WSFE de homologación con controles fail-closed. La emisión remota está bloqueada de forma incondicional dentro del cliente ARCA. Sin embargo, la consulta oficial de clientes por CUIT todavía no existe: el endpoint actual sólo valida el CUIT, detecta duplicados locales y devuelve un fallback manual. Tampoco está incorporado AFIP SDK ni existe aún una abstracción fiscal conectada al flujo real; el contrato genérico existente es un stub separado del cliente WSFE funcional.

La arquitectura actual es un monolito Django/Python desplegado en un VPS con PostgreSQL, Redis, Celery, Gunicorn y Nginx. No se encontró Firebase, Firestore, Cloud Functions, Cloud Run ni un pipeline de contenedores. Por eso, el lugar natural para ejecutar código fiscal es el backend Django o, si se elige AFIP SDK Node, un servicio backend privado adicional. Nunca el bundle React/Vite.

El código ARCA está comprometido en `origin/main`, porque la rama actual y `origin/main` apuntan al mismo commit. No obstante, no se pudo certificar el SHA exacto que ejecuta Gunicorn: el sitio público responde, pero no expone un identificador de release y el acceso SSH de solo lectura no estaba autorizado por el servidor. Los 40 archivos modificados y 7 archivos no rastreados encontrados al inicio de la auditoría son exclusivamente locales y no están en `origin/main`.

## A. Arquitectura real actual

- Backend principal: Django 5 / Python, con vistas HTML renderizadas en servidor y Django REST Framework para `/api/v1/`.
- Frontend principal: templates Django, CSS y JavaScript estático dentro del mismo proyecto.
- Frontend adicional: build compilado de React/Vite para el editor masivo en `catalogopro_build/frontend`; no se encontró el código fuente ni un `package.json` dentro de este repositorio. El build consume el API de Django.
- Backend legacy adicional: artefactos binarios compilados de CatalogoPRO/.NET dentro de `catalogopro_build/api`; no son el backend fiscal recomendado.
- Base local: SQLite mediante `flexs_project.settings.local`.
- Base de producción: PostgreSQL mediante `flexs_project.settings.production`.
- Procesos documentados para producción: Gunicorn, Nginx, Celery y Celery Beat en un VPS.
- Caché/cola: Redis es obligatorio en producción. El código admite Redis o Memcached para Ticket/Sign de WSAA; rechaza LocMem, archivos y base de datos para ese uso.
- Hosting observado: el dominio oficial responde por HTTPS detrás de Nginx en Ubuntu. El API v1 está activo y exige autenticación.
- Ausencias verificadas: no hay configuración de Firebase, Firestore, Cloud Functions, Cloud Run, Vercel, Render, Railway ni Docker.
- Editor remoto: `/editor-masivo/` devolvió 404 en la comprobación pública; el build local no está publicado en esa ruta.

## B. Código ARCA/fiscal ya existente

### ARCA directo

- `core/services/arca_config.py`: allowlist cerrada de endpoints WSAA y WSFE de homologación; producción se identifica para rechazarla.
- `core/services/arca_credentials.py`: carga backend-only y validación offline de certificado/clave, CUIT, vigencia, fingerprint, correspondencia del par, rutas fuera del repositorio y permisos restrictivos.
- `core/services/arca_homologation.py`: compuerta fail-closed de sólo lectura y bloqueo incondicional de emisión.
- `core/services/arca_ticket_cache.py`: Ticket/Sign en caché compartida efímera, con singleflight y limpieza exacta.
- `core/services/arca_transport.py`: transporte SOAP con TLS obligatorio y redirecciones rechazadas.
- `core/services/arca_client.py`: WSAA, `FEDummy`, catálogos `FEParamGet*`, puntos de venta, `FECompUltimoAutorizado` y `FECompConsultar`. Contiene construcción/parsing de `FECAESolicitar`, pero la llamada está bloqueada antes de login, payload y despacho.
- Comandos offline: `arca_homologation_doctor` y `arca_homologation_gate`.
- Comando de red futuro y read-only: `arca_homologation_readonly_probe`.

### Dominio fiscal

- Empresas y puntos de venta separados por compañía y ambiente.
- Documentos fiscales, items, series, intentos de emisión, reconciliaciones y auditoría de mutaciones.
- Claves únicas de origen, idempotencia y correlación.
- Snapshots inmutables y hash de payload.
- Locks de fila, restricciones únicas y bloqueo de serie ante estados inciertos.
- Estados de autorización, rechazo, incertidumbre, recuperación y revisión manual.
- Recuperación por `FECompConsultar` sin reautorizar una operación incierta.
- Persistencia de CAE, vencimiento, número, punto de venta, tipo, request/response sanitizados, actor y timestamps.
- Roles/capacidades para emitir documentos y administración fiscal.
- Impresión/PDF/QR y trazabilidad comercial ya tienen infraestructura local.

### Consulta de clientes por CUIT

- Existe `/admin-panel/clientes/cuit-lookup/`.
- Valida formato y dígito verificador, aplica alcance por empresa y detecta duplicados locales.
- Encola revisión manual si ya existe un cliente con el documento.
- No consulta ARCA. Devuelve `source=fallback` y `official_verification_pending=true`.

### Contrato genérico existente

- `core/integrations/base.py` define un contrato genérico `send`.
- `core/integrations/arca/client.py` es un stub que siempre responde que ARCA no está configurado.
- El flujo fiscal real no usa ese stub: importa directamente `ArcaWsfeClient`.

## C. Partes completas

- Modelo fiscal, trazabilidad, snapshots, idempotencia, locks y recuperación query-only.
- Seguridad offline de certificado/clave y redacción de datos sensibles.
- Allowlist exacta de homologación, TLS obligatorio y producción bloqueada.
- Cliente directo WSAA/WSFE para operaciones de lectura implementadas y cubiertas con tests mockeados.
- Comando read-only con argumentos cerrados, salida sanitizada y limpieza del Ticket.
- Roles internos por grupos y capacidades: Administración, Ventas, Depósito y Facturación; la capacidad `issue_documents` controla mutaciones fiscales.
- Alcance multiempresa y asignación de vendedor mediante `Order.assigned_to`.
- Documentación previa extensa en `.ai/` y `docs/arca/`.

“Completo” aquí significa implementado y probado offline. No significa validado contra ARCA real ni confirmado como desplegado en el proceso activo del VPS.

## D. Partes incompletas

- Configuración local real de ARCA: actualmente deshabilitada, sin endpoints, IDs, CUIT, punto de venta, tipo, rutas de credenciales ni caché habilitada.
- Verificación live de WSAA/WSFE: nunca se ejecutó en esta auditoría.
- Confirmación del punto de venta de homologación contra `FEParamGetPtosVenta`.
- Cliente para `ws_sr_constancia_inscripcion`.
- Normalizador de respuesta de padrón y UI de confirmación antes de guardar.
- Abstracción `FiscalProvider` usada por el dominio real.
- Implementación AFIP SDK y mecanismo runtime para su token.
- Separación de tickets/configuración por servicio WSAA (`wsfe` versus padrón).
- Evidencia del SHA exacto desplegado en Gunicorn.
- Compuerta de emisión en la capa exterior de vista/tarea/servicio; hoy el bloqueo remoto es sólido, pero la ruta y la tarea de emisión existen.
- Prueba actual de concurrencia sobre PostgreSQL: en esta auditoría los ocho casos específicos se omitieron por ejecutarse con SQLite.

## E. Partes mockeadas o simuladas

- La consulta CUIT es un fallback manual; no realiza intento remoto.
- `core/integrations/arca/ArcaIntegrationClient` es un stub.
- Las pruebas ARCA usan mocks de WSAA/WSFE y bloquean DNS/socket/HTTP.
- El probe read-only está probado con cliente mock, pero no fue ejecutado contra ARCA.
- AFIP SDK no está instalado ni referenciado.

## F. Producción versus homologación

- El código directo ARCA sólo acepta el valor externo exacto `homologacion` y endpoints oficiales allowlisted de testing.
- `ARCA_PRODUCTION_ENABLED` debe permanecer falso.
- `ARCA_HOMOLOGATION_EMISSION_ENABLED` debe permanecer falso.
- Aunque el código conoce los endpoints productivos para detectarlos, no puede resolverlos para operar.
- `FECAESolicitar` está bloqueado incondicionalmente antes de obtener Ticket, construir payload o marcar despacho.
- La configuración local actual está en `disabled`.
- El sitio de producción está activo, pero no se demostró el SHA exacto del proceso. La rama local y `origin/main` comparten el commit `da4c97b`; las modificaciones del working tree no están en ese commit.

## G. Secretos y datos sensibles esperados

### Esperados por el código actual

- `ARCA_CERT_PATH`: ruta absoluta al certificado público fuera del repositorio.
- `ARCA_PRIVATE_KEY_PATH`: ruta absoluta a la clave privada fuera del repositorio.
- `ARCA_EXPECTED_CERT_SHA256`: fingerprint público opcional, recomendado.
- `ARCA_CREDENTIAL_ID`: alias backend no secreto.
- `ARCA_CUIT`, `ARCA_SERVICE_ID`, `ARCA_PTO_VTA` y `ARCA_DEFAULT_CBTE_TIPO`: identificadores backend; no deben provenir del navegador.
- `ARCA_CREDENTIALS_CONFIG_JSON`: alternativa multiempresa backend-only con rutas e identificadores, nunca material criptográfico.
- Redis/Memcached para Ticket/Sign temporal. Token y Sign no deben persistirse en archivos ni logs.
- Secretos generales existentes: `DJANGO_SECRET_KEY`, credenciales PostgreSQL, SMTP, Redis y tokens DRF.

### No esperado todavía

- No existe variable ni consumidor para el access token de AFIP SDK.
- No debe agregarse el token al frontend, Firestore, documentación, Git ni valores de ejemplo.

## H. Riesgos encontrados

1. No existe evidencia de release que relacione un SHA de Git con el proceso activo del VPS.
2. La búsqueda por CUIT parece funcional en UI, pero sólo prepara carga manual; puede generar una falsa expectativa de verificación oficial.
3. Hay dos caminos ARCA distintos: un stub genérico y el cliente real directo. Esto crea ambigüedad y acoplamiento.
4. `fiscal_emission.py` depende directamente de `ArcaWsfeClient`; cambiar de proveedor requiere tocar el dominio.
5. La ruta/tarea de emisión ya existe. El cliente la bloquea correctamente, pero antes de habilitar una futura emisión debe agregarse una compuerta exterior que impida encolar o mutar estado mientras la etapa sea read-only.
6. Los dos puntos de venta presentes en SQLite están etiquetados como homologación, pero todavía no fueron validados contra ARCA y no deben asumirse correctos.
7. El entorno local real no tiene configurado ARCA ni una caché compartida para Ticket/Sign.
8. La clave cifrada no está soportada en esta etapa: el código actual requiere una key sin passphrase y rechaza `ARCA_PRIVATE_KEY_PASSPHRASE_FILE`.
9. SQLite no valida las garantías de concurrencia que sí dependen de PostgreSQL.
10. El deploy documentado carga un `.env` desde el directorio de la aplicación. Está ignorado por Git, pero para secretos fiscales es preferible un archivo externo bajo `/etc` o credenciales administradas por systemd.
11. El editor React compilado no está publicado en la ruta esperada; cualquier decisión de UI basada en ese artefacto sería sólo local.
12. La documentación previa contiene estados históricos. Este informe y el handoff fechado deben usarse como estado actual, sin borrar los anteriores.

## I. Cambios recomendados

1. Definir contratos backend separados por responsabilidad:
   - `FiscalVoucherProvider`: status, catálogos, último comprobante, consulta de comprobante y, sólo en una fase autorizada, autorización.
   - `TaxpayerRegistryProvider`: búsqueda por CUIT y normalización.
   - Una fachada `FiscalProvider` puede componer ambos sin mezclar tickets, endpoints ni esquemas.
2. Adaptar el cliente directo actual como `DirectArcaWsfeProvider`; no reemplazarlo.
3. Reutilizar o retirar explícitamente el stub `core/integrations/arca` para que no compita con la implementación real.
4. Crear configuración y caché separadas por servicio WSAA; nunca reutilizar un TA de `wsfe` para el padrón.
5. Implementar padrón detrás de una bandera read-only propia y un permiso dedicado de consulta de clientes.
6. Normalizar a un DTO interno estable y guardar procedencia/fecha; la UI debe mostrar diferencias y requerir confirmación humana antes de actualizar un cliente.
7. Ocultar/deshabilitar la acción de emitir y bloquearla también en vista, tarea y servicio hasta recibir autorización explícita para `FECAESolicitar`.
8. Añadir un identificador de release protegido (`APP_RELEASE_SHA`) a health/observabilidad para poder auditar deployments.
9. Ejecutar las pruebas fiscales de concurrencia contra PostgreSQL antes de cualquier prueba de emisión.
10. Mantener secretos fuera del repositorio y del web root; usar un archivo de entorno de systemd externo con permisos restrictivos en el VPS.

## J. Plan exacto por fases

### Fase 0 — completada

- Inventario de arquitectura, host, auth, roles, endpoints, código fiscal, Git y documentación.
- Pruebas offline dirigidas.
- Sin ARCA y sin emisión.

### Fase 1 — preparación segura offline

1. Confirmar el registro local de empresa y punto de venta que se usará; no crear ni adivinar números.
2. Configurar sólo nombres/rutas en un entorno local ignorado o en variables de proceso.
3. Mantener certificado y key donde ya están, fuera del repositorio; no copiarlos.
4. Configurar una caché Redis local compartida para TA.
5. Mantener producción y emisión en falso.
6. Ejecutar doctor y gate offline contra la configuración real; revisar sólo salida sanitizada.
7. Introducir contratos de proveedor y tests sin red, sin cambiar el flujo funcional.

### Fase 2 — WSFE read-only

1. Habilitar las señales read-only únicamente para una ejecución controlada.
2. Ejecutar una sola vez el management command del probe.
3. Secuencia: WSAA → `FEDummy` → catálogos → puntos de venta → tipo configurado → último autorizado.
4. Limpiar Ticket/Sign y volver a deshabilitar las señales.
5. Documentar evidencia sanitizada.

### Fase 3 — padrón CUIT

1. Confirmar con documentación oficial el service ID, endpoint/WSDL, operación y esquema de homologación.
2. Elegir proveedor directo o adapter AFIP SDK.
3. Implementar `TaxpayerRegistryProvider`, normalizador y manejo de errores.
4. Conectar el endpoint existente sin sobrescritura automática.
5. Probar un CUIT de homologación autorizado y documentar campos devueltos, sin exponer datos personales innecesarios.

### Fase 4 — diseño de factura de prueba

- Validar condición fiscal, tipo, concepto, IVA, moneda, totales, punto de venta, numeración e idempotencia.
- Ensayar payload sólo de forma local/mock.
- Verificar recuperación ante timeout y auditoría.

### Fase 5 — parada obligatoria

- Pedir autorización explícita del usuario antes de cualquier `FECAESolicitar`.
- Sin esa autorización, la emisión debe seguir bloqueada en todas las capas.

## K. Qué puede probarse ya sin emitir

- Checks Django y ausencia de migraciones pendientes.
- Doctor y gate offline.
- Validación criptográfica offline, una vez configuradas las rutas, sin imprimir contenido.
- Tests unitarios de endpoints, credenciales, caché, SOAP, redacción, idempotencia y recuperación.
- Con la Fase 1 completa: TA de WSAA, `FEDummy`, catálogos, puntos de venta y último autorizado mediante el probe read-only.
- No puede probarse todavía el padrón real desde WebFlexs porque el cliente no existe.
- No debe usarse `FECAESolicitar`.

## L. Acciones manuales necesarias del usuario

1. No pegar en chats ni código la key, el token AFIP SDK, Token/Sign de WSAA, `.env`, contraseñas o Clave Fiscal.
2. Confirmar que el punto de venta local candidato corresponde realmente al punto de venta Web Services de homologación. La base local tiene dos registros de homologación, pero no se validaron.
3. Autorizar/instalar Redis local o indicar un Redis de homologación seguro antes del probe.
4. Permitir que Codex configure variables locales sin valores expuestos, o hacerlo manualmente siguiendo un archivo de placeholders.
5. Para certificar deployment, ejecutar en el VPS `git rev-parse HEAD` dentro de `/var/www/webflexs` y compartir sólo el SHA, o habilitar acceso SSH de lectura. No compartir `.env` ni logs sensibles.
6. Antes de Fase 2, confirmar explícitamente que se puede realizar la prueba read-only. Esta autorización no habilita emisión.

## M. Archivos de certificado y provisión segura

- Runtime necesita únicamente el certificado `.crt` y la clave privada `.key` correspondiente.
- El `.csr` no se usa para autenticación runtime.
- No es necesario copiar los archivos: pueden permanecer en el directorio externo al repositorio indicado por el usuario.
- Configurar solamente las rutas absolutas mediante variables backend.
- En Windows, restringir ACL de la key al usuario que ejecuta Django; el doctor verifica que no sea legible por grupos amplios.
- En el VPS eventual, colocar certificado/key fuera de `/var/www/webflexs`, `static` y `media`; dueño del servicio, key modo `0600`, directorio sin acceso público.
- El código actual requiere una key sin passphrase. Si la key estuviera cifrada, primero debe modificarse el mecanismo de carga; no se debe quitar la passphrase sin una decisión de seguridad.
- No versionar, documentar, imprimir ni copiar el contenido de ninguno de estos archivos.

## N. Incorporación de AFIP SDK sin acoplamiento

La arquitectura actual no incluye Node. La opción de menor complejidad es conservar la integración directa Python y colocarla detrás de los contratos de proveedor. AFIP SDK no es necesario para WSFE read-only porque ese camino ya existe.

Si se decide usar `@afipsdk/afip.js`, debe ejecutarse como un adapter backend privado:

- Proceso Node separado bajo systemd en el mismo VPS, o servicio privado en Cloud Run si se adopta esa infraestructura más adelante.
- Sin puerto público; preferentemente loopback o socket privado y autenticación servicio-a-servicio.
- Django invoca operaciones normalizadas, nunca payloads del SDK desde el navegador.
- Token en `AFIP_SDK_ACCESS_TOKEN` dentro de un EnvironmentFile de systemd externo al repositorio, con permisos restrictivos. Para desarrollo, variable de proceso o archivo local ignorado y separado del bundle.
- El adapter implementa el mismo contrato interno; cambiar a integración directa no altera clientes, pedidos ni UI.
- Circuit breaker, timeouts, idempotencia, redacción y auditoría se mantienen en Django.

Google Secret Manager/Firebase Secret sólo sería apropiado si el runtime migra a Cloud Run/Functions. No es el mecanismo natural del VPS actual.

## O. Adecuación de la infraestructura

Sí, con condiciones. Django + PostgreSQL + Redis + Celery + Gunicorn/Nginx es suficiente para integración directa ARCA y para un flujo fiscal serio. Redis resuelve coordinación de TA; PostgreSQL soporta locks y restricciones; Celery permite aislar tareas.

Antes de una operación fiscal real faltan:

- secreto externo al repositorio administrado por systemd;
- evidencia de release/deploy;
- monitoreo y alertas fiscales;
- prueba PostgreSQL actual de concurrencia;
- sincronización horaria y conectividad saliente controlada;
- backup/recovery verificado;
- separación operativa clara entre homologación y producción.

Cloud Run no es necesario para la integración directa. Puede ser útil como aislamiento futuro de un adapter Node, pero introducirlo ahora agregaría otra plataforma de secretos, logs, red y despliegue sin evidencia de que el proyecto ya la use.

## Evidencia de verificación de esta auditoría

- `manage.py check`: aprobado.
- `makemigrations --check --dry-run`: sin cambios.
- Doctor local: `WAITING_FOR_USER`, ambiente disabled, producción y emisión deshabilitadas.
- Gate local: `FAIL` por configuración ausente/deshabilitada, resultado seguro esperado.
- Tests dirigidos: 119 casos reportados por el runner, resultado OK; 111 aprobados y 8 skips por requerir PostgreSQL.
- Red ARCA: no utilizada.
- Probe ARCA: no ejecutado.
- Emisión: no ejecutada.
- Sitio público: HTTPS 200 detrás de Nginx; API v1 presente y autenticado; editor local no publicado en su ruta documentada.
- Git inicial: 40 archivos tracked modificados y 7 untracked; no se alteró staging ni se descartaron cambios.
