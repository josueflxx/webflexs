# Informe de eliminacion de bloqueos previos a homologacion ARCA

Fecha: 2026-07-30  
Rama: `codex/production-fiscal-client-upgrade-20260723`  
HEAD inicial y final: `a925eb434d7e5231d07c9c149d178f21ac267a0c`

Este trabajo fue completamente local. No se utilizaron credenciales, certificados,
claves privadas ni servicios de ARCA.

## A. Resumen ejecutivo

Se resolvieron los bloqueos tecnicos de la auditoria:

- WeasyPrint 69.0 funciona con Python 3.14.2 y Pango 1.58.0.
- Se genero y reviso un PDF binario real.
- Los estados borrador, pendiente, rechazado e incierto fallan cerrados.
- El PDF autorizado usa evidencia fiscal persistida y conserva su snapshot.
- El watermark de homologacion es visible en el PDF renderizado.
- El QR se extrajo desde el PDF rasterizado, se decodifico y coincidio con el
  payload esperado.
- Los ocho casos multithread pasaron sobre PostgreSQL 18.1 con dos conexiones
  backend independientes y barrera por caso.
- La migracion limpia y el upgrade escalonado pasaron.
- La suite completa paso en PostgreSQL y la suite compatible paso en SQLite.
- Git quedo inventariado y clasificado sin descartar ningun cambio.

Bloqueos tecnicos restantes para esta orden: ninguno.

Riesgos operativos restantes: el working tree continua deliberadamente muy
sucio y contiene tres archivos `MM`; por eso no se creo ningun commit.

## B. WeasyPrint

| Dato | Resultado |
| --- | --- |
| Sistema | Windows 10, AMD64, build 10.0.19045 |
| Python | CPython 3.14.2 |
| Gestor | pip 25.3 |
| WeasyPrint | 69.0 |
| Pydyf | 0.12.1 |
| Pango | 1.58.0 |
| Declaracion de runtime | `requirements.txt`: `weasyprint>=61.0` ya existia |
| Herramientas de auditoria | `requirements-arca-validation.txt` |
| PDF smoke | 6.296 bytes |
| Cabecera | `%PDF-` |
| Paginas | 1 |
| Texto extraido | `Smoke ARCA / PDF local de desarrollo - WeasyPrint 69.0` |

Dependencias nativas instaladas fuera del repositorio:

- Runtime portatil MSYS2 UCRT64.
- `mingw-w64-ucrt-x86_64-pango` 1.58.0.
- Dependencias transitivas firmadas: GLib, Cairo, Fontconfig, FreeType,
  HarfBuzz, Fribidi y librerias UCRT64 relacionadas.

Instalacion reproducible:

```powershell
python -m pip install -r requirements-arca-validation.txt
pacman -Syu --noconfirm
pacman -S --needed --noconfirm mingw-w64-ucrt-x86_64-pango
$env:WEASYPRINT_DLL_DIRECTORIES="$env:MSYS2_ROOT\ucrt64\bin"
```

No se agrego al repositorio ninguna ruta absoluta, DLL, cache o binario.

Referencias:

- https://pypi.org/project/weasyprint/
- https://doc.courtbouillon.org/weasyprint/latest/first_steps.html
- https://packages.msys2.org/packages/mingw-w64-ucrt-x86_64-pango

Correccion minima descubierta durante la prueba:

- `core/services/pdf_generator.py` ahora resuelve assets Django de `static/` y
  `media/` localmente antes del fetch HTTP. Esto evita que el generador intente
  descargar su propio logo desde `testserver` y no introduce rutas de maquina.

## C. Validacion PDF real

Todos los archivos fueron generados mediante el endpoint real y
`generate_document_pdf`, no mediante una asercion exclusiva de HTML.

