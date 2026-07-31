# Integracion del editor masivo con WEBFLEXS

## Fuente oficial

WEBFLEXS y su base PostgreSQL son la unica fuente oficial de productos. El editor
externo no debe ejecutar migraciones ni escribir directamente sobre las tablas de
Django. Toda lectura y escritura oficial pasa por la API privada de WEBFLEXS.

La activacion se controla con dos variables independientes:

- `FEATURE_EXTERNAL_EDITOR_ENABLED`: expone las rutas de lectura del editor.
- `FEATURE_EXTERNAL_EDITOR_WRITES`: permite mutaciones. Debe permanecer apagada
  hasta completar las pruebas en staging.

## Identidad y concurrencia

- El identificador tecnico es `Product.id`.
- El identificador comercial estable es `Product.sku`, que sigue siendo unico.
- Toda escritura incluye `expected_updated_at`. Si el producto cambio desde que
  se cargo en el editor, la API responde `409 Conflict` y no sobrescribe datos.
- Las operaciones aceptan una clave de idempotencia para evitar duplicados por
  reintentos de red.

## Mapeo de campos

| Editor | WEBFLEXS | Regla |
| --- | --- | --- |
| `id` | `Product.id` | Solo lectura |
| `internalCode` | `Product.sku` | Obligatorio y unico |
| `name` | `Product.name` | Obligatorio |
| `description` | `Product.description` | Texto libre |
| `cost` | `Product.cost` | Requiere `change_prices` |
| `salePrice` | `Product.price` | Requiere `change_prices` |
| `stock` | `Product.stock` | Entero mayor o igual que cero |
| `status` | `Product.is_active` | `active` / `inactive` |
| `categoryId` | `Product.category_id` | Categoria primaria |
| `categoryIds` | `Product.categories` | Incluye la primaria |
| `subcategoryId` | `Category.id` | Categoria hija; no existe tabla separada |
| `supplierId` | `Product.supplier_ref_id` | Mantiene `ProductSupplier` preferido |
| `supplier` | `Product.supplier` | Compatibilidad; se normaliza |
| `reference` | `Product.filter_1` | Referencia comercial heredada |
| `margin` | Derivado | `(precio - costo) / costo * 100` |
| `vatRate` | No persistido | Requiere decision fiscal posterior |
| `notes` | `Product.attributes.editor_notes` | Observacion interna |
| `tags` | `Product.attributes.editor_tags` | Hasta 20 etiquetas |
| `imageUrl` | `Product.image` | Imagen principal |
| `isDeleted` | `Product.attributes.editor_deleted_at` | Papelera recuperable |

## Endpoints del editor

Base: `/api/v1/editor/`

- `GET products/`
- `GET products/selection-ids/`
- `GET products/<id>/`
- `PATCH products/<id>/`
- `GET categories/`
- `GET suppliers/`
- `POST bulk/preview/`
- `POST bulk/`
- `GET jobs/<id>/`
- `POST jobs/<id>/rollback/`
- `GET jobs/`
- `POST jobs/<id>/redo/`
- `POST products/create/`
- `POST products/<id>/clone/`
- `POST|DELETE products/<id>/image/`
- `POST products/<id>/trash/`
- `GET workspace/`
- `GET validation/`
- `GET duplicates/`
- `GET|POST saved-views/`
- `GET|POST drafts/`
- `POST drafts/<id>/publish/`
- `POST import/preview/`
- `GET|POST supplier-lists/`
- `GET supplier-lists/<id>/`
- `POST supplier-lists/<id>/inspect/`
- `POST supplier-lists/<id>/preview/`
- `POST supplier-lists/<id>/decisions/`
- `POST supplier-lists/<id>/apply/`
- `POST supplier-lists/<id>/rollback/`
- `GET supplier-lists/<id>/report/`

## Actualizacion por proveedor

El asistente de listas de proveedor utiliza `SupplierPriceListBatch` como unidad
auditable. Conserva el archivo original, permite mapear columnas por proveedor,
genera una comparacion sin escribir y bloquea la aplicacion mientras existan
filas pendientes de revision.

