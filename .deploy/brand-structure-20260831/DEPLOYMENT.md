# Publicación completada — 31/08/2026

- Host oficial: `flexsrepuestos.shop` / VPS `72.61.218.244`.
- Gestor: `https://flexsrepuestos.shop/admin-panel/marcas/estructura/`.
- Paquete: 12 archivos; los cuatro archivos compartidos se prepararon sobre copias del host. El cambio de `brand_list.html` fue únicamente el enlace al gestor.
- Migración aplicada: `catalog.0033_brandstructurebatch`.
- Respaldo verificado de PostgreSQL y originales: `/var/backups/webflexs/brand-structure-20260831/`.
- Archivo PostgreSQL: `database.dump`, 13.572.105 bytes, índice validado con `pg_restore --list`.
- Paquete remoto: `/var/tmp/webflexs-brand-structure-20260831/`.

## Verificación

- `manage.py check`: sin errores; aviso informativo de ARCA deshabilitado.
- Render autenticado de Marcas y gestor: HTTP 200, dentro de una transacción de sólo lectura.
- Lógica de vista previa: validada sin guardar un lote.
- Tabla de lotes: disponible, 0 filas al terminar la publicación.
- Comparación de huellas antes/después: marcas, rubros, subrubros, asociaciones de productos, reglas, mapeos y productos sin cambios.
- `admin_panel/base.html` y CSS general `admin.css`: sin cambios.
- Gunicorn y Nginx: activos; sin errores recientes en el registro de Gunicorn.
- Dominio público: Marcas y gestor responden HTTP 302 al login para visitantes sin sesión, como corresponde.
- CSS y JS nuevos: HTTP 200 con tipo correcto, y hashes idénticos entre fuente y `staticfiles`.

No se crearon ni se editaron rubros/subrubros reales durante la publicación. No se desplegaron el resto de cambios pendientes del árbol local.
