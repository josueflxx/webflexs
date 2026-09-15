# Rubros y subrubros en lote

## Acceso y uso

Panel admin → Marcas → **Rubros y subrubros en lote**.
Ruta: `/admin-panel/marcas/estructura/`.

1. Elegir marcas concretas o todas las existentes. Las inactivas requieren una opción explícita.
2. Elegir crear rubros, crear subrubros, editar rubros o editar subrubros.
3. Al crear, escribir hasta 30 nombres, uno por línea. Al editar, identificar el nombre actual y marcar sólo los campos a cambiar: nombre, orden, estado o imagen.
4. Para crear subrubros, elegir si se omiten las marcas sin el rubro padre o si se crea ese padre también.
5. Generar la vista previa; el motivo del cambio es opcional. Se muestran creaciones, ediciones, omisiones y conflictos por marca.
6. Revisar el alcance compartido y confirmar. El historial permite consultar y deshacer el lote si no se perderían cambios posteriores.

Las búsquedas de coincidencias normalizan mayúsculas, acentos y separadores mediante `BrandAlias.normalize`. No fusionan conceptos distintos ni resuelven automáticamente coincidencias ambiguas. Crear nunca sobrescribe lo existente. Editar nunca crea una estructura faltante. Los nombres y slugs existentes se mantienen salvo el nombre explícitamente seleccionado para edición; renombrar no cambia las URLs.

## Seguridad y límites

- Es un catálogo global, compartido entre empresas, igual que el panel de Marcas existente. No se presenta como una operación limitada a la empresa activa.
- No se asignan, copian ni mueven productos, ni se cambian categorías canónicas, reglas, categorías ayudantes o posiciones de productos.
- Se respetan los permisos existentes: sólo el superadministrador principal puede preparar, confirmar o deshacer un lote. El resto del personal tiene consulta; las vistas previas son privadas de su autor.
- La vista previa congela las marcas objetivo, vence a los 30 minutos y se rechaza si la estructura cambió antes de confirmar.
- La aplicación es atómica y una confirmación repetida no duplica el lote. Se bloquean las filas de estructura durante la aplicación/reversión. Las restricciones existentes de nombres/slugs siguen vigentes.
- Máximo: 250 marcas, 30 nombres nuevos y 1.000 filas de vista previa por lote. No requiere una cola de trabajos para el volumen actual.
- La imagen opcional se guarda una sola vez por lote y se comparte por referencia. PNG/JPG/WebP, hasta 5 MB. Se conserva para historial y reversión; los borradores abandonados no tienen eliminación automática de archivos.
- Deshacer restaura valores o elimina las estructuras creadas que sigan sin uso. Se rechaza todo el lote si hay cambios de estructura incompatibles, nuevos hijos, productos, reglas, mapeos o lotes de catalogación que lo impedirían. No existe borrado masivo arbitrario de rubros.
- Los nodos nuevos sin productos se ven en el admin. El catálogo público mantiene su comportamiento de ocultar nodos sin productos activos.

## Publicación

Publicado en el host oficial el 31/08/2026, con respaldo, migración y comprobaciones de sólo lectura. No se modificaron datos existentes del catálogo ni los estilos generales. Las pruebas locales no publican cambios por sí solas.

Actualización del 31/08/2026: el motivo del cambio es opcional también en el host oficial. Se publicó únicamente el formulario, sin migraciones ni cambios de diseño; se verificaron la validación y el HTML en producción con acceso de sólo lectura.

1. Revisar el diff de esta funcionalidad y respaldar base de datos y archivos del servidor. El árbol local contiene otros cambios; no desplegarlo completo sin revisión.
2. Publicar el modelo `BrandStructureBatch`, migración `catalog/0033_brandstructurebatch.py`, servicio y formulario nuevos, vistas `brand_structure.py`, su importación y las cuatro rutas; las dos plantillas nuevas, el botón adicional de `brand_list.html` y CSS/JS `brand_structure`.
3. Ejecutar la migración `catalog.0033` en el entorno oficial. Depende de `0032_categorybrandmapping`.
4. Recopilar estáticos y reiniciar la aplicación según el procedimiento normal del host. Verificar HTTP 200 para ambos estáticos y permisos de lectura del servidor web.
5. No reemplazar `admin_panel/base.html` ni activar hojas de estilo globales por este cambio. El nuevo CSS sólo afecta `.brand-structure-page`.
6. Como administrador, comprobar entrada a Marcas, gestor, vista previa y un lote de prueba autorizado. Confirmar que la presentación anterior de Marcas y los productos no cambian.

## Verificación local

```powershell
python manage.py makemigrations --check --dry-run --settings=flexs_project.settings.test
python manage.py test catalog.tests_brand_structure catalog.tests_brands catalog.tests_category_brand_integration --settings=flexs_project.settings.test --noinput
```

`scripts/preview_brand_structure.py` levanta un entorno descartable en `127.0.0.1:8767`, con marcas de muestra y una base SQLite temporal. No usa datos ni credenciales del host; no debe desplegarse a producción. Se detiene con Ctrl+C.

Prueba visual: selección/filtro de marcas, opción todas/inactivas, los cuatro modos, campos desmarcados deshabilitados, vista previa con un padre faltante, confirmación, historial y ancho móvil. Las escrituras de pruebas deben permanecer en ese entorno descartable.
