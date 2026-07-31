# Preparación segura de homologación ARCA

Fecha: 2026-07-30  
Rama: `codex/production-fiscal-client-upgrade-20260723`  
HEAD inicial y final: `a925eb434d7e5231d07c9c149d178f21ac267a0c`

## A. Resumen ejecutivo

Codex preparó una compuerta local de homologación, banderas independientes,
allowlists, validación criptográfica, consultas WSFEv1 de solo lectura, dos
comandos controlados, documentación y tests offline.

El usuario todavía no realizó las acciones de WSASS ni proporcionó la señal
local. No se le solicitaron secretos.

Conexiones ejecutadas por la aplicación:

- WSAA: ninguna.
- WSFEv1: ninguna.
- Producción ARCA: ninguna.

Solamente se consultó documentación pública oficial para verificar endpoints,
WSASS y operaciones disponibles:

- https://www.arca.gob.ar/ws/documentacion/wsaa.asp
- https://arca.gob.ar/ws/WSASS/html/index.html
- https://www.arca.gob.ar/ws/WSASS/html/conceptos.html
- https://www.arca.gob.ar/fe/ayuda/documentos/wsfev1-RG-4291.pdf
- https://wswhomo.afip.gob.ar/wsfev1/service.asmx

Estado general: la preparación local y sus regresiones están aprobadas. La
conexión real está bloqueada por precondiciones del usuario pendientes.

## B. Acciones del usuario

| Acción | Estado | Evidencia no sensible | Bloqueo |
| --- | --- | --- | --- |
| Ingreso a WSASS | Pendiente | No suministrada | Sí |
| Generación local de clave y CSR | Pendiente | No suministrada | Sí |
| Descarga del certificado testing | Pendiente | No suministrada | Sí |
| Autorización certificado/servicio/CUIT | Pendiente | No suministrada | Sí |
| Confirmación del identificador de servicio | Pendiente | No suministrada | Sí |
| Confirmación de CUIT representado | Pendiente | No suministrada | Sí |
| Confirmación del punto de venta WSFEv1 | Pendiente | No suministrada | Sí |
| Confirmación del tipo de comprobante | Pendiente | No suministrada | Sí |
| Configuración de caché compartido | Pendiente | No suministrada | Sí |
| Variables locales cargadas | Pendiente | No suministrada | Sí |
| `READY_ARCA_HOMOLOGACION_READONLY=true` | Pendiente | Sigue en falso | Sí |

Instrucciones entregadas en:

- `docs/arca/ARCA_HOMOLOGACION_ACCIONES_USUARIO.md`
- `docs/arca/QUESTIONS_FOR_USER.md`
- `docs/arca/arca-homologacion.env.example`

## C. Seguridad de credenciales

| Control | Resultado |
| --- | --- |
| Ubicación fuera del repositorio | Pendiente; no se recibieron rutas |
| Permisos correctos | Pendiente |
| Certificado vigente | Pendiente |
| Subject compatible con CUIT | Pendiente |
| Certificado y clave coincidentes | Pendiente |
| Fingerprint opcional coincidente | Pendiente |
| Secretos nuevos encontrados en Git | 0 |
| Secretos encontrados en logs de esta tarea | 0 |

La validación preparada comprueba:

- ruta canónica fuera del repositorio;
- archivo regular y no symlink;
- permisos POSIX restrictivos;
- ACL de Windows sin principales amplios;
- vigencia;
- integridad de la clave;
- CUIT del `serialNumber` del subject;
- correspondencia de claves públicas;
- fingerprint SHA-256 opcional.

No se admite archivo de passphrase en esta etapa. Token y Sign sólo pueden
vivir en caché compartido efímero Redis o Memcached. Se rechazan cachés de
archivo, base de datos, proceso local y `DummyCache`.

## D. Configuración efectiva

Configuración usada durante las pruebas:

| Campo | Valor sanitizado |
| --- | --- |
| Ambiente | `disabled` |
| Integración | `false` |
| Red homologación | `false` |
| Lectura homologación | `false` |
| Emisión homologación | `false` |
| Producción | `false` |
| Señal del usuario | `false` |
| TLS verify | `true` |
| Redacción | `true` |
| WSAA permitido | `wsaahomo.afip.gov.ar` |
| WSFE permitido | `wswhomo.afip.gov.ar` |
| Timeout de conexión propuesto | 10 s |
| Timeout de lectura propuesto | 30 s |
| Servicio | No configurado |
| CUIT | No configurado |
| Punto de venta | No configurado |
| Tipo de comprobante | No configurado |

