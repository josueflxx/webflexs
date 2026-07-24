# Avances del proyecto WebFlexs

**Fecha de corte:** 24 de julio de 2026  
**Proyecto:** Flexs Repuestos / WebFlexs  
**Repositorio local:** `C:\Users\Brian\Desktop\webflexs`  
**Sitio oficial:** `https://flexsrepuestos.shop`

## 1. Objetivo del documento

Este documento reúne los avances funcionales, técnicos y visuales realizados durante el desarrollo de WebFlexs. También separa el estado real de cada trabajo para evitar confundir:

- **OFICIAL:** integrado en la rama oficial y/o comprobado en el sitio publicado.
- **OFICIAL PENDIENTE DE CONSOLIDAR:** publicado manualmente, pero todavía pendiente de ordenar y registrar completamente en Git.
- **LOCAL:** implementado en el entorno local y todavía no desplegado en producción.
- **PLANIFICADO:** analizado o diseñado, pero todavía no implementado.

## 2. Resumen ejecutivo

WebFlexs evolucionó de un catálogo y panel administrativo básico hacia una plataforma comercial con:

- Catálogo de productos, categorías, marcas, proveedores y listas.
- Administración multiempresa.
- Clientes con información comercial y fiscal.
- Categorías de clientes y descuentos.
- Pedidos, cobros y cuenta corriente.
- Seguimiento de vendedores.
- Documentos fiscales y resguardos de seguridad.
- Stock opcional por producto.
- Panel administrativo reorganizado.
- Cotizador accesible desde todo el panel.
- Página pública renovada con imágenes profesionales.
- Base técnica para integrar facturación electrónica con ARCA.

La facturación electrónica real con ARCA todavía no está habilitada en producción. Se completó el análisis técnico y la arquitectura propuesta, pero faltan la implementación final, homologación, certificados y pruebas controladas.

## 3. Decisiones funcionales acordadas

Durante el análisis se definieron las siguientes reglas de negocio:

1. Las empresas deberán darse de alta oficialmente con ARCA, aunque sus datos ya existan en WebFlexs.
2. Los precios del catálogo se guardan y muestran sin IVA.
3. En una factura electrónica, el sistema debe calcular el IVA y mostrar el precio final correspondiente.
4. El usuario carga el CUIT y el sistema debe intentar completar los demás datos mediante una consulta oficial.
5. Si la consulta automática no es concluyente, el cliente se envía a revisión manual.
6. Los comprobantes electrónicos inicialmente contemplados son:
   - Factura A.
   - Factura B.
   - Nota de crédito A.
   - Nota de crédito B.
7. Un comprobante que ya obtuvo CAE queda inmutable.
8. Una corrección posterior al CAE debe resolverse mediante una nota de crédito, no editando la factura autorizada.
9. Los vendedores sirven para identificar quién realizó cada venta y generar estadísticas.
10. Los usuarios autorizados pueden cambiar el vendedor asignado.
11. Los descuentos se administran mediante categorías de clientes.
12. Cada categoría de cliente puede tener su propio descuento.
13. El uso de stock es opcional para cada producto.
14. Se permite vender por debajo del costo solamente con una observación obligatoria.
15. La observación debe quedar registrada para auditoría.

## 4. Infraestructura y publicación

### Estado oficial

- Sitio alojado en un VPS con Ubuntu.
- Dominio oficial: `flexsrepuestos.shop`.
- Aplicación desplegada en el servidor bajo `/var/www/webflexs`.
- Acceso SSH configurado mediante una clave dedicada de despliegue.
- Uso de Gunicorn y Nginx para servir la aplicación.
- Base de datos PostgreSQL en producción.
- Redis y Celery contemplados en la arquitectura actual.
- Procedimientos de respaldo utilizados antes de reemplazar archivos sensibles del frontend.

### Seguridad operativa aplicada

- No se eliminaron ni reemplazaron datos de clientes durante los despliegues.
- Los cambios visuales se hicieron sin alterar el orden ni el contenido comercial existente.
- Los certificados y secretos fiscales están excluidos del control de versiones.
- Se incorporaron validaciones para impedir que información fiscal sensible aparezca en respuestas o registros inseguros.

## 5. Panel administrativo

### Mejoras oficiales

