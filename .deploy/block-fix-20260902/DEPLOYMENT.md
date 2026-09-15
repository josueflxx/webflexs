# Correccion de bloques de productos — publicada el 02/09/2026

- Host oficial: `https://flexsrepuestos.shop/admin-panel/categorias/788/productos/`.
- VPS: `72.61.218.244`; aplicacion: `/var/www/webflexs`.
- Alcance publicado: tres archivos de aplicacion, preparados sobre las versiones exactas del host.
- No se publicaron los demas cambios pendientes del arbol local.
- No hubo migraciones, cambios de esquema, `collectstatic` ni operaciones de escritura sobre el catalogo.
- Respaldo de originales y huellas de datos: `/var/backups/webflexs/block-fix-20260902/`.
- Paquete remoto: `/var/tmp/webflexs-block-fix-20260902/`.

## Funcionalidad publicada

- Seleccion visible de productos para crear, asignar o quitar bloques.
- Guardado de asignaciones desde la misma tarjeta de bloques.
- Distincion entre guardar asignaciones y guardar el orden de bloques.
- Conversion selectiva de bloques a subcategorias respetando nombres personalizados.
- Reversion correcta de filas reutilizadas, sin sobrescribir cambios posteriores.

## Verificacion

- 17 pruebas locales de `CategoryManageProductsTests`: aprobadas.
- Sintaxis Python y JavaScript: validada.
- Hashes del paquete y de los originales: validados antes y despues de publicar.
- `manage.py check` en produccion: sin errores; solo aviso informativo de ARCA deshabilitado.
- Render autenticado y de solo lectura de la categoria 788: HTTP 200 antes y despues de reiniciar Gunicorn.
- Smoke publico: `/`, `/catalogo/` y `/accounts/login/` HTTP 200; `/admin-panel/` HTTP 302 esperado.
- Categoria 788: huella de datos identica antes y despues del despliegue.
- Gunicorn y Nginx activos; 0 errores recientes de Gunicorn.