| Caso | PDF generado | Estado correcto | CAE correcto | QR correcto | Watermark | Snapshot | Resultado |
| ---- | -----------: | --------------: | -----------: | ----------: | --------: | -------: | --------- |
| A - Borrador | Si | Si: Borrador/no autorizado | No otorgado | No decodificable | No autorizado | Persistido | Aprobado |
| B - Listo para emitir | Si | Si: Listo para emitir/no autorizado | No otorgado | No decodificable | No autorizado | Persistido | Aprobado |
| C - Rechazado | Si | Si: Rechazado | No otorgado | No decodificable | `COMPROBANTE RECHAZADO` | Persistido | Aprobado |
| D - Resultado incierto | Si | Si: Resultado incierto | No otorgado | No decodificable | No autorizado | Persistido | Aprobado |
| E - Autorizado simulado | Si | Si: Autorizado | Persistido y visible | Decodificado | No corresponde a produccion simulada | Historico | Aprobado |
| F - Homologacion simulada | Si | Si: Autorizado | Persistido y visible | Decodificado | `HOMOLOGACION - SIN VALIDEZ FISCAL` visible | Historico | Aprobado |

Artefactos locales, todos ignorados por Git:

- `tmp/pdfs/fiscal-binary/case-a-draft.pdf`
- `tmp/pdfs/fiscal-binary/case-b-ready-to-issue.pdf`
- `tmp/pdfs/fiscal-binary/case-c-rejected.pdf`
- `tmp/pdfs/fiscal-binary/case-d-uncertain.pdf`
- `tmp/pdfs/fiscal-binary/case-e-authorized.pdf`
- `tmp/pdfs/fiscal-binary/case-f-homologation.pdf`

El render visual confirmo:

- Logo y encabezado visibles.
- Estado y CAE legibles.
- QR legible en los casos autorizados.
- Watermarks grandes, diagonales y visibles, sin JavaScript.
- Ausencia de QR en los estados no autorizados.

## D. Validacion QR

Metodo de extraccion:

1. Generacion del PDF real con WeasyPrint.
2. Raster de la primera pagina a 300 DPI con Poppler `pdftoppm`.
3. Lectura automatica de la imagen con OpenCV headless 5.0.0.93.
4. Decodificacion mediante `cv2.QRCodeDetector`.
5. Decodificacion Base64 del parametro `p`.
6. Comparacion exacta del objeto JSON.

Payload esperado, con datos exclusivamente simulados:

```json
{
  "ver": 1,
  "fecha": "2026-07-30",
  "cuit": 30693450239,
  "ptoVta": 8,
  "tipoCmp": 6,
  "nroCmp": 321,
  "importe": 121,
  "moneda": "PES",
  "ctz": 1,
  "tipoDocRec": 80,
  "nroDocRec": 20123456786,
  "tipoCodAut": "E",
  "codAut": 74123456789012
}
```

Payload obtenido: exactamente el mismo objeto.

Campos comparados: version, fecha, CUIT emisor simulado, punto de venta, tipo,
numero, importe, moneda, cotizacion, tipo y numero de documento receptor, tipo
de autorizacion y CAE simulado.

Resultado: aprobado. Borrador, rechazado, pendiente e incierto produjeron cadena
decodificada vacia. Homologacion produjo QR decodificable y watermark visible.

## E. Tests de concurrencia

Todos los casos utilizaron:

- `TransactionTestCase`.
- PostgreSQL 18.1 real.
- Dos threads.
- Dos PIDs de backend PostgreSQL distintos.
- `close_old_connections()` antes y despues de cada worker.
- `threading.Barrier` con timeout.
- Resultados y excepciones en colas separadas.
- Join con timeout y comprobacion de threads vivos.
- Cero `sleep()` como mecanismo de sincronizacion.