Se reorganizó el panel para que las herramientas de uso diario sean más fáciles de encontrar.

- Navegación principal simplificada.
- Accesos directos a:
  - Dashboard.
  - Productos.
  - Clientes.
  - Pedidos.
  - Cobros.
  - Área fiscal.
- Menú de “Más herramientas” para las funciones menos frecuentes.
- Barra superior más compacta.
- Mejor adaptación a diferentes tamaños de pantalla.
- Jerarquía visual más clara entre navegación, indicadores y áreas operativas.

### Cotizador persistente

**Estado: OFICIAL**

- El cotizador quedó disponible desde un botón fijo de la cabecera del panel administrativo.
- El acceso se mantiene al navegar por las distintas secciones.
- También permanece disponible dentro del menú de herramientas.
- Se priorizó visualmente por tratarse de una herramienta de uso frecuente.

### Botón para volver al inicio público

**Estado: OFICIAL PENDIENTE DE CONSOLIDAR**

- Se agregó un acceso al inicio público desde la cabecera del panel.
- Está pensado para permanecer visible en todas las páginas administrativas.
- Conviene consolidar este cambio junto con los demás archivos locales antes del próximo despliegue general.

## 6. Dashboard

### Mejoras oficiales

El dashboard fue reorganizado para reducir el desorden visual y presentar la información según su importancia.

- Encabezado con resumen de la empresa activa.
- Accesos rápidos a pedidos, clientes, cobros y cambio de empresa.
- Bloque separado para indicadores comerciales.
- Indicadores incluidos:
  - Facturado hoy.
  - Facturado durante el mes.
  - Ticket promedio.
  - Margen estimado.
  - Pedidos pendientes.
  - Stock crítico.
- Cola operativa para visualizar pendientes relevantes.
- Actividad reciente separada de los indicadores.
- Tarjetas con mejor alineación, espaciado y contraste.
- Mejor comportamiento responsive.

### Ampliaciones locales

**Estado: LOCAL**

- Indicadores de tareas de clientes.
- Conteos de tareas pendientes y vencidas.
- Integración prevista con la bandeja global de tareas.

## 7. Página pública

### Rediseño visual

**Estado: OFICIAL PENDIENTE DE CONSOLIDAR**

La página principal pública fue actualizada y comprobada en el dominio oficial.

- Reemplazo de emojis por recursos visuales profesionales.
- Uso de iconos vectoriales e imágenes coherentes con el rubro.
- Tarjetas de servicios con imágenes reales o generadas específicamente para la página.
- Mejora del contraste, las proporciones y el tratamiento visual de las secciones.
- Conservación de la identidad oscura con acentos naranjas.

### Imágenes incorporadas

Se agregaron las siguientes imágenes:

- `core/static/core/img/home/service-stock.webp`
- `core/static/core/img/home/service-manufacturing.webp`
- `core/static/core/img/home/service-delivery.webp`

La imagen de “Fabricación propia” fue corregida para mostrar una abrazadera U-bolt alta, con:

- Forma rectangular.
- Roscas visibles en ambos extremos.
- Plaqueta recta.
- Dos perforaciones.
- Tuercas separadas.
- Entorno de taller coherente con la fabricación de repuestos.

El archivo fue publicado y respondió correctamente desde el sitio oficial. También se actualizó la versión del recurso para evitar que el navegador mostrara una copia anterior en caché.

## 8. Productos y catálogo

### Mejoras oficiales del listado

- Nuevo encabezado para buscar y filtrar productos.
- Filtros por categoría, estado y orden.
- Búsqueda por SKU, nombre o proveedor.
- Acciones para:
  - Guardar vistas.
  - Cargar vistas.
  - Crear un producto.
  - Revisar duplicados.
- Selección múltiple y acciones masivas.
- Mejor organización de las columnas.
- Indicadores visuales para:
  - Estado activo.
  - Visibilidad en catálogo.
  - Categorías.
  - Productos sin imagen.
  - Códigos duplicados.
- Acciones de editar y eliminar mantenidas visibles.
- Mejor distribución en resoluciones grandes y pequeñas.

### Reglas comerciales incorporadas

- Precio base del catálogo sin IVA.
- Campo de alícuota de IVA por producto.
- Stock opcional por producto.
- Posibilidad de distinguir si un producto controla stock.
- Validaciones para mantener consistencia en importaciones y ediciones externas.
- Protección frente a cambios no autorizados desde el editor externo.