El valor externo admitido al habilitar es `homologacion`. El valor
`homologation` queda reservado al modelo interno existente. Las URLs deben
estar presentes y coincidir exactamente con la allowlist; no existe fallback.

Jerarquía implementada:

```text
ARCA_ENABLED=true
AND ARCA_ENVIRONMENT=homologacion
AND ARCA_HOMOLOGATION_NETWORK_ENABLED=true
AND ARCA_HOMOLOGATION_READ_ENABLED=true
AND ARCA_PRODUCTION_ENABLED=false
AND ARCA_HOMOLOGATION_EMISSION_ENABLED=false
AND READY_ARCA_HOMOLOGACION_READONLY=true
```

Además, `FECAESolicitar` permanece bloqueado en código.

## E. Resultados WSAA

| Control | Resultado |
| --- | --- |
| Solicitud real | No realizada |
| Servicio solicitado | Ninguno |
| Ticket real recibido | No |
| Token real recibido | No |
| Sign real recibido | No |
| Reintentos reales | 0 |
| Contacto con producción | No |

Los mocks validaron TRA, `uniqueId`, ventana UTC, servicio explícito, firma CMS
DER con `-nodetach`, timeout, eliminación de temporales, parseo de Ticket y
redacción de fallos. No se persistieron credenciales reales.

## F. Resultados WSFEv1

| Operación | Tipo | Resultado | Errores/observaciones | Escritura |
| --- | --- | --- | --- | --- |
| `FEDummy` | Lectura | Mock aprobado; real no ejecutado | Pendiente WSAA | No |
| Parámetros | Lectura | Mocks aprobados; real no ejecutado | Pendiente WSAA | No |
| Puntos de venta | Lectura | Mock aprobado; real no ejecutado | Pendiente WSAA | No |
| `FECompUltimoAutorizado` | Lectura | Mock aprobado; real no ejecutado | Pendiente WSAA | No |
| `FECompConsultar` | Lectura opcional | Mock aprobado; real no ejecutado | Sin número confirmado | No |
| `FECAESolicitar` | Bloqueado | No ejecutado | `arca_emission_disabled` | No |
| `FECAEASolicitar` | Bloqueado | No ejecutado | Fuera de allowlist | No |
| Métodos de registración | Bloqueados | No ejecutados | Fuera de allowlist | No |

Los métodos paramétricos allowlisted son:

- `FEParamGetTiposCbte`
- `FEParamGetTiposDoc`
- `FEParamGetTiposIva`
- `FEParamGetTiposMonedas`
- `FEParamGetTiposConcepto`
- `FEParamGetPtosVenta`

## G. Tests

### PostgreSQL

- Ejecutados: 590.
- Aprobados: 590.
- Fallidos: 0.
- Errores: 0.
- Omitidos: 0.
- Duración: 167,152 s.
- Base de test nueva y efímera; no se reutilizó `--keepdb`.
- Incluyó los ocho tests de concurrencia con PostgreSQL.

### SQLite

- Ejecutados: 590.
- Aprobados: 582.
- Fallidos: 0.
- Errores: 0.
- Omitidos: 8.
- Duración: 106,546 s.
- Los ocho skips corresponden exclusivamente a concurrencia que exige locks,
  constraints y conexiones independientes de PostgreSQL.

### Tests específicos ARCA

- Suite dirigida final: 58/58.
- Configuración y allowlist: aprobadas.
- TRA, firma y WSAA mock: aprobados.
- WSFE lectura mock: aprobado.
- Seguridad criptográfica mock: aprobada.
- Caché y singleflight: aprobados.
- Bloqueo de emisión antes de login/payload/dispatch: aprobado.
- Redacción de secretos: aprobada.

### Migraciones

- `makemigrations --check --dry-run`: sin cambios detectados.
- SQLite limpio: todas las migraciones aplicadas correctamente.
- PostgreSQL de upgrade existente: `migrate --check` aprobado.
- Las suites completas también crearon y migraron bases limpias.

Un `migrate --check` aislado sobre SQLite `:memory:` devolvió código 1 porque
cada proceso comienza con una base vacía. Se documentó y se repitió con
`migrate --noinput`; las 120 migraciones finalizaron correctamente.

## H. Git

Estado inicial:

- 74 rutas modificadas o eliminadas.
- 89 archivos no rastreados.
- Total: 163.
- 3 archivos `MM`.
- 3 entradas indexadas.

