# Análisis técnico del proyecto Webflexs

**Fecha de corte:** 22 de julio de 2026  
**Alcance:** repositorio y base SQLite local; no se accedió a producción ni se realizaron emisiones ARCA.  
**Conclusión:** **SÍ, PERO REQUIERE REESTRUCTURACIÓN** antes de habilitar facturación electrónica real.

## 1. Resumen ejecutivo

Webflexs ya no es solamente un catálogo. El repositorio contiene una aplicación Django con catálogo público, administración, clientes multiempresa, pedidos, documentos internos, cuenta corriente, pagos, permisos, auditoría, API, webhooks y una primera implementación de facturación fiscal. Esa base permite una evolución incremental y no justifica una reescritura completa.

La facturación actual, sin embargo, **no está lista para producción**. El request WSFE omite `CondicionIVAReceptorId`, obligatorio desde el 15 de abril de 2025; no existe recuperación mediante `FECompConsultar`; se persisten XML SOAP y vistas parciales de `token`/`sign`; la creación del documento puede afectar stock antes del CAE; y los efectos posteriores a la autorización se ejecutan con errores silenciados. Además, varias rutas administrativas legacy resuelven objetos por ID sin aplicar alcance de empresa y permiten precio manual sin exigir el permiso específico de precios.

La estrategia correcta es conservar Django, PostgreSQL y los módulos maduros, crear límites de dominio claros y sustituir progresivamente los puntos inseguros. ARCA debe permanecer deshabilitado en producción hasta cerrar los bloqueantes de seguridad, datos, idempotencia, recuperación y homologación.

## 2. Método y evidencia

Se inspeccionaron código, modelos, migraciones, vistas, servicios, plantillas, configuración, scripts, documentación, pruebas y artefactos compilados. Se ejecutaron únicamente controles locales y consultas agregadas sin exponer datos personales ni secretos.

Controles realizados:

- `manage.py check --settings=flexs_project.settings.local`: sin errores.
- Migraciones locales: no aparecen migraciones pendientes.
- Inventario agregado de la base local, sin mostrar registros individuales.
- Revisión de `.env` únicamente por nombres de variables y ubicación; no se copiaron valores.
- Revisión oficial actual de ARCA documentada en `ARCA_INTEGRATION.md`.
- Suite Django completa iniciada como verificación no destructiva; el resultado queda registrado en `PROJECT_STATUS.md`.

La base local es evidencia del entorno de trabajo, **no una afirmación sobre producción**.

## 3. Arquitectura actual

### 3.1 Stack principal

| Capa | Implementación observada | Evaluación |
|---|---|---|
| Backend principal | Python, Django 5, vistas server-rendered | Reutilizable |
| API | Django REST Framework v1 | Reutilizable con permisos más finos |
| UI principal | Django Templates, CSS y JavaScript | Reutilizable |
| Editor externo | API compatible con editor React; sólo hay bundles compilados | Auditable sólo parcialmente |
| Backend alternativo compilado | Artefactos .NET 8 WebAPI en `catalogopro_build/api` | Sin fuente; no debe ser autoridad fiscal |
| Base local | SQLite con archivo de ~119 MB | Sólo desarrollo/prueba |
| Base producción configurada | PostgreSQL | Adecuada |
| Tareas | Celery + Redis | Adecuadas, falta completar operación de workers |
| PDF | WeasyPrint | Reutilizable tras volver el artefacto inmutable |
| QR | `qrcode` | Reutilizable tras corregir fuente de datos y URL oficial |
| Observabilidad | logging Django, archivos locales y Sentry configurable | Incompleta para operación fiscal |
| Servidor | Gunicorn + Nginx en VPS | Válido, con deuda operativa |

Dependencias centrales: Django, DRF, `django-cors-headers`, Pillow, `openpyxl`, pandas, PostgreSQL driver, Celery, Redis, Sentry, WeasyPrint y QR.

### 3.2 Estructura funcional

