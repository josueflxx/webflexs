# Avances del proyecto WebFlexs

**Fecha de corte:** 31 de julio de 2026  
**Proyecto:** Flexs Repuestos / WebFlexs  
**Repositorio local:** `C:\Users\Brian\Desktop\webflexs`  
**Sitio oficial:** `https://flexsrepuestos.shop`  
**Aplicación en el VPS:** `/var/www/webflexs`

## 1. Objetivo y criterio de estado

Este documento consolida los avances funcionales, visuales, fiscales y técnicos realizados en WebFlexs. También distingue lo que ya se publicó de lo que todavía necesita una activación, homologación o validación adicional.

| Estado | Significado |
|---|---|
| Publicado | Disponible en el sitio oficial y verificado visual o funcionalmente |
| Implementado | Desarrollado en el código, con pruebas, pero sujeto a despliegue, migración o aceptación final |
| Preparado | Infraestructura, controles y documentación listos para el siguiente paso |
| Pendiente del usuario | Requiere credenciales, certificados, autorizaciones o decisiones externas |
| Diferido | Quedó expresamente fuera del alcance actual |

No se incluyen contraseñas, claves privadas, certificados, tokens ni datos fiscales sensibles.

## 2. Resumen ejecutivo

WebFlexs evolucionó desde un catálogo con administración básica hacia una plataforma comercial multiempresa que integra:

- Catálogo público y administrativo de productos.
- Categorías, proveedores, listas de proveedor, marcas, rubros y subrubros.
- Clasificación asistida y masiva de productos por marca.
- Clientes con información comercial, fiscal, descuentos y cuenta corriente.
- Pedidos, cotizaciones, remitos, facturas, notas de crédito y cobros.
- Seguimiento del flujo comercial de punta a punta.
- Vendedores y base para estadísticas de rendimiento.
- Stock opcional por producto y soporte para depósitos.
- Importaciones, exportaciones, API, webhooks y auditoría.
- Panel administrativo reorganizado y responsive.
- Base fiscal protegida y preparación offline para ARCA.

La página oficial ya contiene la mayor parte de las mejoras visuales y operativas. La conexión real con ARCA continúa bloqueada de forma intencional hasta completar las acciones de homologación, certificados y configuración oficial.

## 3. Decisiones de negocio confirmadas

Durante el desarrollo se acordaron las siguientes reglas:

1. Cada empresa deberá cargarse y validarse oficialmente con ARCA, aunque ya tenga datos guardados localmente.
2. Los precios del catálogo se almacenan y muestran sin IVA.
3. En los comprobantes electrónicos, el sistema calcula el IVA y presenta el precio final correspondiente.
4. El usuario ingresa el CUIT y el sistema debe intentar completar los demás datos fiscales mediante una consulta oficial autorizada.
5. Si la consulta no devuelve datos concluyentes, el caso pasa a revisión manual.
6. El primer alcance de comprobantes electrónicos comprende:
   - Factura A.
   - Factura B.
   - Nota de crédito A.
   - Nota de crédito B.
7. Las notas de débito quedaron diferidas para una etapa posterior.
8. Un comprobante con CAE queda inmutable.
9. Una corrección posterior al CAE se resuelve mediante una nota de crédito, no modificando la factura autorizada.
10. Antes de obtener CAE, los datos permitidos del borrador pueden corregirse.
11. Los vendedores se utilizan para identificar quién realizó cada venta y generar estadísticas.
12. Los usuarios autorizados pueden modificar el vendedor asignado y el cambio debe conservar trazabilidad.
13. Los descuentos comerciales se administran mediante categorías de clientes.
14. Cada categoría puede definir descuento, lista de precio, condición de venta, cuenta corriente y límite.
15. El stock debe estar disponible, pero su control es opcional para cada producto.
16. Se permite vender por debajo del costo solamente con una observación obligatoria.
17. Las excepciones comerciales y fiscales deben quedar auditadas.

## 4. Estado general por módulo

| Área | Estado al 31/07/2026 |
|---|---|
| Sitio público y página de inicio | Publicado |
| Cabecera pública y navegación | Publicado |
| Catálogo público | Publicado |
| Panel administrativo global | Publicado |
| Dashboard administrativo | Publicado |
| Listado administrativo de productos | Publicado |
| Marcas, rubros y subrubros | Publicado |
| Asignación masiva de productos a marcas | Publicado |
| Panel de clientes | Publicado |
| Categorías de clientes | Publicado |
| Seguimiento general de ventas | Publicado |
| Ficha visual de pedidos | Publicado |
| Ficha visual de documentos fiscales | Publicado |
| Cotizador persistente | Publicado |
| Acceso permanente al inicio público | Publicado |
| Tareas comerciales de clientes | Implementado |
| Informe de vendedores | Implementado |
| Ficha integral del producto | Implementado |
| Stock ampliado por depósito | Implementado, pendiente de activación controlada |
| Resguardos fiscales e inmutabilidad por CAE | Implementado |
| Preparación offline para ARCA | Preparado |
| Consulta oficial completa por CUIT | Pendiente de credenciales y servicio autorizado |
| Homologación real con ARCA | Pendiente del usuario |
| Emisión electrónica real en producción | Bloqueada hasta homologación |
| Notas de débito | Diferido |

