# Vista 3D de abrazadera

La página `/catalogo/abrazaderas-a-medida/` ofrece un plano con medidas y una
vista 3D de referencia. El modelo representa una abrazadera curva de acero;
no cambia cuando se modifican las medidas, el perfil o la terminación del formulario.

El botón **Ver en 3D** carga el módulo y el GLB desde los archivos estáticos del
propio sitio. No hay descargas 3D iniciales, CDN en tiempo de ejecución, texturas,
decodificadores adicionales ni rotación automática. El visor se reutiliza al
alternar vistas. Se puede girar, acercar y restablecer la cámara. Ante un error
aparece un botón para reintentar y el plano sigue disponible.

## Archivos

- `core/static/core/js/clamp_viewer.js`: carga bajo demanda y controles.
- `core/static/core/css/clamp_viewer.css`: estilos de escritorio y móvil.
- `core/static/core/models/abrazadera-curva.glb`: activo original generado para
  FLEXS, de 196.220 bytes, sin texturas, con 10.768 triángulos.
- `scripts/build_clamp_model.py`: genera el GLB con Python estándar. Ejecutar
  `python scripts/build_clamp_model.py` para reproducirlo.
- `core/static/core/vendor/model-viewer/`: model-viewer 4.3.1 y su licencia.

La referencia tiene varilla de 12,7 mm, ancho interior de 120 mm y largo útil de
180 mm. La rosca es ilustrativa. Para reemplazarla por un modelo de producto real,
exportar un GLB en metros y actualizar `data-model` en la plantilla. Usar una
nueva ruta de archivo cuando cambie el modelo para evitar cachés antiguos.

## Dependencia

- Distribución original: https://ajax.googleapis.com/ajax/libs/model-viewer/4.3.1/model-viewer.min.js
- Código y licencia: https://github.com/google/model-viewer/tree/v4.3.1
- Documentación: https://modelviewer.dev/

Se conserva la licencia Apache 2.0 y los avisos de dependencias incluidos en la
distribución. Publicar los archivos con el proceso habitual de `collectstatic`.

## Portada

La portada también muestra la abrazadera en la columna derecha del encabezado;
en pantallas de hasta 900 px se ubica debajo del texto. Está presente en todos
los temas visuales de la portada. Usa `home_clamp.js` y `home_clamp.css`.

El visor se carga cuando entra en pantalla, después de dar prioridad al contenido
inicial. Si está activado el ahorro de datos, espera al botón **Explorar en 3D**.
La imagen estática queda disponible sin JavaScript o si falla la carga. Incluye
controles para acercar, alejar y restablecer la cámara, además de gestos.

El material de la portada utiliza metalicidad 1, rugosidad 0,24, tone mapping ACES
y sombras suaves. `clamp-studio.hdr` es un entorno RGBE original de 512 × 256 px
(102.869 bytes) con fuentes de luz de estudio blancas, frías y cálidas. Sus
reflejos se calculan sobre el material físico al girar el modelo. Se regenera con
`python scripts/build_clamp_studio.py`. La portada usa `abrazadera-plaqueta-tuercas.glb` (367.432 bytes); el formulario
usa el modelo correspondiente al perfil seleccionado. Los ajustes de material de estudio se aplican
únicamente al visor de la portada.


## Indicaciones de medidas

`clamp_dimensions.js` y `clamp_dimensions.css` agregan el botón **Ver medidas**
a los dos visores. Se muestran A (diámetro de varilla), B (ancho interior),
C (largo útil desde la cara interior del arco) y D (rosca). Las líneas se proyectan
desde anclajes 3D mediante `queryHotspot` y se actualizan en eventos de cámara y
redimensionado, sin un bucle de animación adicional. En móvil se muestran letras
compactas y la explicación debajo. El control permite ocultarlas.

Son indicaciones de dónde medir, sin valores numéricos: los GLB son ilustrativos
y no responden a las medidas del formulario. No deben confundirse con cotas de
un pedido ni con un plano de fabricación. Los anclajes del modelo ensamblado
usan los mismos parámetros geométricos que `scripts/build_clamp_assembly.py`.

La versión con plaqueta y tuercas y ambos visores con indicaciones se despliegan
mediante el paquete `.deploy/clamp-dimensions-20260914`. El respaldo en el VPS es
`/var/backups/webflexs/clamp-dimensions-20260914`. Incluye solo las dos plantillas
y los estáticos necesarios, sin cambios de base de datos.

## Perfiles 3D y cota C

El inicio permite elegir plana, curva y semicurva. El formulario usa el perfil seleccionado para cargar el modelo 3D correspondiente. Cada GLB se carga bajo demanda y se reutiliza al volver al perfil. Los tres conservan plaqueta, tuercas y rosca.

`python scripts/build_clamp_assembly.py` genera los tres archivos: `abrazadera-plaqueta-tuercas.glb` (plana aprobada, sin cambios de geometría), `abrazadera-curva-plaqueta-tuercas.glb` (corona semicircular) y `abrazadera-semicurva-plaqueta-tuercas.glb` (corona elíptica más baja).

La plana conserva C exterior. Para curva y semicurva, C une el extremo interior de la pata izquierda (-0.03865, 0) con el centro interior del arco (0, 0.25365), según la referencia del usuario. Las anotaciones siguen la cámara y conservan su visibilidad al cambiar el perfil. Son guías ilustrativas: no modifican las medidas numéricas del formulario ni el cálculo del pedido.

Los tres perfiles se publicaron el 14/09/2026 mediante `.deploy/clamp-profiles-20260914`. Respaldo en `/var/backups/webflexs/clamp-profiles-20260914-r2`. La publicación preserva los cambios ajenos al visor en las plantillas del servidor. Incluye seis archivos actualizados y dos nuevos GLB; no requiere migraciones.
