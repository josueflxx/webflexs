# ARCA — Fase 1.1: configuración privada offline

Fecha: 2026-08-12  
Resultado: código y plantilla preparados; configuración privada real no modificada.  
Restricciones respetadas: sin ARCA, sin TA, sin probes, sin emisión y sin deploy.

## A. Variables exactas

### Activación y guardas

- `ARCA_ENABLED`
- `ARCA_ENVIRONMENT` — único valor operativo permitido: `homologacion`.
- `ARCA_HOMOLOGATION_NETWORK_ENABLED`
- `ARCA_HOMOLOGATION_READ_ENABLED`
- `READY_ARCA_HOMOLOGACION_READONLY`
- `ARCA_TAXPAYER_READ_ENABLED`
- `READY_ARCA_TAXPAYER_READONLY`
- `ARCA_WSASS_AUTHORIZATION_CONFIRMED`
- `ARCA_HOMOLOGATION_EMISSION_ENABLED` — debe permanecer `false`.
- `ARCA_PRODUCTION_ENABLED` — debe permanecer `false`.
- `ARCA_TLS_VERIFY` y `ARCA_REDACT_SECRETS` — deben permanecer `true`.

Las banderas de red/lectura son requisitos del gate; por sí solas no abren una conexión. Durante Fase 1.1 sólo deben usarse en el comando offline con bloqueo de sockets. No ejecutar servidor, worker ni probe con estas banderas hasta autorización separada.

### Servicios y endpoints

- `ARCA_WSAA_URL`
- `ARCA_WSFE_URL`
- `ARCA_WSFE_WSDL`
- `ARCA_TAXPAYER_URL`
- `ARCA_TAXPAYER_WSDL`
- `ARCA_WSFE_SERVICE_ID=wsfe`
- `ARCA_TAXPAYER_SERVICE_ID=ws_sr_constancia_inscripcion`

### Credencial e identidad

- `ARCA_CREDENTIAL_ID`
- `ARCA_CUIT`
- `ARCA_CERT_PATH`
- `ARCA_PRIVATE_KEY_PATH`
- `ARCA_EXPECTED_CERT_SUBJECT_CN`
- `ARCA_EXPECTED_CERT_ISSUER_CN`
- `ARCA_EXPECTED_CERT_SHA256` — opcional, recomendado.
- `ARCA_OPENSSL_BIN`
- `ARCA_PRIVATE_KEY_PASSPHRASE_FILE` — debe quedar vacío en esta etapa.

### Redis y TA

- `ARCA_TOKEN_CACHE_ENABLED`
- `ARCA_TOKEN_CACHE_BACKEND=redis`
- `ARCA_TOKEN_CACHE_URL`
- `ARCA_TOKEN_CACHE_PREFIX`
- `ARCA_TOKEN_CACHE_PATH` — debe quedar vacío.
- `ARCA_TA_RENEWAL_MARGIN_SECONDS`
- `ARCA_WSAA_LOCK_SECONDS`
- `ARCA_WSAA_WAIT_SECONDS`

### Empresa, POS, comprobante y padrón

- La empresa no se selecciona por variable global. Los comandos aceptan `--company-id`.
- El POS se selecciona con `--point-of-sale-id` y debe coincidir con `ARCA_PTO_VTA`.
- `ARCA_DEFAULT_CBTE_TIPO` recibe el ID numérico ARCA candidato.
- `ARCA_TEST_TAXPAYER_CUIT` es opcional y debe quedar vacío hasta autorizar el probe.

### Ambigüedades existentes

- `ARCA_SERVICE_ID` es el nombre legacy de `ARCA_WSFE_SERVICE_ID`; configurar sólo el nombre nuevo.
- `ARCA_COMPANY_CONFIG_JSON` es legacy; `ARCA_CREDENTIALS_CONFIG_JSON` es el nombre primario para configuración multiempresa. Para la única empresa emisora actual alcanza la configuración global.
- `REDIS_URL` configura la caché general/Celery. Cuando `ARCA_TOKEN_CACHE_BACKEND=redis`, `ARCA_TOKEN_CACHE_URL` tiene prioridad y configura la caché Django usada por TA.
- `ARCA_WSAA_SERVICE` es un alias interno, no una variable que deba completarse.
- `ARCA_CONNECT_TIMEOUT_SECONDS` está declarado pero el transporte actual utiliza `ARCA_READ_TIMEOUT_SECONDS` como timeout efectivo único.

## B. Plantilla privada

La plantilla terminada está en `docs/arca/arca-homologacion.private.env.example`. No contiene valores reales. El `.env` real no fue leído, creado ni modificado.

## C. Redis local

El repositorio incluye el cliente Python `redis` y usa Redis para caché/Celery en VPS, pero no contiene Dockerfile ni Compose. En esta PC no se encontró Docker, servidor/CLI Redis ni distribución WSL y no hay listener en 6379.