Al aplicar se puede elegir entre actualizar solamente el costo o conservar el
margen vigente y recalcular el precio de venta. Cada costo modificado genera
`SupplierCostHistory`. La reversion comprueba primero que el costo, el precio y
las condiciones comerciales no hayan cambiado posteriormente.

La conciliacion inicial de referencias se realiza con:

```bash
python manage.py backfill_supplier_codes_from_base /ruta/BASE.xlsx --report reporte.json
python manage.py backfill_supplier_codes_from_base /ruta/BASE.xlsx --apply --report aplicado.json
```

El comando no aplica referencias compartidas, productos con referencias
contradictorias ni reemplaza codigos existentes salvo que se solicite
explicitamente con `--replace-existing`.

## Productividad y seguridad operativa

- Las ediciones individuales disponen de deshacer y rehacer durante la sesion.
- Los cambios pueden guardarse como borrador persistente antes de publicarlos.
- La publicacion de un borrador genera un trabajo auditable y reversible.
- Los lotes superiores a `EXTERNAL_EDITOR_SYNC_LIMIT` se despachan en segundo
  plano; se usa Celery cuando esta habilitado y un hilo local como respaldo.
- La papelera es logica: no elimina filas de producto y admite restauracion.
- Las listas CSV y XLSX de proveedores se conservan como lotes auditables y se
  previsualizan antes de aplicar.
- El centro de control agrupa validaciones, duplicados, etiquetas, vistas,
  borradores, progreso, historial y comparacion antes/despues.

## Seguridad de despliegue

1. Crear y verificar un respaldo antes de habilitar escritura.
2. Restaurar el respaldo en staging y ejecutar todas las pruebas alli.
3. Desplegar primero con `FEATURE_EXTERNAL_EDITOR_WRITES=False`.
4. Habilitar escritura para un operador y un lote pequeno.
5. Revisar auditoria, catalogo publico y exportacion Excel.
6. Habilitar el editor para el resto de administradores.

La aplicacion .NET puede seguir procesando archivos y calculando propuestas, pero
debe publicar los cambios mediante esta API y no mediante una conexion directa a
PostgreSQL.

## Despliegue implementado

El frontend oficial se compila con `VITE_WEBFLEXS_MODE=true`. En ese modo:

- usa `/api/v1/editor` en el mismo dominio;
- autentica usuarios administrativos de Django;
- muestra solamente el editor oficial de productos;
- envia una vista previa antes de cada lote;
- agrega una clave de idempotencia y consulta el progreso del trabajo;
- no usa el servicio .NET ni sus credenciales PostgreSQL.

El despliegue normal conserva temporalmente el servicio y el proxy heredados
como vía de retorno durante el canary. El editor oficial no los consume. Solo
después del período de estabilización se retiran de forma explícita con
`RETIRE_LEGACY_CATALOGOPRO=1`.

Secuencia en el host:

```bash
cd /var/www/webflexs
source venv/bin/activate
python manage.py migrate --noinput
python manage.py check
sudo bash scripts/deploy_catalogopro_vps.sh
```

La retirada definitiva del backend heredado es un paso separado y posterior:

```bash
sudo RETIRE_LEGACY_CATALOGOPRO=1 bash scripts/deploy_catalogopro_vps.sh
```

Primero se configura:

```env
FEATURE_EXTERNAL_EDITOR_ENABLED=True
FEATURE_EXTERNAL_EDITOR_WRITES=False
```

Tras validar login, filtros y vistas previas en el host, se cambia unicamente
`FEATURE_EXTERNAL_EDITOR_WRITES=True` y se reinicia Gunicorn. Los trabajos y sus
reversiones se revisan desde el administrador Django en `External editor jobs`.

Para una prueba local aislada puede utilizarse
`flexs_project.settings.external_editor_test` con `EXTERNAL_EDITOR_TEST_DB`
apuntando a un archivo SQLite temporal.