Estado final, incluyendo este informe:

- 74 rutas modificadas o eliminadas.
- 97 archivos no rastreados.
- Total: 171.
- 3 archivos `MM`.
- 3 entradas indexadas.

Archivos `MM`, con HEAD, index y working tree sin cambios:

- `admin_panel/templates/admin_panel/base.html`
- `core/static/core/css/base.css`
- `templates/base.html`

Nuevos archivos de esta orden:

- `core/services/arca_homologation.py`
- `core/management/commands/arca_homologation_gate.py`
- `core/management/commands/arca_homologation_readonly_probe.py`
- `core/test_arca_homologation_gate.py`
- `docs/arca/ARCA_HOMOLOGACION_ACCIONES_USUARIO.md`
- `docs/arca/QUESTIONS_FOR_USER.md`
- `docs/arca/arca-homologacion.env.example`
- este informe.

También se realizaron hunks relacionados en archivos ARCA/settings ya
modificados o no rastreados antes de esta orden. Su autoría es mixta y no son
seguros para staging completo sin separar hunks.

No se perdió ningún cambio. No se modificó staging. No se creó commit.

Propuesta posterior de commits:

1. `feat(arca): add homologation-only configuration guardrails`
2. `test(arca): validate WSAA and WSFE homologation clients`
3. `docs(arca): add secure homologation setup guide`

No se propone un commit separado de `.gitignore`: los patrones de certificados,
claves y artefactos ya estaban presentes y no requirieron otro cambio.

## I. Errores y manejo

### Compuerta local

- Etapa: preconexión.
- Resultado: `ARCA_HOMOLOGATION_READINESS_GATE=FAIL`.
- Causa: precondiciones del usuario y variables seguras pendientes.
- Reintentos: 0.
- Bloquea WSAA/WSFE: sí.
- Acción: completar `docs/arca/QUESTIONS_FOR_USER.md`.

### SQLite `migrate --check`

- Etapa: migraciones.
- Resultado inicial: código 1.
- Causa: instancia `:memory:` nueva y sin migrar en cada proceso.
- Reintento: `migrate --noinput`.
- Resultado final: 120 migraciones aprobadas.
- Bloquea: no.

No hubo errores de red, WSAA ni WSFE porque esas fases no fueron autorizadas.

## J. Procedimiento de reversión

Estado funcional seguro:

```dotenv
ARCA_ENABLED=false
ARCA_HOMOLOGATION_NETWORK_ENABLED=false
ARCA_HOMOLOGATION_READ_ENABLED=false
ARCA_HOMOLOGATION_EMISSION_ENABLED=false
ARCA_PRODUCTION_ENABLED=false
READY_ARCA_HOMOLOGACION_READONLY=false
```

- No se creó caché real de Ticket.
- No quedaron Token, Sign, CMS o respuestas SOAP reales.
- No hay procesos backend iniciados con banderas nuevas.
- No existen credenciales que preservar o revocar en esta tarea.
- No se utilizaron `git reset`, `git restore`, `git clean` ni descartes.

## K. Prohibiciones verificadas

- No acceso a producción.
- No certificado productivo.
- No `FECAESolicitar`.
- No `FECAEASolicitar`.
- No comprobante emitido.
- No modificación de producción.
- No Firebase.
- No cambio de Firebase Rules.
- No deploy.
- No merge.
- No push.
- No reset ni rebase.
- No descarte de cambios.
- No exposición de secretos.
- No TLS inseguro ni `verify=False`.
- No reducción de validaciones.
- No tests fallidos ocultos.

# TODAVÍA NO LISTO

Bloqueos exactos:

1. El usuario debe completar WSASS y la autorización.
2. Debe generar y almacenar certificado/clave fuera del repositorio.
3. Debe confirmar servicio, CUIT, punto de venta y tipo de comprobante.
4. Debe configurar un caché compartido efímero.
5. Debe cargar las variables locales y establecer la señal final.
6. La compuerta local debe pasar con metadatos criptográficos reales.
7. Recién después podrá autorizarse una prueba WSAA/WSFE de solo lectura.

Punto de reanudación:

1. Completar `docs/arca/QUESTIONS_FOR_USER.md`.
2. No compartir ningún valor secreto por chat.
3. Informar solamente que el checklist está completo.
4. Pedir a Codex: “Ejecutá únicamente la compuerta local de homologación; no
   conectes todavía si no devuelve PASS”.

No se intentará emitir hasta resolver todos los bloqueos y recibir una orden
separada.
