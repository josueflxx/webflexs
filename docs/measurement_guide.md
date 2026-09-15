# Manual de medición 2D

Ruta pública: `/catalogo/como-medir/`. La plantilla `catalog/templates/catalog/how_to_measure.html` contiene un SVG curvo completo para funcionar sin JavaScript. Los estilos y la interacción están en `core/static/core/css/measurement_guide.css` y `core/static/core/js/measurement_guide.js`. No requiere bibliotecas adicionales.

Los tres perfiles corresponden a los modelos 3D aprobados: curva semicircular, semicurva elíptica de arco bajo y plana con tramo horizontal y esquinas redondeadas. A mide el diámetro; B une las caras internas; D cubre todo el tramo roscado.

Según la referencia aprobada por el usuario, C en curva y semicurva une el extremo interior de la pata izquierda (185,450) y el centro interior del arco (260,93). En plana conserva la cota vertical exterior en x=100, desde el nivel interior del techo hasta los extremos. Las coordenadas son ilustrativas y no modifican las medidas o cálculos del pedido.

Los controles A–D resaltan la cota y su explicación. La selección de perfil mantiene el resaltado y actualiza la instrucción C, la descripción accesible y el arco. En celular las etiquetas muestran letras grandes; las tarjetas desplazan el esquema a la vista. El movimiento respeta la preferencia de movimiento reducido.

Validación: `node output/measurement-guide-review/check.mjs`, con pruebas de perfiles, extremos de C, líneas auxiliares, selección, teclado, desbordes a 768/390/320 px, tema claro y esquema sin JavaScript. Para producción definir `GUIDE_ORIGIN=https://flexsrepuestos.shop`.

Publicación: paquete `.deploy/measurement-guide-20260914`, una plantilla y dos estáticos; respaldo en `/var/backups/webflexs/measurement-guide-20260914`. No requiere migraciones.

Corrección de rosca publicada el 14/09/2026: líneas completas reutilizadas con `<use>` y origen local para cada pata. El patrón anterior repetía una junta dentro del ancho de la varilla. Respaldo: `/var/backups/webflexs/measurement-thread-20260914`; paquete: `.deploy/measurement-thread-20260914`. Verificación visual pública en los tres perfiles y móvil, sin errores de JavaScript.