| Caso | Threads | Conexiones | Resultado esperado | Resultado obtenido | Registros finales | Excepciones | Estado |
| ---- | ------: | ---------: | ------------------ | ------------------ | ----------------: | ----------- | ------ |
| 1 - Misma idempotencia | 2 | 2 | Una operacion | Ambos callers recibieron el mismo ID; uno creo | 1 documento | 0 | Aprobado |
| 2 - Misma identidad y numero | 2 | 2 | Un registro y conflicto controlado | Un insert y un `IntegrityError` esperado | 1 documento | 1 esperada | Aprobado |
| 3 - Reserva simultanea | 2 | 2 | Una reserva coherente y numeros no duplicados | Una reserva; segundo caller bloqueado; reintento obtuvo 2 | 2 documentos, numeros 1 y 2 | 1 `ValidationError` esperada | Aprobado |
| 4 - Dos vendedores | 2 | 2 | Una emision efectiva | Un intento, un actor efectivo, un autorizado | 1 documento, 1 intento | 1 `ValidationError` esperada | Aprobado |
| 5 - Dos workers | 2 | 2 | Una consulta efectiva | Un consulto; el otro recibio operacion en curso | 1 intento de recovery | 0 | Aprobado |
| 6 - Reintento incierto | 2 | 2 | No reemitir; recuperar | Un recovery y bloqueo de reemision | 1 authorize + 1 recover | 1 `ValidationError` esperada | Aprobado |
| 7 - CAE y fallo de persistencia | 2 | 2 | No declarar autorizado; conservar frontera | Quedo `SUBMITTING`, luego `UNCERTAIN` y se recupero sin segundo authorize | 1 authorize + 1 recover | 1 `ValidationError` esperada | Aprobado |
| 8 - Doble HTTP | 2 | 2 | Encolar una vez | Dos respuestas 302 coherentes y un solo `.delay()` | 1 timestamp de despacho, 0 intentos remotos | 0 | Aprobado |

Repeticiones del modulo completo:

- 8/8 en 20,540 s.
- 8/8 en 17,140 s.
- 8/8 en 28,561 s.
- 8/8 dentro de la suite PostgreSQL completa.

No se observaron deadlocks ni comportamiento flaky.

## F. Resultados completos de tests

### PostgreSQL 18

Motor: PostgreSQL 18.1, 64 bits.

| Ejecucion | Total | Aprobados | Fallidos | Errores | Omitidos | Duracion |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| PDF/QR/snapshot | 4 | 4 | 0 | 0 | 0 | 36,981 s |
| Concurrencia repeticion 1 | 8 | 8 | 0 | 0 | 0 | 20,540 s |
| Concurrencia repeticion 2 | 8 | 8 | 0 | 0 | 0 | 17,140 s |
| Concurrencia repeticion 3 | 8 | 8 | 0 | 0 | 0 | 28,561 s |
| Suite completa fresca | 571 | 571 | 0 | 0 | 0 | 170,151 s |

Validaciones de migraciones:

- `makemigrations --check --dry-run`: sin cambios.
- Base limpia nueva `webflexs_pre_hom_clean_20260730`: 120 migraciones, sin pendientes.
- Upgrade nuevo `webflexs_pre_hom_upgrade_20260730`:
  `accounts 0018 / core 0032 / orders 0016` a
  `accounts 0019 / core 0033` y latest: aprobado, 120 migraciones.
- `manage.py check`: solamente `arca.I001`, que confirma ARCA deshabilitada.

Corrida invalida, conservada por transparencia:

- Se reutilizo `test_webflexs_test` con `--keepdb` despues de ejecutar
  `TransactionTestCase` individuales.
- Esa base habia sido vaciada por `flush`, incluidos datos de migraciones.
- Resultado: 571 tests, 70 fallos, 204 errores, 106,059 s.
- Evidencia raiz: `SalesDocumentTypeSeedTests` encontro cero empresas y varios
  endpoints devolvieron redirects por ausencia de seeds.
- Esta corrida no se uso para el veredicto. Se corrigio el entorno creando un
  nombre de base de test nuevo; no se borro ni sobreescribio la base anterior.

### SQLite

| Ejecucion | Total | Aprobados | Fallidos | Errores | Omitidos | Duracion |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Suite completa compatible | 571 | 563 | 0 | 0 | 8 | 115,850 s |

Los ocho omitidos son exactamente los ocho metodos de
`PostgreSQLFiscalConcurrencyTests`.