### Espacio de trabajo del producto

**Estado: LOCAL**

Se desarrolló una nueva ficha integral de producto con:

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
- Registros de auditoría.

También se creó una línea de tiempo que combina movimientos y cambios relevantes. El costo se oculta para los usuarios que no tienen permisos para verlo.

## 9. Clientes

### Base comercial y fiscal

Se incorporaron o consolidaron:

- Datos de contacto.
- Datos fiscales.
- CUIT.
- Condición frente al IVA.
- Categoría comercial.
- Descuento.
- Cuenta corriente.
- Pedidos y documentos asociados.
- Empresa a la que pertenece el cliente.
- Validación de duplicados por CUIT.

### Consulta por CUIT

**Estado actual: PARCIAL**

- El usuario puede ingresar un CUIT.
- El sistema valida su formato.
- Se comprueba si ya existe un cliente con ese CUIT.
- Existe una estructura de consulta y carga asistida.
- Cuando no se obtienen datos concluyentes, se utiliza un flujo manual.

La obtención oficial y completa de los datos desde ARCA todavía no está terminada. No debe presentarse la carga actual como una consulta oficial hasta completar la integración correspondiente.

### Revisión fiscal manual

**Estado: OFICIAL**

- Se creó la entidad `ClientFiscalReview`.
- Los casos dudosos o incompletos pueden enviarse a una cola de revisión.
- Se incorporaron pantallas para inspeccionar estos casos.
- La revisión queda vinculada a la empresa y al cliente correspondiente.

### Tareas y seguimiento del cliente

**Estado: LOCAL**

Se implementó un sistema de tareas comerciales:

- Título.
- Descripción.
- Estado:
  - Pendiente.
  - Completada.
  - Cancelada.
- Prioridad.
- Responsable.
- Fecha de vencimiento.
- Observación comercial.
- Registro de creación y cambios.
- Bandeja por cliente.
- Bandeja global.
- Filtros para tareas propias o del equipo.
- Identificación de tareas vencidas.
- Aislamiento por empresa.

Los cambios de estado requieren una observación y quedan auditados.

## 10. Vendedores

### Asignación comercial

**Estado: LOCAL**

- Los pedidos pueden tener un vendedor asignado.
- Los usuarios autorizados pueden cambiar el vendedor.
- La modificación queda registrada.
- Las opciones de vendedores respetan el contexto de la empresa.

### Informe de rendimiento

**Estado: LOCAL**

Se creó un informe para conocer quién vendió qué:

- Total vendido por vendedor.
- Cantidad de pedidos.
- Cantidad de facturas.
- Notas de crédito.
- Detalle de documentos recientes.
- Productos más vendidos.
- Comparación dentro de la empresa activa.
- Aislamiento de información entre empresas.

Las estadísticas utilizan el vendedor actualmente asignado a cada operación.

## 11. Pedidos, precios y rentabilidad

### Pedidos

- Gestión de pedidos dentro del panel.
- Estados y seguimiento operativo.
- Asociación con cliente, empresa y vendedor.
- Integración con cobros y documentos.
- Registro de actividad reciente.

### Precio manual por debajo del costo

**Estado: OFICIAL**

- Se permite modificar el precio de una operación.
- Si el precio queda por debajo del costo, se exige una observación.
- La observación se guarda en el ítem de la operación.
- Se conserva una referencia del costo utilizado.
- La decisión queda disponible para auditoría.

### Descuentos por categoría de cliente

**Estado: OFICIAL**

- Cada cliente puede pertenecer a una categoría.
- La categoría puede definir un porcentaje de descuento.
- El descuento puede aplicarse en el flujo comercial.
- La categoría se administra desde la edición del cliente.

## 12. Facturación y documentos fiscales

### Funciones existentes

- Modelos de documentos fiscales.
- Asociación de documentos con clientes y operaciones.
- Generación de PDF.
- Generación de QR.
- Registro de CAE cuando se obtiene una autorización.
- Seguimiento del estado de emisión.
- Protección de documentos autorizados.

### Inmutabilidad

**Estado: OFICIAL**