Recomendación: después de aprobación del usuario, instalar [Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/) y ejecutar la [imagen oficial de Redis](https://hub.docker.com/_/redis) fijada a `redis:8.2.8-alpine`, ligada únicamente a `127.0.0.1`. Para TA local se recomienda sin persistencia RDB/AOF. No se instaló ni descargó nada durante esta fase.

## D. Empresas fiscales

Modelo emisor: `core.models.Company`; campo fiscal: `cuit`.

Hay cuatro empresas locales. Sólo la empresa ID `1`, activa, tiene CUIT configurado y coincide con el subject del certificado: `20*******72`. Las IDs 2, 3 y 4 no tienen CUIT fiscal configurado. Ningún dato fue modificado.

Los comandos offline ahora aceptan `--company-id`; el probe futuro ya lo exigía.

## E. POS y tipos candidatos

- POS ID `1`, empresa ID `1`, número `1`, homologación, activo y default.
- POS ID `2`, empresa ID `2`, número `1`, homologación, activo y default.

Ambos son sólo registros locales: `arca_verified=false`.

Para la empresa emisora ID `1` existen series locales:

- `FA` → candidato ARCA `CbteTipo=1`.
- `FB` → candidato ARCA `CbteTipo=6`.

También existen tipos comerciales A/B y notas de crédito, pero no deben elegirse automáticamente. El usuario debe elegir un candidato para que el gate WSFE pase. La verdad remota futura será `FEParamGetPtosVenta` y, después, los catálogos WSFE.

## F. Bloqueos restantes

Con valores públicos y credenciales reales cargados sólo en un proceso efímero, y con DNS/socket/HTTP/SOAP bloqueados:

- Doctor: `WAITING_FOR_USER`.
- Gate WSFE: `FAIL` por `ticket_cache_disabled`, `point_of_sale_missing` y `voucher_type_missing`.
- Gate padrón: `FAIL` sólo por `ticket_cache_disabled`.
- Credencial: validada completamente offline.

Restan exactamente:

1. instalar/iniciar Redis local y completar las variables de caché;
2. seleccionar POS candidato ID `1` / número `1` para la empresa emisora, sin declararlo verificado;
3. seleccionar el tipo candidato `1` o `6` según el comprobante de la futura prueba;
4. cargar manualmente la configuración privada;
5. volver a ejecutar el comando offline consolidado.

`ARCA_TEST_TAXPAYER_CUIT` no bloquea doctor/gates y debe seguir vacío.

## G. Cambios realizados

- CN esperado del sujeto y del emisor ahora se validan con OpenSSL.
- `ARCA_OPENSSL_BIN` ahora admite una ruta privada/configurable.
- Se agregó `ARCA_TEST_TAXPAYER_CUIT` como entrada opcional del probe futuro.
- Doctor muestra si identidad esperada y credencial fueron validadas.
- Doctor/gates aceptan selección local explícita de empresa/POS.
- Nuevo comando `arca_phase11_offline_check` bloquea primitives de red y ejecuta sólo diagnósticos.
- Plantillas públicas actualizadas; `.env` real intacto.

## H. Tests

- Suite dirigida final: 145 casos; 137 aprobados y 8 omitidos por requerir PostgreSQL.
- Validación criptográfica real offline: PASS, sin imprimir fingerprint, modulus, CN, issuer, rutas ni material de clave.
- Diagnóstico aislado: `network_guard=PASS`.

## I. Confirmación

**NO SE REALIZÓ TRÁFICO A ARCA.**

No se resolvió DNS de ARCA, no se abrió SOAP, no se obtuvo TA, no se ejecutó `dummy`, `FEDummy`, `getPersona_v2`, `FEParamGetPtosVenta`, `FECompUltimoAutorizado` ni `FECAESolicitar`.

## J. Comandos manuales

No ejecutar estos pasos hasta aceptar la instalación de Docker Desktop. Después de instalarlo y abrirlo:

```powershell
docker pull redis:8.2.8-alpine
docker run --name webflexs-arca-redis --detach --restart unless-stopped --publish 127.0.0.1:6379:6379 redis:8.2.8-alpine redis-server --save "" --appendonly no
docker exec webflexs-arca-redis redis-cli ping
```

Resultado requerido: `PONG`.

Luego completar manualmente un entorno privado usando `docs/arca/arca-homologacion.private.env.example`. Elegir `ARCA_DEFAULT_CBTE_TIPO=1` o `6`; Codex no eligió ninguno.

Con empresa/POS candidatos de la base local:

```powershell
python manage.py arca_phase11_offline_check --company-id 1 --point-of-sale-id 1
python manage.py arca_homologation_doctor --company-id 1 --point-of-sale-id 1
python manage.py arca_homologation_gate --company-id 1 --point-of-sale-id 1
python manage.py arca_taxpayer_homologation_gate --company-id 1
```

No ejecutar los comandos que contienen `probe`. Después del diagnóstico, volver a dejar las banderas de red/lectura/readiness en `false` hasta obtener autorización separada.

## K. VPS posterior

Fuera del alcance actual quedan: directorio de credenciales externo al checkout (por ejemplo bajo `/etc`), key `0600`, propietario del servicio, usuarios efectivos de Gunicorn/Celery, Redis privado real, EnvironmentFile de systemd, SHA desplegado, staging, secuencia de restart y rollback. No se inspeccionó ni modificó el VPS.