Motivo comun y explicito:

`requires PostgreSQL row locks, partial unique constraints, and independent backend connections; SQLite cannot validate these semantics`

PDF, QR, watermark y snapshot si se ejecutaron y pasaron en SQLite.

## G. Estado de Git

Inventario inicial subyacente:

- 74 rutas modificadas o borradas.
- 84 archivos no rastreados.
- Total: 158 rutas.
- 3 archivos `MM`.
- HEAD sin cambios durante toda la tarea.

Inventario final subyacente:

- 74 rutas modificadas o borradas.
- 89 archivos no rastreados, luego de agregar este informe.
- Total: 163 rutas.
- 3 archivos `MM`, sin tocar:
  - `admin_panel/templates/admin_panel/base.html`
  - `core/static/core/css/base.css`
  - `templates/base.html`

Archivos relacionados creados en esta etapa:

- `core/test_fiscal_pdf_binary.py`
- `core/test_fiscal_concurrency.py`
- `requirements-arca-validation.txt`
- `docs/arca/ARCA_PRE_HOMOLOGATION_INVENTORY_2026-07-30.md`
- `docs/arca/ARCA_PRE_HOMOLOGATION_EXECUTION_REPORT_2026-07-30.md`

Archivos existentes modificados de manera minima:

- `core/services/pdf_generator.py`: fetch local de assets Django.
- `.gitignore`: se agrego solamente `output/pdf/` en esta etapa.

Cambios preexistentes relevantes, no atribuibles por completo a esta etapa:

- `core/services/pdf_generator.py` ya contenia el hardening fiscal de QR.
- `.gitignore` ya contenia cambios no indexados sobre secretos y certificados.
- `core/test_fiscal_readiness.py` y el resto del bloque fiscal siguen no
  rastreados.

Sensibles detectados por nombre:

| Ruta | Tipo de riesgo | Estado | Accion recomendada |
| --- | --- | --- | --- |
| `.env` | Configuracion/secretos local | Ignorado, no leido, no indexado | Mantener ignorado |
| `.env.example` | Plantilla | Rastreado y modificado | Revisar solo nombres/placeholders antes de commit |
| `.claude/settings.local.json` | Configuracion local | No rastreado | No incluir en commits |
| `catalogopro_build/api/appsettings.json` | Configuracion de autoria incierta | Modificado | Revisar por separado, no incluir |
| `core/test_security_hardening.py` | Literal de marcador de clave privada | Test preexistente, no es una clave | Mantener como fixture de deteccion |

No se detectaron por nombre archivos `.pem`, `.key`, `.crt`, `.cer`, `.p12`,
`.pfx`, `.der`, `.jks` o `.keystore` rastreados o no rastreados.

Archivos generados:

- Veinte assets con hash bajo `catalogopro_build/frontend/assets/`, excluidos.
- PDFs y PNGs de esta prueba bajo `tmp/` u `output/pdf/`, ignorados.

No se perdio, reemplazo ni descarto ningun cambio.

## H. Commits locales

No se creo ningun commit local.

Motivos:

- `core/services/pdf_generator.py` mezcla el hardening fiscal preexistente con
  el hunk nuevo del fetcher.
- `.gitignore` mezcla cambios preexistentes con la linea nueva.
- Los tests nuevos dependen de `core/test_fiscal_readiness.py`, todavia no
  rastreado y de una etapa anterior.
- Crear commits incompletos no seria aislado ni reproducible desde HEAD.

Propuesta exacta, una vez que el bloque fiscal preexistente tenga autoria y
scope confirmados:

1. `test(pdf): validate real WeasyPrint binary generation`
   - `requirements-arca-validation.txt`
   - hunk local de assets de `core/services/pdf_generator.py`
   - smoke/test binario de `core/test_fiscal_pdf_binary.py`
2. `test(fiscal-pdf): verify fail-closed snapshots watermark and QR`
   - resto fiscal atribuible de `core/test_fiscal_pdf_binary.py`
   - dependencias fiscales ya aprobadas, incluido `core/test_fiscal_readiness.py`
