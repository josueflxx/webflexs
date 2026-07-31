# Acciones del usuario para homologación ARCA

Esta guía no autoriza conexiones ni emisión. No compartas por chat la clave
fiscal, la clave privada, el certificado completo, contraseñas, Token, Sign,
CMS, archivos `.env`, cookies, sesiones ni dumps de variables.

## Responsabilidades separadas

Acciones exclusivas del usuario:

- Generar y custodiar la clave fuera del repositorio.
- Generar el CSR e ingresar personalmente a WSASS.
- Descargar el certificado de testing y crear la autorización de servicio.
- Confirmar CUIT representado, identificador exacto del servicio, punto de
  venta y tipo de comprobante.
- Configurar Redis o Memcached y cargar las variables sólo en el entorno
  local.
- Habilitar al final la señal de solo lectura.

Acciones de Codex, únicamente bajo una orden posterior:

- Auditar la configuración y validar metadatos, permisos, certificado y clave.
- Ejecutar la compuerta offline.
- Si la compuerta devuelve `PASS` y existe otra orden expresa, ejecutar las
  lecturas controladas.

Codex no debe ingresar a WSASS, recibir secretos ni habilitar la señal en
nombre del usuario.

Documentación oficial verificada:

- WSASS para testing:
  https://arca.gob.ar/ws/WSASS/html/index.html
- Generación del certificado mediante WSASS:
  https://arca.gob.ar/ws/WSASS/html/crearcertificado.html
- Autorización del certificado, servicio y CUIT representado:
  https://www.arca.gob.ar/ws/WSASS/html/conceptos.html
- WSAA y endpoint de testing:
  https://www.arca.gob.ar/ws/documentacion/wsaa.asp
- Manual vigente de WSFEv1:
  https://www.arca.gob.ar/fe/ayuda/documentos/wsfev1-RG-4291.pdf

## 1. Crear una carpeta privada fuera del repositorio

En Windows PowerShell:

```powershell
$secureDir = Join-Path $env:USERPROFILE ".webflexs\arca-homologacion"
New-Item -ItemType Directory -Path $secureDir -Force
icacls $secureDir /inheritance:r
icacls $secureDir /grant:r "${env:USERNAME}:(OI)(CI)F"
```

En Linux o macOS:

```bash
mkdir -p "$HOME/.webflexs/arca-homologacion"
chmod 700 "$HOME/.webflexs/arca-homologacion"
```

No uses una carpeta sincronizada públicamente ni una ubicación dentro de
WebFlexs.

## 2. Generar la clave y el CSR localmente

Desde la carpeta segura:

```bash
openssl genrsa -out arca-homologacion.key 2048
openssl req -new -key arca-homologacion.key -out arca-homologacion.csr
```

Antes de responder los campos del CSR, consultá el manual vigente de WSASS.
No inventes el DN. WSASS exige que `serialNumber` use el formato indicado por
ARCA y corresponda al CUIT del usuario conectado.

La clave privada no se sube a WSASS. Solamente se carga el CSR.

## 3. Gestionar el certificado en WSASS

Esta parte debe realizarla el usuario:

1. Ingresar al portal de ARCA con clave fiscal.
2. Abrir WSASS con la identidad personal requerida por el servicio.
3. Crear o seleccionar el alias/DN correcto.
4. Subir exclusivamente el CSR.
5. Descargar el certificado X.509 de testing.
6. Guardarlo en la carpeta privada externa.
7. Crear la autorización que relacione certificado, servicio y CUIT
   representado.
8. Consultar el catálogo de WSASS y registrar localmente el identificador
   exacto del servicio. No asumirlo a partir de ejemplos.

## 4. Confirmar los datos de consulta

Confirmá personalmente:

- CUIT representado.
- Punto de venta de homologación habilitado para WSFEv1.
- Tipo de comprobante que se consultará.
- Identificador de servicio mostrado por WSASS.
- Autorización WSASS creada.

No pruebes puntos de venta, CUIT o servicios al azar.

## 5. Cargar la configuración local

Usá como esquema:

`docs/arca/arca-homologacion.env.example`

Los valores reales deben estar en variables locales, un gestor de secretos o
un archivo ignorado fuera del repositorio. WebFlexs usa un caché Django
compartido Redis o Memcached para el Ticket de Acceso. El caché de Token/Sign
en archivos está bloqueado.

Configuración local de Redis:

```dotenv
ARCA_TOKEN_CACHE_BACKEND=redis
ARCA_TOKEN_CACHE_URL=redis://127.0.0.1:<PUERTO>/<DB>
ARCA_TOKEN_CACHE_PREFIX=webflexs:arca:homo
```

Configuración local alternativa de Memcached:

```dotenv
ARCA_TOKEN_CACHE_BACKEND=memcached
ARCA_TOKEN_CACHE_URL=127.0.0.1:<PUERTO>
ARCA_TOKEN_CACHE_PREFIX=webflexs:arca:homo
```

Ejemplos orientativos con Docker, para ejecutar sólo por decisión del usuario
y reemplazando la imagen por una versión/digest aprobados localmente:

```powershell
docker run --rm --name webflexs-arca-redis -p 127.0.0.1:<PUERTO>:6379 <IMAGEN_REDIS_APROBADA>
docker run --rm --name webflexs-arca-memcached -p 127.0.0.1:<PUERTO>:11211 <IMAGEN_MEMCACHED_APROBADA>
```

No inicies ambos backends. No uses `LocMemCache`, `DatabaseCache`,
`FileBasedCache` ni `DummyCache`. El servicio debe permanecer enlazado a
loopback durante la homologación local.

Esta etapa admite una clave privada sin passphrase y protegida por permisos del
sistema operativo. Los archivos de passphrase están bloqueados hasta que se
implemente y audite su manejo sin exposición en procesos o logs.

## 6. Señal para solicitar la comprobación

Completá primero el checklist de `docs/arca/QUESTIONS_FOR_USER.md`.

Después de crear personalmente la autorización, confirmá:

```text
ARCA_WSASS_AUTHORIZATION_CONFIRMED=true
```

La última variable que debe habilitarse es:

```text
READY_ARCA_HOMOLOGACION_READONLY=true
```

Además deben habilitarse explícitamente la integración, red y lectura.
`ARCA_HOMOLOGATION_EMISSION_ENABLED` y `ARCA_PRODUCTION_ENABLED` deben
permanecer en `false`.

Luego pedile a Codex que ejecute únicamente la compuerta local. Una conexión
real solamente podrá evaluarse si la compuerta devuelve:

```text
ARCA_HOMOLOGATION_READINESS_GATE=PASS
```
