# Informe de preparación offline previa a credenciales ARCA

Fecha: 2026-07-30  
Alcance: configuración, seguridad, caché, comandos, pruebas y documentación
locales. No se usaron credenciales ni se estableció ninguna conexión con ARCA.

## A. Veredicto

### PREPARACIÓN OFFLINE COMPLETADA — EN ESPERA DEL USUARIO

La preparación específica de ARCA quedó implementada, sus 90 pruebas dirigidas
pasan y las suites completas PostgreSQL y SQLite quedaron aprobadas.

El bloqueo ambiental de PDF se resolvió instalando desde los repositorios
oficiales y firmados de MSYS2:

- Pango 1.57.1 y sus dependencias en `C:\msys64\ucrt64\bin`.
- Poppler/pdftoppm 26.04.0 en la misma ubicación.
- Variables de usuario `WEASYPRINT_DLL_DIRECTORIES` y `PDFTOPPM_BINARY`.

No se debilitó, omitió ni convirtió en mock ninguna prueba.

## B. Cambios realizados

| Archivo | Cambio | Motivo | Autoría aislable | Riesgo |
| ------- | ------ | ------ | ---------------: | ------ |
| `.env.example` | Defaults ARCA seguros, confirmación WSASS y caché sin valores reales | Esquema local fail-closed | Sí, bloque ARCA | Bajo |
| `requirements.txt` | Dependencia opcional de cliente Memcached | Soporte reproducible Redis/Memcached | Sí, una línea | Bajo |
| `flexs_project/settings/base.py` | Booleanos estrictos, ambiente/endpoints exactos y selección de caché compartida | Rechazar valores ambiguos y cachés inseguras | Sí, bloques ARCA/caché | Medio |
| `flexs_project/settings/test.py` | Neutralización explícita de nuevas variables ARCA | Suite determinista y sin red | Sí, bloque de entorno | Bajo |
| `core/checks.py` | Activación WSASS incluida en checks fail-closed | Detectar habilitación incompleta | Sí, un hunk | Bajo |
| `core/models.py` | Tupla ordenada en constraint fiscal | Estabilizar autodetección de migraciones sin cambiar semántica | Sí, un hunk | Bajo |
| `core/migrations/0033_arca_fiscal_integrity.py` | Estado serializado del mismo constraint como tupla ordenada | Evitar migración espuria 0034 | Sí, una operación | Bajo |
| `core/services/arca_config.py` | `homologacion` exacto, sin normalizar espacios o mayúsculas | Allowlist de ambiente cerrada | Sí, funciones ARCA | Medio |
| `core/services/arca_credentials.py` | Rechazo de vacío, symlink, junction y componentes enlazados | Evitar rutas canónicas inseguras | Sí, validación de rutas | Alto |
| `core/services/arca_homologation.py` | Razones granulares, confirmación WSASS, caché/prefijo y flags estrictos | Gate exhaustivo y sanitizado | Sí, evaluación del gate | Alto |
| `core/services/arca_ticket_cache.py` | Inspección Redis/Memcached, prefijo, singleflight y borrado exacto | Token/Sign sólo en caché compartida y efímera | Sí, módulo ARCA | Alto |
| `core/services/arca_client.py` | Orden WSAA→FEDummy→catálogos→último autorizado y cortes parciales | Secuencia futura exacta, sin continuar tras fallos | Sí, `run_preflight` | Alto |
| `core/services/arca_doctor.py` | Nuevo diagnóstico `PASS`/`FAIL`/`WAITING_FOR_USER` | Estado offline sin mostrar secretos | Archivo nuevo | Medio |
| `core/management/commands/arca_homologation_doctor.py` | Nuevo comando sanitizado y sin red | Diagnóstico operativo offline | Archivo nuevo | Bajo |
| `core/management/commands/arca_homologation_readonly_probe.py` | CLI sin abreviaturas, limpieza de caché y evidencia sanitizada | Impedir argumentos aproximados y persistencia de Ticket | Sí, comando ARCA | Alto |
| `core/test_arca_homologation_gate.py` | Matriz negativa de ambiente, endpoints, flags y cachés | Verificar fallo cerrado | Sí, tests ARCA | Bajo |
| `core/test_arca_security.py` | Casos de credenciales, caché, concurrencia y secuencia | Cobertura offline de seguridad | Sí, tests ARCA | Bajo |
| `core/test_arca_offline_commands.py` | Tests con DNS/socket/HTTP/SOAP bloqueados | Probar aislamiento de doctor, gate y probe mockeado | Archivo nuevo | Bajo |
| `docs/arca/ARCA_HOMOLOGACION_ACCIONES_USUARIO.md` | Responsabilidades, prohibiciones y caché local | Separar acciones humanas/Codex | Sí, secciones ARCA | Bajo |
| `docs/arca/QUESTIONS_FOR_USER.md` | Checklist WSASS, caché y secretos que no deben compartirse | Cierre humano verificable | Sí, checklist | Bajo |
| `docs/arca/arca-homologacion.env.example` | Placeholders Redis/Memcached y confirmación WSASS | Ejemplo sin datos reales | Sí, esquema ARCA | Bajo |
| `docs/arca/ARCA_HOMOLOGACION_READONLY_RUNBOOK.md` | Runbook futuro de 18 pasos con stop/evidencia/reversión | Preparar lectura posterior sin ejecutarla | Archivo nuevo | Bajo |
| Este informe | Evidencia A–H de la tarea | Trazabilidad | Archivo nuevo | Bajo |

Los archivos preexistentes modificados conservan autoría mixta. Los cambios de
esta tarea pueden aislarse posteriormente por los hunks indicados. No se
modificaron arbitrariamente los tres archivos `MM`.