3. `test(fiscal-concurrency): add PostgreSQL multithread transaction tests`
   - `core/test_fiscal_concurrency.py`
   - fixtures fiscales requeridos, una vez aprobados
4. `chore(git): ignore generated fiscal test artifacts`
   - solamente el hunk `output/pdf/` de `.gitignore`
5. `docs(arca): document pre-homologation validation results`
   - `docs/arca/ARCA_PRE_HOMOLOGATION_INVENTORY_2026-07-30.md`
   - `docs/arca/ARCA_PRE_HOMOLOGATION_EXECUTION_REPORT_2026-07-30.md`

No se ejecuto `git add`, no se altero el index y no se hizo push.

## I. Riesgos restantes

### Criticos

Ninguno identificado dentro del alcance autorizado.

### Altos

- Working tree con 163 rutas subyacentes y tres `MM`. Antes de preparar
  credenciales debe aislarse el bloque fiscal en commits revisados o en un
  worktree limpio, sin resolver los `MM` arbitrariamente.

### Medios

- WeasyPrint depende de Pango nativo instalado por maquina. Debe repetirse la
  preparacion MSYS2 en cada nuevo entorno.
- WeasyPrint 69.0 declara Python >=3.10; el entorno local demostro funcionamiento
  real en Python 3.14.2, aunque la documentacion publicada menciona pruebas
  explicitas hasta versiones anteriores.
- Reutilizar con `--keepdb` una base que ya fue objeto de `TransactionTestCase`
  individuales puede eliminar seeds de migraciones. Para la suite global usar
  una base nueva o no usar `--keepdb`.

### No bloqueantes

- WeasyPrint informa que `box-shadow` no es una propiedad soportada; no afecta
  el contenido fiscal, QR ni watermark.
- Los settings de test advierten que `STATIC_ROOT` temporal no existe. El
  fetcher local encuentra los assets en los static directories de las apps.

## J. Prohibiciones verificadas

- No hubo conexion con ARCA.
- No se llamo WSAA.
- No se llamo WSFE.
- No se usaron certificados.
- No se usaron claves privadas.
- No se probo homologacion real.
- No se probo ni modifico produccion.
- No se cambiaron Firebase Rules.
- No hubo deploy.
- No hubo merge.
- No hubo push.
- No se uso `git reset`.
- No se uso `git checkout --`.
- No se uso `git restore`.
- No se uso `git clean`.
- No se borraron archivos no rastreados.
- No se descartaron cambios existentes.
- No se tocaron los tres archivos `MM`.
- No se redujeron validaciones.
- No se ocultaron tests fallidos: la corrida contaminada esta documentada.
- Las corridas validas terminaron con cero fallos y cero errores.
- ARCA permanece deshabilitada; `manage.py check` devuelve `arca.I001`.

## K. Veredicto final

# LISTO PARA PREPARAR CREDENCIALES DE HOMOLOGACIÓN

Justificacion:

- WeasyPrint esta operativo.
- El PDF binario real se genero y se reviso visualmente.
- Los estados sin CAE fallan cerrados.
- El autorizado usa snapshot historico.
- El watermark se valido en el PDF real.
- El QR se extrajo y decodifico.
- El payload coincide con los datos persistidos simulados.
- Los ocho casos multithread pasaron en PostgreSQL con conexiones
  independientes y barrera.
- Idempotencia, numeracion, workers, incertidumbre y recuperacion evitan
  duplicados.
- PostgreSQL completo paso 571/571.
- SQLite compatible paso sin fallos ni errores; los ocho skips tienen
  justificacion PostgreSQL explicita.
- Git esta clasificado, no se perdieron cambios y no hay secretos preparados
  para commit.
- No se creo ningun commit inseguro.
- ARCA continua completamente deshabilitada.

El siguiente paso debe ser una auditoria separada. Esta orden no autoriza
preparar, cargar ni probar credenciales.
