# Operación de copias de seguridad

El panel muestra solicitudes persistidas, resultados reales y alertas de antigüedad. Cada copia comprende todas las empresas de esta instalación. Descargar requiere el permiso `manage_backups`; las respuestas no se guardan en caché.

## Ejecución

El modo predeterminado es `BACKUP_EXECUTION_MODE=worker`. El botón registra un trabajo en `BACKUP_ROOT`; `manage.py backup_worker` procesa la cola. Las unidades de `scripts/systemd/webflexs-backup.*` lo ejecutan cada minuto como `www-data`. No encienden las tareas fiscales ni de webhooks de Celery. El ejecutor comprueba una copia diaria a las 02:30 en `TIME_ZONE`, configurable con `BACKUP_SCHEDULE_HOUR` y `BACKUP_SCHEDULE_MINUTE`.

Para un entorno que ya use Celery se puede configurar `BACKUP_EXECUTION_MODE=celery` y sus workers/beat. No deben activarse ambos planificadores.

La carpeta de backups debe pertenecer a `www-data:www-data` con modo `2770`; el usuario web debe ser el mismo usuario o pertenecer al grupo. Los archivos nuevos usan modo `0660`. Si se cambia `BACKUP_ROOT`, actualizar también `ReadWritePaths` en las unidades. Nunca ubicar el directorio bajo `MEDIA_ROOT`, archivos estáticos o una carpeta servida por Nginx.

Comandos de operación (desde la carpeta de la aplicación con el entorno de producción cargado):

```sh
venv/bin/python manage.py backup_worker --no-schedule
venv/bin/python manage.py backup_system
venv/bin/python manage.py verify_backup NOMBRE_manifest.json
```

## Contenido y comprobaciones

PostgreSQL se guarda con `pg_dump --format=custom --no-owner --no-acl`; incluye esquema y datos en una instantánea coherente. Las contraseñas viajan por el entorno del proceso, no por argumentos. SQLite/desarrollo conserva la exportación portable de Django. Las imágenes se archivan aparte; los enlaces simbólicos no se siguen.

Antes de publicar un manifiesto completo se comprueban la lectura del archivo y SHA-256. Se usan nombres únicos, archivos parciales y un bloqueo del sistema operativo. Una interrupción queda marcada como fallida en la siguiente ejecución. Verificar hoy un archivo antiguo no reinicia su antigüedad.

La integridad no equivale a una recuperación probada. `backup_restore_test --latest`, ejecutado como root, verifica una copia nativa de PostgreSQL, la restaura en una base temporal `flexs_restore_check_<uuid>`, comprueba que pueda consultarse y elimina solamente esa base temporal. No acepta nombres de base proporcionados por el usuario y nunca restaura sobre la base configurada de la aplicación. Registra el resultado en el manifiesto. Las unidades `webflexs-backup-restore.*` permiten ejecutarlo semanalmente los domingos a las 03:30 de Argentina.

## Conservación

`BACKUP_RETENTION_DAYS=30`, `BACKUP_MIN_COPIES=3`. Solo se limpian conjuntos nuevos, completos y verificados, conservando como mínimo los tres más recientes. Las copias anteriores (manifiestos versión 1), conjuntos dañados y archivos ajenos no se eliminan automáticamente. Si hay copia externa configurada, tampoco se limpia una copia cuyo envío no haya terminado. `BACKUP_RETENTION_ENABLED=False` permite suspender toda limpieza.

El panel alerta después de `BACKUP_MAX_AGE_HOURS=26` sin una copia reciente verificada y tras cinco minutos sin señal del ejecutor. Los errores aparecen en el historial; el mensaje de solicitud no afirma que el backup haya terminado.

## Almacenamiento externo cifrado (pendiente de destino)

No se envía ningún dato mientras `BACKUP_OFFSITE_REPOSITORY` esté vacío. La conexión usa Restic, que cifra los archivos antes de enviarlos y admite SFTP o almacenamiento de objetos. Cuando se elija un destino autorizado:

1. Instalar Restic y configurar el acceso para el usuario del ejecutor, con comprobación de identidad del servidor remoto.
2. Crear una contraseña de cifrado en un archivo privado; conservar una copia de recuperación fuera del VPS. La contraseña no se carga desde el panel.
3. Configurar `BACKUP_OFFSITE_REPOSITORY` y `BACKUP_OFFSITE_PASSWORD_FILE` en el entorno del servicio. Para S3, proveer también las credenciales del proveedor por entorno, sin incorporarlas al repositorio.
4. Inicializar explícitamente el repositorio Restic desde el contexto del ejecutor, verificar conectividad y ejecutar una copia completa.
5. Comprobar la recuperación desde ese destino. No habilitar políticas de borrado remoto hasta definir su conservación.

El envío se considera confirmado solo si Restic devuelve un identificador de instantánea. Un fallo externo conserva la copia local válida y muestra una advertencia. La contraseña y la dirección del repositorio nunca se muestran en el panel. Los manifiestos no contienen credenciales. Las unidades endurecidas pueden requerir un `ReadOnlyPaths` adicional para una clave SSH o contraseña ubicadas fuera de los directorios permitidos; preferir `/etc/webflexs/` con permisos mínimos.

## Limitaciones

Los respaldos no incluyen código del proyecto, configuración del servidor, secretos de `.env` ni claves fiscales externas a `MEDIA_ROOT`; deben conservarse por separado en un medio seguro. El respaldo de imágenes se realiza después de la instantánea de la base; puede haber archivos subidos durante ese intervalo. La prueba semanal valida la base restaurada y sus restricciones; una prueba operativa completa del portal continúa siendo una tarea separada. El repositorio Restic aún no está configurado hasta que se acuerde el destino.