- Un documento que posee CAE no puede modificarse libremente.
- Se impiden cambios que comprometan la validez fiscal.
- La corrección debe resolverse con el documento fiscal correspondiente.

### Alcance previsto de ARCA

La primera versión de facturación electrónica cubrirá:

- Factura A.
- Factura B.
- Nota de crédito A.
- Nota de crédito B.

No se incluyeron notas de débito en el alcance inicial.

### Cálculo de IVA

- Los productos conservan precios netos.
- Cada ítem puede conservar una instantánea de la alícuota utilizada.
- El documento electrónico debe calcular neto, IVA y total.
- El total del comprobante debe reflejar el precio final con IVA.

## 13. Stock y depósitos

### Stock opcional

**Estado: OFICIAL**

- Cada producto puede decidir si utiliza control de stock.
- Un producto puede permanecer en el catálogo aunque no controle existencias.
- La lógica comercial no obliga a todos los productos a utilizar stock.

### Sistema ampliado de depósitos

**Estado: LOCAL**

Se desarrolló una nueva capa de stock por depósito:

- Activación general desde la configuración.
- Activación individual de depósitos.
- Saldos por producto y depósito.
- Inicialización controlada.
- Confirmación y observación obligatorias para operaciones sensibles.
- Comando de inicialización de saldos.
- Registro de acciones administrativas.
- Prevención de doble descuento mediante operaciones idempotentes.
- Compatibilidad temporal con el stock anterior.
- Actualización de stock al autorizar documentos.

También se agregaron opciones para:

- Permitir o impedir stock negativo.
- Definir si un producto se puede comprar.
- Separar la venta, compra, visibilidad y control de stock.

El nuevo sistema de depósitos todavía no debe habilitarse en producción hasta ejecutar las migraciones, inicializar saldos y completar una prueba controlada.

## 14. Multiempresa, permisos y auditoría

### Multiempresa

- Cada operación se ejecuta en el contexto de una empresa activa.
- Los usuarios pueden tener acceso a una o varias empresas.
- Clientes, pedidos, tareas, depósitos e informes se filtran por empresa.
- Se evita el acceso cruzado a registros de otras empresas.

### Permisos

- Capacidades específicas para distintas áreas.
- Restricciones para visualizar costos.
- Restricciones para modificar información fiscal.
- Restricciones para realizar acciones administrativas sensibles.

### Auditoría

- Registro de cambios relevantes.
- Observaciones obligatorias para excepciones comerciales.
- Seguimiento de cambios de vendedor.
- Seguimiento de tareas de clientes.
- Registro de inicialización y configuración de stock.
- Resguardo de los datos utilizados al emitir documentos.

## 15. Editor externo y API

Se reforzó la integración con herramientas externas:

- Validaciones adicionales en serializadores y endpoints.
- Protección de campos comerciales sensibles.
- Manejo de trabajos del editor externo.
- Mejor tratamiento de errores.
- Documentación específica en:
  - `docs/EXTERNAL_EDITOR_INTEGRATION.md`
- Scripts de despliegue y compilaciones del catálogo externo actualizados localmente.

Antes de publicar estos archivos debe revisarse el conjunto de recursos compilados para eliminar versiones obsoletas y conservar solamente la compilación correcta.

## 16. Investigación e integración con ARCA

### Estado

**Estado: PLANIFICADO Y DOCUMENTADO**

Se completó una auditoría profunda del proyecto y una investigación de los servicios oficiales necesarios. No se emitieron comprobantes reales ni se utilizaron certificados productivos.

### Arquitectura propuesta

- Integración exclusivamente desde el backend.
- Ambiente de homologación antes de producción.
- Autenticación mediante WSAA.
- Emisión mediante `wsfev1`.
- Almacenamiento seguro de certificados y claves.
- Bloqueos distribuidos para evitar emisiones duplicadas.
- Uso de un hash canónico de la solicitud.
- Estado explícito para emisiones inciertas.
- Reconciliación mediante `FECompConsultar`.
- Registros técnicos sin exponer secretos.
- Separación clara entre preparación, envío, autorización, error e incertidumbre.

### Riesgos críticos detectados