## 5. Infraestructura y despliegue

### 5.1 Sitio oficial

- VPS con Ubuntu 24.04 LTS.
- Dominio `flexsrepuestos.shop`.
- Proyecto instalado en `/var/www/webflexs`.
- Acceso SSH mediante una clave dedicada de despliegue.
- Aplicación servida con Gunicorn y Nginx.
- Base PostgreSQL en producción.
- Redis y Celery contemplados para trabajos asíncronos y bloqueos.
- Archivos estáticos recolectados después de los despliegues que los modifican.

### 5.2 Método seguro de publicación

- Inspección previa de los archivos afectados.
- Respaldo antes de cada reemplazo importante.
- Aplicación de migraciones solamente cuando el cambio lo requiere.
- Verificación con `manage.py check`.
- Reinicio controlado del servicio.
- Validación HTTP y visual posterior.
- Conservación de los datos de clientes, productos, pedidos y documentos.

### 5.3 Respaldos recientes en el VPS

Los últimos despliegues relevantes generaron estos respaldos:

- `/var/backups/webflexs/brand_workspace_20260731_112458`
- `/var/backups/webflexs/order_ui_20260731_123216`
- `/var/backups/webflexs/fiscal_ui_20260731_133918`

Estos respaldos corresponden, respectivamente, al espacio de clasificación por marcas, la ficha de pedidos y la ficha de documentos fiscales.

## 6. Navegación global del panel administrativo

### 6.1 Cabecera y barra principal

Se reorganizó la navegación para reducir desplazamientos y mantener visibles las herramientas frecuentes:

- Dashboard.
- Productos.
- Clientes.
- Pedidos.
- Cobros.
- Fiscal.
- Editor masivo.
- Categorías.
- Marcas.
- Productos sin categoría.
- Proveedores.
- Listas de proveedor.
- Búsqueda y alta de clientes.
- Informes.
- Categorías de clientes.
- Seguimiento de ventas.
- Solicitudes de compra.
- Solicitudes a medida.
- Configuración.
- Backups.
- API y webhooks.
- Cambio de empresa.

Los accesos que antes estaban dentro de “Más herramientas” se llevaron a la barra administrativa como botones agrupados por área.

### 6.2 Cotizador persistente

- El botón del cotizador permanece disponible en toda la interfaz administrativa.
- Tiene prioridad visual por ser una herramienta de uso frecuente.
- No depende de estar en el dashboard.

### 6.3 Acceso al inicio público

- Se agregó un botón para volver al inicio de la página pública.
- El acceso está disponible desde todo el panel.
- Se evita que el usuario tenga que modificar manualmente la dirección del navegador.

### 6.4 Comportamiento responsive

- La barra puede desplazarse horizontalmente cuando no entra en pantalla.
- Los grupos mantienen una jerarquía visual consistente.
- Los botones críticos conservan visibilidad en escritorio y móvil.
- Se redujeron saltos, desbordes y superposiciones.

## 7. Dashboard administrativo

El dashboard se rediseñó para mostrar primero lo que requiere atención:

- Resumen de la empresa activa.
- Accesos rápidos a pedidos, clientes, cobros y cambio de empresa.
- Indicadores separados del resto de la actividad.
- Facturado hoy.
- Facturado en el mes.
- Ticket promedio.
- Margen estimado.
- Pedidos pendientes.
- Stock crítico.
- Cola operativa con prioridades.
- Solicitudes por revisar.
- Pedidos por confirmar.
- Pedidos en curso.
- Pedidos listos para facturar.
- Comprobantes a resolver.
- Movimientos abiertos.
- Clientes con deuda.
- Cobros registrados.
- Actividad reciente con acceso al registro relacionado.

La nueva composición reduce el ruido visual y separa resumen, prioridades y actividad.

## 8. Página pública

### 8.1 Diseño general

- Identidad oscura con acentos naranjas.
- Mejor jerarquía de títulos, textos, llamadas a la acción y tarjetas.
- Cabecera pública más compacta y profesional.
- Navegación más clara hacia catálogo, marcas, abrazaderas a medida, portal, solicitudes y pedidos.
- Barras y controles desplegables estilizados de forma coherente.
- Mejor adaptación a anchos de pantalla distintos.

### 8.2 Recursos visuales profesionales

Se reemplazaron emojis por iconos, SVG e imágenes coherentes con el rubro.

Imágenes principales:

- `core/static/core/img/home/service-stock.webp`
- `core/static/core/img/home/service-manufacturing.webp`
- `core/static/core/img/home/service-delivery.webp`

La imagen de “Fabricación propia” se corrigió para representar una abrazadera U-bolt alta con:

- Forma rectangular.
- Extremos roscados.
- Plaqueta recta con dos perforaciones.
- Tuercas separadas.
- Entorno de taller.

### 8.3 Secciones y categorías

- Servicios principales con imágenes.
- Familias de productos con recursos visuales profesionales.
- Tarjetas más consistentes.
- Mejor contraste y separación de contenidos.
- Eliminación de iconografía informal en las áreas rediseñadas.

## 9. Catálogo público

### 9.1 Experiencia visual

- Encabezado del catálogo con cantidad de productos.
- Panel lateral de filtros.
- Buscador por SKU, nombre, frase o tipo.
- Selector de visualización por tarjetas o lista.
- Ordenamiento por nombre y otros criterios disponibles.
- Tarjetas de producto con mejor jerarquía.
- Precio, stock, categorías y acciones diferenciadas.
- Estados sin imagen tratados de forma uniforme.
- Acceso a solicitudes de abrazaderas a medida.
- Descarga del catálogo Excel.

### 9.2 Marcas en el catálogo

- Base para explorar productos clasificados por marca.
- Rubros y subrubros reconocibles.
- Asociación con categorías del catálogo general.
- Filtros y estructura pensados para mejorar la navegación comercial.

## 10. Productos

### 10.1 Listado administrativo

- Buscador por SKU, nombre o proveedor.
- Filtros por categoría, estado y orden.
- Guardado y carga de vistas.
- Creación de productos.
- Revisión de códigos duplicados.
- Selección múltiple.
- Acciones masivas.
- Indicadores de producto activo y visible.
- Categorías visibles mediante etiquetas.
- Identificación de productos sin imagen.
- Advertencias de códigos duplicados.
- Acciones de edición y eliminación siempre accesibles.
- Mejor tabla para resoluciones grandes y comportamiento responsive.

### 10.2 Reglas del producto

- Precio del catálogo almacenado sin IVA.
- Alícuota de IVA configurable.
- Cálculo de precio final disponible para operaciones fiscales.
- Control de stock opcional.
- Opción de permitir stock negativo.
- Opción para indicar si el producto se puede comprar.
- Separación entre visibilidad, venta, compra y control de existencias.
- Validaciones para importación y edición externa.

### 10.3 Ficha integral del producto

Se implementó una vista de trabajo que reúne:

- Datos generales.
- Estado comercial.
- Precio neto.
- IVA.
- Precio final calculado.
- Costo actual.
- Margen.
- Proveedor.
- Categorías.
- Ofertas de proveedores.
- Historial del producto.
- Movimientos de stock.
- Cambios de costo.
- Auditoría.

El costo se oculta a los usuarios que no tienen permisos para consultarlo.

## 11. Marcas, rubros y subrubros

### 11.1 Administración visual

- Tarjetas de marcas con identidad, estado, orden y cantidad de rubros.
- Estructuras de cada marca desplegables y ocultables.
- Rubros y subrubros dentro de una jerarquía clara.
- Controles de edición, eliminación, alta de rubro y alta de subrubro.
- Filtros por texto y estado.
- Reducción importante de la longitud inicial de la página.

### 11.2 Espacio de asignación de productos

Se desarrolló un espacio específico para agregar productos a cada rubro o subrubro:

- Buscador por SKU o nombre.
- Filtro por categoría del catálogo general.
- Resultados con información relevante del producto.
- Selección individual y múltiple.
- Seleccionar todos los resultados visibles.
- Asignación masiva.
- Movimiento de productos entre destinos.
- Eliminación de asignaciones.
- Reordenamiento del listado.
- Resumen de selección y destino.
- Prevención de duplicados.
- Adaptación para escritorio y móvil.

### 11.3 Autosincronización segura

- Asociación entre categorías generales y subrubros de marca.
- Vista previa antes de aplicar.
- Confirmación explícita.
- Resumen de productos por agregar, mover, conservar o retirar.
- Registro del lote de sincronización.
- Eliminaciones reversibles.
- Función para deshacer cambios recientes.
- Controles para impedir pérdidas silenciosas de clasificación.

### 11.4 Asignación desde el listado general

- Selección masiva de productos desde “Productos”.
- Destino por marca, rubro o subrubro.
- Asignación sin tener que abrir cada ficha individual.

### 11.5 Validación y publicación

- Migración `catalog.0031_brandcatalogbatch_reversible_removals`.
- 41 pruebas enfocadas aprobadas.
- Verificación visual en escritorio y móvil.
- Publicación en el sitio oficial.
- Respaldo: `/var/backups/webflexs/brand_workspace_20260731_112458`.

## 12. Clientes