- `catalog/`: productos, categorías, marcas, proveedores, listas de precios, importaciones Excel y editor masivo.
- `accounts/`: perfil de cliente, relación cliente-empresa, categorías comerciales, pagos y cuenta corriente.
- `orders/`: carrito, solicitudes, propuestas, pedidos e ítems históricos.
- `core/`: empresas, permisos, documentos, stock, fiscal, ARCA, auditoría, API, integraciones, backups y tareas.
- `admin_panel/`: operación interna sobre clientes, pedidos, fiscal, configuración, usuarios, productos e informes.
- `templates/`: catálogo público, autoservicio de cliente y panel interno.
- `flexs_project/settings/`: configuración base, local, producción y entornos auxiliares.
- `catalogopro_build/`: frontend y API compilados, sin código fuente reproducible en este repositorio.
- `docs/`: documentación parcial del flujo comercial y configuración ARCA.

### 3.3 Fronteras actuales

La UI server-rendered llama directamente a vistas Django, que a veces contienen reglas de negocio y a veces delegan a servicios. La API v1 tiene mejor aislamiento por empresa que varias vistas legacy. Los modelos se comparten entre catálogo, ventas y fiscal.

No hay funciones serverless. Celery es el único mecanismo asíncrono identificado. El repositorio incluye dos posibles superficies de backend —Django y binarios .NET—, pero la fuente .NET no está disponible: Django debe declararse sistema de registro o se producirá divergencia.

## 4. Flujo actual

### 4.1 Productos y catálogo

`catalog.Product` contiene SKU único, nombre, proveedor textual y FK, descripción, costo, precio, stock entero, categoría principal, categorías adicionales, imagen, activo, cinco filtros y atributos JSON. El catálogo público consume estos modelos Django; no se detectó una copia fiscal separada.

Existen:

- categorías jerárquicas y marcas;
- proveedores y ofertas por proveedor;
- listas de precios por empresa y precios por producto;
- historial inmutable de costos de proveedor;
- importaciones Excel con previsualización, control de cambios y algunos rollbacks;
- editor masivo y editor externo protegido por capacidades;
- desactivación mediante `is_active`.

Faltan para facturación confiable:

- descripción fiscal específica;
- unidad de medida;
- moneda de producto/lista;
- alícuota y tratamiento impositivo por producto;
- definición explícita de precio neto o final con IVA;
- historial general de precios efectivos;
- descuento máximo y reglas de venta bajo costo;
- variantes normalizadas;
- stock por depósito, reservado y disponible.

El `stock` entero de `Product` funciona como saldo global derivado, pero también existe `StockMovement`. No es suficiente para concurrencia multi-depósito.

### 4.2 Precios

El servicio de pricing resuelve lista y descuento del cliente. `PriceList` está asociada a empresa y `PriceListItem` a producto. El pedido conserva lista y precios históricos básicos.

El backend no define de manera inequívoca si todos los precios incluyen IVA. La facturación aplica una configuración global de cálculo `gross`/`net` y tasas por tipo de comprobante; no obtiene el tratamiento desde cada producto. Esto impide demostrar la exactitud fiscal en una venta mixta.

Riesgo crítico: `admin_panel/views/orders.py` acepta un precio manual en alta/edición de ítems. `StaffCapabilityMiddleware` exige `manage_orders`, no `change_prices`, y no valida descuento máximo ni venta bajo costo. Un usuario con gestión de pedidos puede manipular el precio desde la solicitud HTTP.

### 4.3 Clientes

`ClientProfile` está unido 1:1 a `auth.User`; mezcla identidad fiscal, dirección y reglas comerciales. `ClientCompany` agrega empresa, categoría, lista, descuento y estado. La decisión de modelar todo cliente como usuario complica clientes sin portal y mezcla identidad de login con tercero comercial.

La consulta CUIT actual (`admin_panel/views/clients.py:2211`) sólo normaliza 8/11 dígitos y devuelve un payload manual vacío con `source=fallback`. No valida dígito verificador, no consulta ARCA, no registra consulta y no previene duplicados por restricción de base.

Estado agregado de la base local:

| Indicador | Resultado local |
|---|---:|
| Clientes | 1.856 |
| Relaciones cliente-empresa | 2.014 |
| Sin identificador fiscal | 364 |
| Grupos de CUIT/DNI normalizado duplicado | 48 |
| Sin domicilio fiscal | 1.855 |
| Sin condición IVA | 5 |

Los duplicados deben resolverse mediante un proceso de conciliación previo a imponer unicidad; no se debe elegir o fusionar automáticamente.

### 4.4 Usuarios, roles y permisos

Se usa `django.contrib.auth.User`, sesiones Django y tokens DRF permanentes. Hay grupos `admin`, `administracion`, `ventas`, `deposito` y `facturacion`, más `AdminCapabilityProfile` para sobrescribir capacidades.

Capacidades actuales: dashboard, búsqueda, venta, pedidos, cancelación, precios, productos, emisión, importaciones, usuarios, exportación, backups e integraciones.

Fortalezas:

- autenticación y permisos principales viven en backend;
- API v1 usa `IsAuthenticated` por defecto;
- varias API filtran por empresa autorizada;
- costo del producto se oculta si falta `change_prices`;
- middleware aplica permisos a acciones sensibles conocidas;
- el superusuario primario tiene controles especiales para credenciales de clientes y configuración fiscal.

Limitaciones:

- permisos demasiado amplios para el dominio solicitado;
- no existe entidad `Vendedor` ni asignación comercial histórica;
- `assigned_to` de `Order` representa asignación operativa, no vendedor comercial;
- no hay permisos `ver_propios`/`ver_todos` por entidad;
- rutas legacy usan `staff_member_required` y algunas resuelven objetos por ID antes de comprobar alcance;
- editar clientes está permitido a cualquier `is_staff`;
- tokens DRF no tienen expiración, alcance ni rotación integrada.

Hallazgo crítico de autorización horizontal: alta, edición y baja de ítems de pedido cargan `Order` por `pk` sin comprobar la empresa activa. En clientes, la edición carga `ClientProfile` global por `pk`; resolver una empresa autorizada no impide modificar el perfil global de un cliente no vinculado. La API v1 está mejor protegida, pero no compensa estos accesos server-rendered.

### 4.5 Ventas y documentos

`Order` ya conserva empresa, cliente, estado, montos, datos copiados, origen, documentos SaaS y asignado operativo. `OrderItem` conserva SKU, nombre, cantidad, precio base/final, descuento y lista.

Existen tipos configurables `SalesDocumentType` para presupuesto/pedido/remito/factura/notas, documento interno, documento fiscal manual, WSFE o SaaS. Esta abstracción es valiosa, pero mezcla configuración de presentación, numeración y efectos contables/stock.

Faltan snapshots de vendedor, costo, moneda, unidad e impuestos en venta. Tampoco existen aprobaciones de descuento ni un agregado de venta separado del pedido; puede mantenerse `Order` como venta si se clarifica su semántica.

### 4.6 Fiscal actual

`FiscalDocument`, `FiscalDocumentItem`, `FiscalDocumentSeries` y `FiscalEmissionAttempt` son una buena base parcial. Hay snapshots de cliente/empresa y líneas fiscales. Se implementó WSAA, `FECAESolicitar`, `FECompUltimoAutorizado`, preflight, estados, intentos, CAE, notas asociadas, QR y PDF.

Brechas bloqueantes:

- request sin `CondicionIVAReceptorId`;
- soporte fijo de A/B/C y sus notas; no consulta habilitaciones ni contempla M;
- IVA por configuración global/tipo, no por producto;
- sin `FECompConsultar` para recuperar una respuesta perdida;
- reintento de estado incierto vuelve a solicitar CAE;
- request/response guardan XML crudo y previews de token/firma;
- documento autorizado editable a nivel de modelo/base y líneas borrables en cascada;
- no existe PDF inmutable con hash;
- correo afirma adjuntar/confirmar, pero usa `send_mail` sin archivo adjunto;
- estados insuficientes para incertidumbre, observaciones y ajustes parciales/totales.

En la base local hay dos documentos fiscales: uno manual cerrado y uno WSFE listo para emitir; no hay intentos ni documentos autorizados. No se llamó a ARCA.