1. Falta completar `CondicionIVAReceptorId` en la solicitud fiscal.
2. Un reintento posterior a un timeout no debe enviar ciegamente el mismo comprobante.
3. Antes de reintentar debe consultarse ARCA para verificar si el comprobante ya fue autorizado.
4. La consulta actual por CUIT no constituye todavía una carga oficial completa.
5. Debe revisarse y actualizarse la URL utilizada en el QR fiscal.
6. Falta definir el manejo seguro y operativo de certificados por empresa.

### Documentación creada

La investigación se encuentra en:

- `docs/arca/ARCA_PROJECT_AUDIT.md`
- `docs/arca/ARCA_OFFICIAL_RESEARCH.md`
- `docs/arca/ARCA_ARCHITECTURE.md`
- `docs/arca/ARCA_DATA_MODEL.md`
- `docs/arca/ARCA_SECURITY.md`
- `docs/arca/ARCA_OPEN_QUESTIONS.md`
- `docs/arca/ARCA_IMPLEMENTATION_PLAN.md`

También se conserva la guía previa:

- `docs/ARCA_QUICK_CONFIG.md`

## 17. Pruebas incorporadas

Se agregaron o ampliaron pruebas para:

- Seguridad fiscal.
- Inmutabilidad de documentos.
- Validación de precios por debajo del costo.
- Aislamiento multiempresa.
- Tareas de clientes.
- Cambios de estado con observación.
- Filtros de tareas propias y del equipo.
- Tareas vencidas.
- Bandeja global.
- Estadísticas de vendedores.
- Espacio de trabajo de productos.
- Línea de tiempo de productos.
- Ocultamiento de costos sin permiso.
- Inicialización de stock por depósito.
- Idempotencia de movimientos de stock.
- Compatibilidad con productos que no controlan stock.
- Restricciones de acceso entre empresas.

Archivos de prueba destacados:

- `admin_panel/test_client_tasks.py`
- `admin_panel/test_product_workspace.py`
- `admin_panel/test_seller_performance.py`
- `admin_panel/test_warehouse_stock_ui.py`
- `catalog/test_product_timeline.py`
- `core/test_warehouse_stock.py`

## 18. Cambios consolidados en Git

La rama local actual es:

`codex/production-fiscal-client-upgrade-20260723`

El punto consolidado coincide con `origin/main` en el commit:

`1ce43cd`

Principales commits del avance:

- `6155827` — Implementación de resguardos fiscales, clientes y controles comerciales.
- `711f036` — Mejora de navegación y experiencia responsive del panel.
- `5bd7ae0` — Acceso permanente al cotizador.
- `a317578` — Reorganización visual del dashboard.
- `f3f1bfa` — Mejora del listado de productos.
- `1ce43cd` — Acciones de productos mantenidas visibles.

## 19. Cambios locales pendientes de consolidar

Actualmente existe un conjunto importante de modificaciones locales sin commit. Incluye:

- Tareas comerciales de clientes.
- Observaciones comerciales.
- Informe de rendimiento de vendedores.
- Nueva ficha integral de producto.
- Línea de tiempo de producto.
- Stock por depósitos.
- Nuevas migraciones de base de datos.
- Ajustes del botón de inicio del panel.
- Recursos visuales de la página pública.
- Documentación de ARCA.
- Cambios del editor externo.
- Recursos compilados del catálogo externo.

Estos cambios no deben desplegarse todos juntos sin:

1. Revisar el diff completo.
2. Separar los cambios por módulo.
3. Ejecutar migraciones en un entorno de prueba.
4. Correr las pruebas.
5. Verificar permisos y aislamiento por empresa.
6. Preparar un respaldo.
7. Publicar por etapas.

## 20. Estado por área

| Área | Estado actual |
|---|---|
| Navegación principal del panel | OFICIAL |
| Cotizador persistente | OFICIAL |
| Dashboard reorganizado | OFICIAL |
| Listado de productos mejorado | OFICIAL |
| Resguardos fiscales y comerciales | OFICIAL |
| Revisión fiscal manual de clientes | OFICIAL |
| Precio bajo costo con observación | OFICIAL |
| Stock opcional por producto | OFICIAL |
| Página pública con imágenes profesionales | OFICIAL PENDIENTE DE CONSOLIDAR |
| Botón para volver al inicio público | OFICIAL PENDIENTE DE CONSOLIDAR |
| Tareas de clientes | LOCAL |
| Estadísticas de vendedores | LOCAL |
| Ficha integral de producto | LOCAL |
| Stock por depósitos | LOCAL |
| Documentación técnica de ARCA | LOCAL |
| Consulta oficial de CUIT en ARCA | PLANIFICADO |
| Facturación electrónica real con ARCA | PLANIFICADO |
| Homologación fiscal | PENDIENTE |
| Certificados productivos por empresa | PENDIENTE |