### 12.1 Panel de clientes

- Encabezado específico del módulo.
- Accesos a búsqueda, alta, informes, importación y categorías.
- Total de clientes.
- Cantidad de clientes aprobados.
- Accesos al portal.
- Nuevos clientes del mes.
- Clientes recientes.
- Distribución por categorías.
- Acceso directo a la ficha comercial.
- Empresa activa visible.

### 12.2 Datos comerciales y fiscales

- Datos de contacto.
- Domicilio, localidad, provincia y código postal.
- CUIT y condición frente al IVA.
- Razón social.
- Categoría comercial.
- Descuento.
- Lista de precios.
- Cuenta corriente.
- Límite de cuenta corriente.
- Pedidos, cobros y documentos asociados.
- Validación de duplicados por CUIT dentro del contexto correspondiente.

### 12.3 Consulta por CUIT

La base actual permite:

- Ingresar un CUIT.
- Normalizar y validar el formato.
- Detectar clientes existentes.
- Preparar una carga asistida.
- Enviar el caso a revisión manual cuando los datos no son concluyentes.

La carga oficial completa desde ARCA no se presenta como habilitada mientras falten las credenciales y el servicio autorizado.

### 12.4 Revisión fiscal manual

- Entidad `ClientFiscalReview`.
- Cola de casos dudosos o incompletos.
- Asociación con la empresa y el cliente.
- Registro del motivo y resolución.
- Flujo protegido frente a datos fiscales incompletos.

### 12.5 Tareas comerciales

Se implementó un sistema de seguimiento con:

- Título y descripción.
- Prioridad.
- Responsable.
- Estado pendiente, completado o cancelado.
- Fecha de vencimiento.
- Observación comercial.
- Bandeja por cliente.
- Bandeja global.
- Filtros de tareas propias y del equipo.
- Detección de tareas vencidas.
- Auditoría de cambios.
- Aislamiento por empresa.

## 13. Categorías de clientes

El frontend del apartado se reorganizó para administrar reglas comerciales con mayor claridad:

- Búsqueda por nombre o lista de precio.
- Filtro por estado.
- Alta de nuevos tipos.
- Tabla con:
  - Nombre.
  - Condición de venta.
  - Cuenta corriente.
  - Límite.
  - Uso de costo.
  - Descuento.
  - Lista de precio.
  - Estado.
  - Cantidad de clientes.
- Etiquetas para valores booleanos y porcentajes.
- Acciones de edición y eliminación diferenciadas.
- Integración visual con el módulo general de clientes.

## 14. Vendedores

### 14.1 Asignación

- Los pedidos pueden conservar un vendedor asignado.
- La lista de vendedores respeta la empresa activa.
- Los usuarios autorizados pueden cambiar la asignación.
- El cambio queda registrado para trazabilidad.

### 14.2 Rendimiento

Se implementó un informe con:

- Total vendido por vendedor.
- Cantidad de pedidos.
- Cantidad de facturas.
- Notas de crédito.
- Documentos recientes.
- Productos más vendidos.
- Comparación dentro de la empresa.
- Aislamiento de información entre empresas.

## 15. Flujo de ventas

Se creó una vista operativa que cruza solicitudes comerciales y pedidos:

`Solicitud -> Pedido -> Remito -> Factura -> Cobro`

Incluye:

- Solicitudes nuevas.
- Propuestas esperando respuesta del cliente.
- Borradores operativos.
- Pedidos listos para remito.
- Pedidos listos para factura.
- Cobros pendientes.
- Búsqueda por cliente.
- Filtro por empresa.
- Estado actual y siguiente paso.
- Referencias a documentos relacionados.
- Acciones directas para abrir la operación, cobros y documentos.

El frontend se compactó para mostrar el avance comercial sin obligar a cambiar de módulo constantemente.

## 16. Pedidos y cotizaciones

### 16.1 Ficha operativa rediseñada

La ficha del pedido se transformó en un espacio de trabajo de ancho completo:

- Cabecera con tipo, número, estado, cliente, empresa y fecha.
- Acciones principales agrupadas.
- Resumen de subtotal, descuento, neto, IVA, total y saldo.
- Flujo visual de la operación.
- Panel de datos básicos.
- Panel de cliente.
- Datos adicionales plegables.
- Moneda, lista de precio, depósito y documentos vinculables.
- Tabla de productos.
- Estado vacío profesional cuando no hay ítems.
- Observaciones separadas.
- Totales claramente jerarquizados.
- Línea de tiempo técnica plegable.
- Diseño responsive sin desborde horizontal.

### 16.2 Reglas comerciales

- Asociación con cliente, empresa y vendedor.
- Descuento por categoría del cliente.
- Precio manual permitido con controles.
- Observación obligatoria si el precio queda por debajo del costo.
- Conservación del costo utilizado para auditoría.
- Integración con cobros y documentos.