## C. Tests

### Dirigidos

- Gate, configuración, credenciales, criptografía mock, cliente WSAA/WSFE
  mock, caché, redacción, emisión y comandos: **90 ejecutados, 90 aprobados,
  0 fallidos, 0 errores, 0 skips**, 0,625 s.
- Caché y management commands como subconjunto cuantificado: **21 ejecutados,
  21 aprobados**, 0,189 s.

### PostgreSQL

- Base de test recreada sin `--keepdb`.
- **622 ejecutados, 622 aprobados, 0 fallidos, 0 errores, 0 skips**,
  216,644 s.

### SQLite

- Base en memoria nueva.
- **622 ejecutados, 614 aprobados, 0 fallidos, 0 errores, 8 skips**,
  172,254 s.
- Los ocho skips son los casos 1–8 de
  `PostgreSQLFiscalConcurrencyTests`: requieren locks de fila, constraints
  parciales y conexiones backend independientes que SQLite no puede validar.
  Ejecutados aisladamente: 8/8 skips con esa razón explícita.
- Pruebas binarias reales de PDF/QR: **4 ejecutadas, 4 aprobadas**, 25,809 s.

### Migraciones

- `makemigrations --check --dry-run`: **PASS — No changes detected**.
- Base PostgreSQL limpia temporal: todas las migraciones hasta `core.0033`:
  **PASS**.
- `migrate --check` sobre esa base: **PASS**.
- La base temporal `webflexs_arca_audit_019fad84` fue eliminada al terminar.

## D. Compuerta y doctor

Estado actual esperado y obtenido:

```text
ARCA_HOMOLOGATION_DOCTOR=WAITING_FOR_USER
ARCA_HOMOLOGATION_READINESS_GATE=FAIL
```

Razones sanitizadas: integración/red/lectura/señal/caché deshabilitadas y
faltantes de autorización WSASS, endpoints, servicio, CUIT, punto de venta,
tipo de comprobante y rutas de credenciales. Son faltantes humanos previstos,
no regresiones del gate.

El doctor informó producción y emisión deshabilitadas, TLS y redacción activas,
sin rutas completas, CUIT, fingerprint, Token ni Sign. Los tests bloquearon
explícitamente DNS, sockets, HTTP y transporte SOAP. No se ejecutó
`arca_homologation_readonly_probe`.

## E. Seguridad

- Producción: bloqueada por ambiente, flag y endpoint.
- Emisión: `FECAESolicitar`, CAEA y métodos no allowlisted permanecen
  bloqueados antes de login/payload/red.
- TLS: obligatorio; no hay flag aceptado para desactivarlo.
- Endpoints: coincidencia exacta; se rechazan HTTP, usuario/contraseña,
  subdominios engañosos, IP, puerto, path, query, fragmento y redirección.
- Credenciales: fuera de repositorio/publicables; archivo regular no vacío,
  sin symlink/junction, permisos restringidos y validación criptográfica local.
- Git: 0 `.env` reales rastreados, 0 artefactos `.key/.pem/.p12/.pfx/.crt/.cer`
  en el estado y `.env` ignorado. Los ejemplos contienen sólo placeholders.
- Logs: tests verifican redacción de Token, Sign y excepciones. El probe sólo
  imprime booleanos, contadores y punto enmascarado.
- Caché: `LocMem`, base de datos, archivo, dummy, backend desconocido y
  ubicaciones inválidas se rechazan. Fallos de read/write/lock/delete son
  fail-closed.
- `catalogopro_build/api/appsettings.json` ya figuraba modificado al inicio y
  no fue leído ni alterado por esta tarea.

## F. Documentación

Revisados:

- `ARCA_HOMOLOGACION_ACCIONES_USUARIO.md`
- `QUESTIONS_FOR_USER.md`
- `arca-homologacion.env.example`

Creado:

- `ARCA_HOMOLOGACION_READONLY_RUNBOOK.md`

Pendiente del usuario: WSASS, clave/CSR/certificado de testing, autorización,
CUIT representado, identificador exacto de servicio, punto de venta, tipo de
comprobante, caché local y variables. La señal de solo lectura debe ser lo
último que habilite el usuario.

## G. Git

- Branch: `codex/production-fiscal-client-upgrade-20260723`.
- HEAD inicial y final:
  `a925eb434d7e5231d07c9c149d178f21ac267a0c`.
- Estado inicial: 171 rutas (74 tracked con estado, 97 untracked).
- Estado final previsto incluyendo este informe: 177 rutas (75 tracked con
  estado, 102 untracked).
- Archivos nuevos de esta tarea: 5 (doctor service, doctor command, tests de
  comandos, runbook e informe).
- Staging inicial/final: exactamente 3 archivos, 11 inserciones y 5 borrados.
- `MM` preservados con hashes iniciales/finales idénticos:
  - `admin_panel/templates/admin_panel/base.html`:
    HEAD `5a9901f`, index `3065ee4`, worktree `d258bd4`.
  - `core/static/core/css/base.css`:
    HEAD `a096761`, index `3458fb3`, worktree `d275487`.
  - `templates/base.html`:
    HEAD `bc87b22`, index `e6a2603`, worktree `adbabff`.
- No commit, push, merge, rebase, reset, restore, clean, deploy ni cambio de
  staging.
- No quedaron logs, bases temporales ni otros archivos generados por la tarea.
- No se perdió ni descartó ningún cambio preexistente.

## H. Próximo paso exacto

> No ejecutar conexiones. Esperar a que el usuario complete WSASS, certificado, autorización, CUIT, punto de venta, tipo de comprobante, caché y variables locales. Después ejecutar únicamente una orden separada para la compuerta local y la prueba de solo lectura.