## 21. Pendientes prioritarios

### Prioridad alta

1. Ordenar y separar los cambios locales.
2. Ejecutar toda la suite de pruebas.
3. Consolidar en Git los cambios visuales ya publicados manualmente.
4. Probar migraciones de tareas, productos y depósitos en una copia de la base.
5. Definir qué módulos locales entrarán en el próximo despliegue.
6. Implementar el estado fiscal `uncertain`.
7. Implementar reconciliación con `FECompConsultar`.
8. Incorporar `CondicionIVAReceptorId`.
9. Actualizar el QR fiscal.

### Prioridad media

1. Publicar tareas de clientes.
2. Publicar informes de vendedores.
3. Publicar la ficha integral del producto.
4. Preparar e inicializar el sistema de depósitos.
5. Limpiar los recursos compilados del catálogo externo.
6. Completar la consulta oficial de datos por CUIT.

### Antes de emitir con ARCA

1. Resolver las preguntas abiertas de negocio.
2. Configurar una empresa de homologación.
3. Obtener certificado y clave de homologación.
4. Configurar punto de venta electrónico.
5. Validar tipos de comprobante y condiciones de IVA.
6. Probar emisión sin datos reales sensibles.
7. Probar timeout, rechazo, reintento y recuperación.
8. Verificar PDF, QR y CAE.
9. Aprobar un procedimiento de soporte y contingencia.
10. Recién después preparar el paso a producción.

## 22. Guía rápida para revisar lo ya publicado

### Página pública

Abrir:

`https://flexsrepuestos.shop/`

Revisar:

- Encabezado.
- Navegación.
- Sección de servicios.
- Imagen de fabricación propia.
- Sección de productos.
- Ausencia de emojis en los bloques rediseñados.

### Panel administrativo

Abrir:

`https://flexsrepuestos.shop/admin-panel/`

Revisar:

- Navegación principal.
- Botón del cotizador.
- Acceso al inicio público.
- Resumen comercial.
- Cola operativa.
- Actividad reciente.

### Productos

Abrir:

`https://flexsrepuestos.shop/admin-panel/productos/`

Revisar:

- Filtros.
- Buscador.
- Acciones masivas.
- Estado y categorías.
- Alertas de duplicados.
- Botones de edición y eliminación.

### Cotizador

Utilizar el botón “Cotizador” de la cabecera del panel y verificar que siga disponible al navegar por otras secciones.

## 23. Recomendación de continuidad

El próximo paso seguro es preparar un despliegue controlado dividido en tres entregas:

### Entrega 1: consolidación y seguridad

- Ordenar los cambios locales.
- Consolidar el frontend ya publicado.
- Limpiar los archivos compilados.
- Ejecutar pruebas y crear un respaldo.

### Entrega 2: funciones comerciales

- Tareas de clientes.
- Estadísticas de vendedores.
- Ficha integral de producto.
- Mejoras de auditoría.

### Entrega 3: stock y ARCA

- Migración e inicialización de depósitos.
- Homologación de ARCA.
- Consulta oficial de CUIT.
- Facturas A y B.
- Notas de crédito A y B.
- Reconciliación y recuperación ante errores.

## 24. Conclusión

La plataforma ya cuenta con una base comercial considerable y con mejoras visibles en producción. El panel, el dashboard, el cotizador y el listado de productos están más ordenados y utilizables. También existen controles fiscales y comerciales importantes.

El desarrollo local contiene la siguiente evolución de la plataforma: tareas, estadísticas de vendedores, ficha integral de productos y stock por depósitos. Estos módulos deben validarse y desplegarse por etapas.

La integración real con ARCA todavía no está habilitada. Su arquitectura, riesgos, modelo de datos, seguridad y plan de implementación ya están documentados, lo que permite continuar de forma controlada sin poner en riesgo los datos ni emitir comprobantes accidentalmente.