### 16.3 Validación y publicación

- 3 pruebas enfocadas aprobadas.
- `manage.py check` aprobado.
- Revisión visual de escritorio y móvil.
- Publicación en el sitio oficial.
- Respaldo: `/var/backups/webflexs/order_ui_20260731_123216`.

## 17. Documentos fiscales

### 17.1 Ficha fiscal rediseñada

- Cabecera con tipo de comprobante y estado.
- Indicadores de total, pagado, saldo y cantidad de productos.
- Acciones agrupadas por finalidad.
- Panel de datos fiscales.
- Panel de cliente.
- Estados de comprobante y cuenta corriente claramente visibles.
- Secciones plegables para datos adicionales, moneda, movimientos y listas.
- Tabla de productos con precio, IVA, bonificación e importe.
- Estado vacío profesional.
- Observaciones y totales en áreas separadas.
- Panel técnico y trazabilidad plegables.
- Diseño responsive.

La mejora fue exclusivamente visual y de navegación. Durante las pruebas no se emitieron, anularon ni modificaron comprobantes sensibles.

### 17.2 Funciones y resguardos

- Modelos de documentos fiscales.
- Asociación con cliente, pedido, empresa y punto de venta.
- Generación de PDF.
- Generación de QR.
- Registro de CAE y vencimiento.
- Estados de preparación, envío, autorización, rechazo e incertidumbre.
- Inmutabilidad después del CAE.
- Prevención de cambios que invaliden el comprobante.
- Recuperación y consulta antes de reintentar una emisión incierta.

### 17.3 IVA

- El producto conserva el precio neto.
- Cada ítem guarda una instantánea de la alícuota utilizada.
- El documento calcula neto, IVA, impuestos y total.
- La factura electrónica presenta el importe final con IVA.

### 17.4 Validación y publicación

- Prueba enfocada del detalle fiscal aprobada.
- `manage.py check` aprobado.
- Revisión visual en escritorio y móvil.
- Publicación en el sitio oficial.
- Respaldo: `/var/backups/webflexs/fiscal_ui_20260731_133918`.

## 18. Stock y depósitos

### 18.1 Stock opcional

- Cada producto puede decidir si controla stock.
- Un producto puede permanecer visible y vendible aunque no utilice existencias.
- El catálogo no obliga a todos los artículos a administrar stock.

### 18.2 Capa ampliada por depósito

Se implementó:

- Configuración general de depósitos.
- Activación individual.
- Saldos por producto y depósito.
- Inicialización controlada.
- Confirmación y observación para operaciones sensibles.
- Prevención de doble descuento mediante idempotencia.
- Compatibilidad temporal con el campo de stock anterior.
- Actualización al autorizar documentos cuando corresponda.
- Permiso para stock negativo por producto.
- Separación entre venta, compra y control de stock.

La activación productiva completa requiere migración, inicialización de saldos, comparación con el stock actual y una prueba controlada.

## 19. Configuración

Se reorganizó el centro de configuración:

- Accesos a facturación electrónica.
- Reportes fiscales.
- Empresas.
- Tipos de venta.
- Depósitos.
- Administradores.
- Importaciones.
- Exportaciones.
- Diagnóstico del catálogo.
- Acciones rápidas hacia empresas, fiscal e importación.
- Tarjetas consistentes con explicación y acción.
- Mejor distribución en escritorio y móvil.

## 20. Multiempresa, permisos y auditoría

### 20.1 Multiempresa

- Empresa activa visible.
- Cambio de empresa desde la navegación.
- Clientes, pedidos, tareas, documentos, marcas, depósitos e informes filtrados por empresa.
- Prevención de acceso cruzado.

### 20.2 Permisos

- Restricción para consultar costos.
- Restricción para modificar datos fiscales.
- Restricción de acciones administrativas sensibles.
- Operaciones disponibles según la empresa y capacidades del usuario.

### 20.3 Auditoría

- Observaciones obligatorias para excepciones.
- Registro de cambios de vendedor.
- Seguimiento de tareas de clientes.
- Historial de producto y stock.
- Registro de configuración e inicialización.
- Instantáneas de datos usados en documentos.
- Bloqueo de modificaciones luego del CAE.

## 21. Importación, exportación, API y editor externo

- Importación y exportación de clientes.
- Exportación del catálogo en Excel.
- Editor masivo de productos.
- API y webhooks desde configuración.
- Validaciones reforzadas en serializadores y endpoints.
- Protección de campos comerciales y fiscales.
- Procesamiento de trabajos del editor externo.
- Manejo de errores y seguimiento.
- Documentación en [EXTERNAL_EDITOR_INTEGRATION.md](./EXTERNAL_EDITOR_INTEGRATION.md).

Los archivos compilados del catálogo externo continúan requiriendo limpieza y consolidación antes de registrarlos definitivamente en Git.