### 4.7 Cuenta corriente, pagos y stock

Cuenta corriente y pagos existen, pero son incompletos:

- un pago tiene un único pedido opcional;
- no hay aplicaciones N:N pago-factura;
- no hay pago combinado normalizado, anticipo ni saldo a favor explícito;
- el ledger usa importes firmados y fuentes idempotentes, pero los errores de sincronización se silencian;
- no hay caja ni recibo de cobranza completo.

La cuenta corriente de facturas sólo considera estados autorizados/cerrados, lo cual es correcto. Sin embargo, el efecto se ejecuta fuera de la transacción de autorización y `except Exception: pass` puede dejar un CAE sin movimiento contable.

El stock presenta un defecto de ciclo de vida: al crear/asignar un `FiscalDocument`, `apply_sales_document_type_to_fiscal_document` puede crear movimientos y actualizar `Product.stock` antes de la autorización ARCA. La decisión de cuándo reservar/descontar stock no está definida y debe resolverse antes de implementar el nuevo circuito.

No hay modelos de comisiones.

## 5. Infraestructura, logs y backups

### 5.1 Entornos

- Local: SQLite, cache de memoria si Redis no está configurado, email configurable.
- Producción: PostgreSQL obligatorio, Redis obligatorio, cookies seguras, HSTS, SSL redirect y proxy SSL.
- Homologación ARCA y producción se distinguen por punto de venta y configuración, con `ARCA_ALLOW_PRODUCTION=False` como barrera inicial.

No existe un entorno de staging integral claramente desplegado. Los settings auxiliares de integridad no sustituyen una homologación aislada.

### 5.2 Deploy

`setup_vps.sh` prepara Nginx, Gunicorn, PostgreSQL y Redis. No instala unidades de `celery worker` ni `celery beat`, aunque el sistema depende de tareas para emisión, reintentos y backups. `deploy_update.ps1` reinicia Gunicorn y Nginx, no workers. Esto puede dejar la UI operativa y la cola fiscal detenida.

La CI ejecuta Python 3.12, `manage.py check` y la suite Django sobre SQLite. No prueba PostgreSQL, Redis/Celery real, concurrencia, bundles React ni binarios .NET. No hay pruebas del cliente WSAA/WSFE.

### 5.3 Logs y auditoría

`AdminAuditLog` conserva usuario, empresa, acción, entidad, ID, detalles y fecha; hay 37.494 eventos locales. Señales y llamadas manuales cubren cambios comunes.

Brechas:

- el registro es `fail-open`: un error se descarta silenciosamente;
- no es append-only ni tiene protección contra modificación/borrado;
- falta resultado normalizado, request/correlation ID y actor efectivo/impersonación;
- login exitoso/fallido y cambios fiscales no tienen cobertura completa;
- no hay política de retención/minimización de PII;
- payloads fiscales guardan más información de la necesaria.

### 5.4 Backups

El backup genera `dumpdata` comprimido, archivo de media opcional, manifiesto y SHA-256, con retención local. Es útil para portabilidad, pero no equivale a una estrategia de recuperación de producción:

- no hay cifrado;
- no hay destino externo/inmutable;
- el directorio puede compartir el mismo VPS;
- no se observan pruebas automáticas de restore ni RPO/RTO;
- el scheduler existe en Celery Beat, pero el deploy no instala Beat.

## 6. Seguridad observada

Detalle y prioridades en `SECURITY_REVIEW.md`.

Hallazgos principales:

1. **Crítico:** request WSFE incompatible con obligación vigente de condición IVA del receptor.
2. **Crítico:** XML y previews de credenciales WSAA pueden persistirse en base/log de errores.
3. **Crítico:** acceso horizontal por ID en rutas administrativas legacy.
4. **Crítico:** posibilidad de efectos de stock antes del CAE y falta de recuperación exacta.
5. **Crítico:** credenciales o contraseñas estáticas aparecen en scripts versionados (`setup_vps.sh` y scripts de creación de usuarios); no se documentan los valores y deben considerarse comprometidas si alguna vez se usaron.
6. **Alto:** factura autorizada no es inmutable en modelo/DB y PDF se regenera.
7. **Alto:** precio manual sin permiso de precios ni límites comerciales.
8. **Alto:** efectos contables posteriores con errores silenciados.
9. **Alto:** backups sólo locales/no cifrados y workers no administrados por deploy.
10. **Medio:** secretos de webhook en texto plano y tokens DRF permanentes.
11. **Medio:** CSP permite `unsafe-inline` y `unsafe-eval`.
12. **Medio:** código fuente del build .NET/React no disponible para auditoría/reproducibilidad.

No se encontraron certificados, claves privadas ni `.env` real versionados. `.env` local está ignorado. `.gitignore` debe ampliarse para certificados y claves fiscales.

## 7. Componentes reutilizables y decisión

| Componente | Acción | Motivo |
|---|---|---|
| Django + PostgreSQL | Conservar | Base adecuada y madura |
| Catálogo, categorías, imágenes, importadores | Conservar y extender | Fuente de productos ya operativa |
| Company/ClientCompany y contexto activo | Conservar y endurecer | Base multiempresa existente |
| Order/OrderItem | Refactor incremental | Conserva flujos y snapshots parciales |
| PriceList/PriceListItem | Extender | Falta semántica fiscal/historia/reglas |
| FiscalDocument/Item/Attempt | Migrar y endurecer | Estructura útil, ciclo de vida inseguro |
| Cliente WSAA/WSFE actual | Sustituir detrás de interfaz | Omisiones y persistencia sensible |
| Celery/Redis | Conservar y operar correctamente | Necesario para trabajos resilientes |
| PDF/QR | Refactor | Debe producir artefacto inmutable |
| Cuenta corriente | Extender | Falta aplicación de pagos y garantías |
| Auditoría | Extender y hacer confiable | Buen punto de partida |
| Vistas admin legacy | Migrar a servicios/policies | Permisos y empresa inconsistentes |
| Build .NET/React | Aislar o recuperar fuente | No debe controlar fiscal sin auditoría |

## 8. Limitaciones y deuda técnica

- Modelos grandes y servicios dispersos con reglas duplicadas.
- Nombres legacy AFIP en código/UI aunque el organismo sea ARCA.
- Dos campos para identificador del cliente (`document_number`, `cuit_dni`).
- Datos fiscales y comerciales mezclados con usuario de autenticación.
- Catálogo global con precios por empresa: falta decidir si el maestro es compartido o por empresa.
- Uso extendido de `except Exception: pass` en efectos relevantes.
- Pruebas funcionales amplias, pero sin simulador contractual de ARCA ni pruebas de concurrencia PostgreSQL.
- Documentación general desactualizada frente a los modelos actuales.
- Scripts sueltos en raíz y artefactos/logs/planillas locales aumentan el riesgo operativo.

## 9. Riesgos de migración

- Imponer CUIT único sin conciliar 48 grupos duplicados bloquearía la migración.
- Separar cliente de usuario requiere compatibilidad temporal para el portal actual.
- Cambiar semántica neto/final de precios sin snapshot y reconciliación alteraría totales.
- Mover stock a saldos por depósito requiere inventario inicial y doble lectura controlada.
- Hacer inmutables facturas exige migrar/archivar los documentos existentes y distinguir borrador de comprobante legal.
- Activar ARCA antes de recuperar fuentes y configurar workers puede producir autorizaciones no reflejadas localmente.

## 10. Decisión arquitectónica

No reescribir. Adoptar un **monolito modular Django** como única autoridad comercial/fiscal, con PostgreSQL, servicios de dominio, policies de autorización, adaptadores ARCA aislados, Celery para tareas y un outbox transaccional para efectos derivados. El catálogo público continúa leyendo los modelos actuales mientras las nuevas tablas se incorporan con migraciones aditivas y feature flags.

La implementación sólo puede avanzar después de resolver las decisiones bloqueantes en `QUESTIONS_FOR_USER.md` y ejecutar la Etapa 1 definida en `IMPLEMENTATION_PHASES.md`.