## 22. Investigación del SaaS de referencia

Se analizó el comportamiento del sistema SaaS Gestión usado como referencia, sin realizar facturaciones ni modificar datos sensibles.

Se tomaron como ideas valiosas:

- Ficha integral del cliente.
- Separación entre datos de contacto, facturación y comerciales.
- Cuenta corriente con movimientos.
- Acceso a pagos, facturas, remitos, pedidos y presupuestos.
- Acciones rápidas por movimiento.
- Ciclo comercial conectado.
- Historial y trazabilidad.
- Vendedor asignado.
- Categoría, descuento y lista de precios.
- Estado del cliente y límites de cuenta corriente.

WebFlexs adoptó estas ideas con una interfaz propia, más moderna y compatible con la arquitectura multiempresa existente.

## 23. Integración con ARCA

### 23.1 Arquitectura implementada o preparada

- Integración exclusiva desde backend.
- Separación estricta entre homologación y producción.
- Autenticación WSAA.
- Cliente WSFEv1.
- Caché de tickets.
- Parámetros fiscales.
- Bloqueos para evitar emisiones duplicadas.
- Numeración y reservas protegidas.
- Hash canónico de solicitud.
- Estado de resultado incierto.
- Consulta y recuperación mediante `FECompConsultar`.
- Protección de secretos y datos sensibles.
- Comandos de diagnóstico y compuerta de preparación.
- Pruebas de PDF, QR, rechazo, incertidumbre y concurrencia.
- Migración de integridad fiscal `core.0033_arca_fiscal_integrity`.
- Identidad fiscal de clientes mediante `accounts.0019_client_fiscal_identity`.

### 23.2 Resultado de la preparación offline

La preparación local fue aprobada:

- Pruebas dirigidas aprobadas.
- Pruebas fiscales con PostgreSQL aprobadas.
- Pruebas con SQLite aprobadas.
- Migraciones coherentes.
- Validación binaria de PDF resuelta.
- QR validado según el estado del documento.
- Casos de concurrencia e incertidumbre aprobados.
- Diagnóstico fail-closed.
- `FECAESolicitar` bloqueado mientras no se cumplan las precondiciones.

### 23.3 Estado real de homologación

La conexión real todavía no se ejecutó. Continúan pendientes:

- Ingreso del usuario a WSASS.
- Generación local de clave y CSR.
- Certificado de testing.
- Autorización del certificado, CUIT y servicio.
- Confirmación del identificador de servicio.
- Confirmación del CUIT representado.
- Punto de venta WSFEv1.
- Tipo de comprobante de prueba.
- Caché compartida.
- Variables locales seguras.
- Activación explícita de la compuerta de solo lectura.

El estado esperado de la compuerta sigue siendo:

`ARCA_HOMOLOGATION_READINESS_GATE=FAIL`

Esto es un control de seguridad deliberado: no se intenta conectar ni emitir hasta que todos los requisitos estén completos y verificados.

### 23.4 Acciones prohibidas mientras esté bloqueado

- No usar credenciales productivas para pruebas.
- No guardar claves privadas en el repositorio.
- No registrar Token, Sign, certificados o contraseñas en logs.
- No emitir comprobantes reales.
- No reintentar ciegamente después de un timeout.
- No habilitar producción antes de terminar homologación.

## 24. Migraciones destacadas

- `accounts.0018_clientprofile_commercial_observation_clienttask`
- `accounts.0019_client_fiscal_identity`
- `catalog.0029_product_allow_negative_stock_product_is_purchasable_and_more`
- `catalog.0030_brand_cataloging_workflow`
- `catalog.0031_brandcatalogbatch_reversible_removals`
- `core.0031` y `core.0032` para la ampliación de stock y depósitos
- `core.0033_arca_fiscal_integrity`

Antes de ejecutar migraciones pendientes en producción se debe crear un respaldo y revisar el plan exacto del módulo correspondiente.

## 25. Pruebas y validaciones incorporadas

Las pruebas cubren, entre otros puntos:

- Seguridad fiscal.
- Inmutabilidad de documentos.
- Estados inciertos y recuperación.
- Concurrencia y numeración fiscal.
- Generación de PDF y QR.
- Precio por debajo del costo.
- Aislamiento multiempresa.
- Tareas de clientes.
- Cambios de estado con observación.
- Rendimiento de vendedores.
- Ficha integral del producto.
- Línea de tiempo del producto.
- Ocultamiento de costos.
- Stock por depósito.
- Idempotencia de movimientos.
- Productos sin control de stock.
- Clasificación por marcas.
- Autosincronización y reversión.
- Ficha de pedidos.
- Ficha de documentos fiscales.
- Diseño de escritorio y móvil.

Archivos destacados:

- `admin_panel/test_client_tasks.py`
- `admin_panel/test_product_workspace.py`
- `admin_panel/test_seller_performance.py`
- `admin_panel/test_warehouse_stock_ui.py`
- `catalog/test_product_timeline.py`
- `catalog/tests_brand_cataloging.py`
- `core/test_fiscal_readiness.py`
- `core/test_arca_offline_commands.py`
- `core/test_warehouse_stock.py`

## 26. Archivos principales modificados recientemente

### Marcas

- `catalog/models.py`
- `catalog/services/brand_cataloging.py`
- `catalog/migrations/0031_brandcatalogbatch_reversible_removals.py`
- `admin_panel/views/brands.py`
- `admin_panel/views/products.py`
- `admin_panel/urls.py`
- `admin_panel/templates/admin_panel/brands/_brand_product_workspace.html`
- `core/static/core/css/brand_product_workspace.css`
- `core/static/core/js/brand_product_workspace.js`

### Pedidos

- `admin_panel/templates/admin_panel/orders/detail.html`
- `admin_panel/templates/admin_panel/orders/_order_items_rows.html`
- `core/static/core/css/order_detail.css`

### Fiscal

- `admin_panel/templates/admin_panel/fiscal/detail.html`
- `core/static/core/css/fiscal_detail.css`

### Navegación y frontend general

- `admin_panel/templates/admin_panel/base.html`
- `templates/base.html`
- `core/static/core/css/base.css`
- `core/static/core/css/admin_ux.css`
- `core/static/core/js/main.js`

## 27. Guía para revisar los cambios en la página oficial

### 27.1 Página pública

Abrir:

`https://flexsrepuestos.shop/`

Revisar:

- Cabecera.
- Navegación.
- Tarjetas de servicios.
- Imagen de fabricación propia.
- Familias de productos.
- Ausencia de emojis en los bloques rediseñados.

### 27.2 Catálogo

Abrir el botón “Ver catálogo” desde la página pública.

Revisar:

- Panel de filtros.
- Buscador.
- Selector tarjetas/lista.
- Ordenamiento.
- Tarjetas de productos.
- Descarga de Excel.
- Acceso a abrazaderas a medida.

### 27.3 Panel administrativo

Abrir:

`https://flexsrepuestos.shop/admin-panel/`

Revisar:

- Cotizador permanente.
- Acceso al inicio público.
- Botones de herramientas en la barra.
- Resumen comercial.
- Cola operativa.
- Actividad reciente.

### 27.4 Productos

Abrir:

`https://flexsrepuestos.shop/admin-panel/productos/`

Revisar filtros, búsqueda, vistas, acciones masivas, duplicados, estados, categorías y botones de acción.

### 27.5 Marcas

Abrir:

`https://flexsrepuestos.shop/admin-panel/marcas/`

Revisar:

- Marcas plegables.
- Rubros y subrubros.
- Filtros.
- Botones de productos.
- Asignación manual y masiva.
- Vista previa de autosincronización.

No confirmar una sincronización real durante una revisión visual si no se desea modificar la clasificación.

### 27.6 Clientes

Abrir:

`https://flexsrepuestos.shop/admin-panel/clientes/`

Revisar indicadores, clientes recientes, distribución por categorías y accesos del módulo.

Categorías:

`https://flexsrepuestos.shop/admin-panel/clientes/categorias/`

Revisar descuentos, condición de venta, límites, lista de precio, estado y cantidad de clientes.

### 27.7 Ventas

Abrir:

`https://flexsrepuestos.shop/admin-panel/ventas/`

Revisar la secuencia Solicitud, Pedido, Remito, Factura y Cobro.

### 27.8 Pedidos y fiscal

Abrir un pedido o comprobante de prueba ya existente solamente para inspección visual.

Revisar:

- Cabecera.
- Estados.
- Acciones agrupadas.
- Datos y productos.
- Totales.
- Secciones plegables.
- Comportamiento responsive.

No emitir, anular, cerrar ni modificar comprobantes durante una revisión visual.

## 28. Pendientes actuales

### Prioridad alta

1. Mantener respaldados y separados los cambios locales todavía no consolidados en Git.
2. Revisar el conjunto completo de migraciones antes del siguiente despliegue estructural.
3. Completar las acciones del usuario para homologación ARCA.
4. Ejecutar primero las pruebas reales de solo lectura en homologación.
5. Validar punto de venta, CUIT representado y tipos de comprobante.
6. Realizar una aceptación funcional controlada de marcas, pedidos y fiscal en producción.
7. Configurar o confirmar una política automática de backups.

### Prioridad media

1. Publicar o terminar de habilitar la bandeja de tareas comerciales.
2. Publicar o terminar de habilitar el informe de vendedores.
3. Activar la ficha integral del producto para los perfiles correspondientes.
4. Preparar la inicialización de stock por depósitos.
5. Limpiar los recursos compilados antiguos del catálogo externo.
6. Completar la consulta oficial de datos fiscales por CUIT.

### Antes de emitir con ARCA

1. Completar WSASS, clave, CSR y certificado de testing.
2. Autorizar servicio y CUIT.
3. Crear o confirmar el punto de venta WSFEv1.
4. Ejecutar el diagnóstico sin exponer secretos.
5. Obtener `PASS` en la compuerta.
6. Probar solamente operaciones de lectura.
7. Probar emisión controlada en homologación.
8. Probar rechazo, timeout, consulta, recuperación y nota de crédito.
9. Verificar PDF, QR, CAE y vencimiento.
10. Aprobar el procedimiento de soporte y reversión.
11. Recién entonces evaluar la activación productiva.

## 29. Documentación relacionada

### General

- [ARCA_QUICK_CONFIG.md](./ARCA_QUICK_CONFIG.md)
- [COMMERCIAL_FLOW_CONFIRMATION.md](./COMMERCIAL_FLOW_CONFIRMATION.md)
- [DEPLOY_SAFETY_CHECKLIST.md](./DEPLOY_SAFETY_CHECKLIST.md)
- [EXTERNAL_EDITOR_INTEGRATION.md](./EXTERNAL_EDITOR_INTEGRATION.md)
- [PHASE0_PHASE1_ROLLOUT.md](./PHASE0_PHASE1_ROLLOUT.md)
- [PHASE2_TO_6_ROLLOUT.md](./PHASE2_TO_6_ROLLOUT.md)

### ARCA

- [ARCA_PROJECT_AUDIT.md](./arca/ARCA_PROJECT_AUDIT.md)
- [ARCA_OFFICIAL_RESEARCH.md](./arca/ARCA_OFFICIAL_RESEARCH.md)
- [ARCA_ARCHITECTURE.md](./arca/ARCA_ARCHITECTURE.md)
- [ARCA_DATA_MODEL.md](./arca/ARCA_DATA_MODEL.md)
- [ARCA_SECURITY.md](./arca/ARCA_SECURITY.md)
- [ARCA_IMPLEMENTATION_PLAN.md](./arca/ARCA_IMPLEMENTATION_PLAN.md)
- [ARCA_HOMOLOGACION_READONLY_RUNBOOK.md](./arca/ARCA_HOMOLOGACION_READONLY_RUNBOOK.md)
- [ARCA_HOMOLOGACION_ACCIONES_USUARIO.md](./arca/ARCA_HOMOLOGACION_ACCIONES_USUARIO.md)
- [ARCA_HOMOLOGACION_PREPARACION_SEGURA_INFORME_2026-07-30.md](./arca/ARCA_HOMOLOGACION_PREPARACION_SEGURA_INFORME_2026-07-30.md)
- [ARCA_OFFLINE_PRE_CREDENTIALS_EXECUTION_REPORT_2026-07-30.md](./arca/ARCA_OFFLINE_PRE_CREDENTIALS_EXECUTION_REPORT_2026-07-30.md)
- [ARCA_PRE_HOMOLOGATION_EXECUTION_REPORT_2026-07-30.md](./arca/ARCA_PRE_HOMOLOGATION_EXECUTION_REPORT_2026-07-30.md)

## 30. Historial resumido de las mejoras visuales

1. Reorganización de la navegación administrativa.
2. Botón permanente del cotizador.
3. Rediseño del dashboard.
4. Mejora del listado de productos.
5. Botón permanente para volver al inicio.
6. Exposición de “Más herramientas” en la barra.
7. Rediseño de la página pública.
8. Reemplazo de emojis por recursos profesionales.
9. Corrección de la imagen de fabricación propia.
10. Mejora del catálogo público.
11. Mejora de controles desplegables.
12. Mejora del módulo de marcas.
13. Estructuras de marcas plegables.
14. Mejora de configuración.
15. Mejora del panel de clientes.
16. Mejora de categorías de clientes.
17. Mejora del seguimiento de ventas.
18. Nuevo espacio de clasificación masiva por marcas.
19. Rediseño de la ficha de pedidos.
20. Rediseño de la ficha de documentos fiscales.

## 31. Conclusión

WebFlexs ya dispone de una base comercial sólida, un catálogo amplio, un panel administrativo más ordenado y herramientas específicas para clientes, ventas, productos, marcas y documentos. Las mejoras más recientes de marcas, pedidos y fiscal están publicadas en la página oficial y cuentan con respaldos independientes.

El sistema fiscal avanzó considerablemente en seguridad, integridad, PDF, QR, concurrencia, recuperación y diagnóstico. Sin embargo, la emisión real continúa bloqueada correctamente hasta que el usuario complete las credenciales y autorizaciones de homologación.

El siguiente objetivo recomendado es cerrar la aceptación funcional de los módulos ya publicados, consolidar los cambios locales por bloques y completar la preparación de homologación ARCA sin utilizar datos productivos ni realizar facturación real.
