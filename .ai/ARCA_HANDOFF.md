==============================
PARA CHATGPT — HANDOFF ARCA
==============================

Fecha: 2026-08-12  
Alcance: Fase 1.1 privada offline  
ARCA/producción/emisión/deploy: no ejecutados

## 1. ESTADO ACTUAL

La configuración privada real no fue modificada. Se prepararon settings, plantillas y un comando offline consolidado. Con valores conocidos cargados sólo en un proceso efímero y sockets bloqueados, las credenciales validan; faltan Redis y la elección humana de POS/tipo para que todos los gates pasen.

## 2. VARIABLES REQUERIDAS

Activación: `ARCA_ENABLED`, `ARCA_ENVIRONMENT`, `ARCA_HOMOLOGATION_NETWORK_ENABLED`, `ARCA_HOMOLOGATION_READ_ENABLED`, `READY_ARCA_HOMOLOGACION_READONLY`, `ARCA_TAXPAYER_READ_ENABLED`, `READY_ARCA_TAXPAYER_READONLY`, `ARCA_WSASS_AUTHORIZATION_CONFIRMED`, `ARCA_TLS_VERIFY`, `ARCA_REDACT_SECRETS`.

Guardas obligatorias: `ARCA_HOMOLOGATION_EMISSION_ENABLED=false` y `ARCA_PRODUCTION_ENABLED=false`.

Servicios/endpoints: `ARCA_WSAA_URL`, `ARCA_WSFE_URL`, `ARCA_WSFE_WSDL`, `ARCA_TAXPAYER_URL`, `ARCA_TAXPAYER_WSDL`, `ARCA_WSFE_SERVICE_ID=wsfe`, `ARCA_TAXPAYER_SERVICE_ID=ws_sr_constancia_inscripcion`.

Credencial: `ARCA_CREDENTIAL_ID`, `ARCA_CUIT`, `ARCA_CERT_PATH`, `ARCA_PRIVATE_KEY_PATH`, `ARCA_EXPECTED_CERT_SUBJECT_CN`, `ARCA_EXPECTED_CERT_ISSUER_CN`, `ARCA_EXPECTED_CERT_SHA256` opcional y `ARCA_OPENSSL_BIN`.

WSFE: `ARCA_PTO_VTA`, `ARCA_DEFAULT_CBTE_TIPO`. Padrón futuro: `ARCA_TEST_TAXPAYER_CUIT`, opcional y actualmente vacío.

Legacy: no usar `ARCA_SERVICE_ID` si se usa `ARCA_WSFE_SERVICE_ID`; no usar `ARCA_COMPANY_CONFIG_JSON` si se usa `ARCA_CREDENTIALS_CONFIG_JSON`.

## 3. REDIS

El proyecto usa la caché Django compartida y exige `ARCA_TOKEN_CACHE_ENABLED`, `ARCA_TOKEN_CACHE_BACKEND=redis`, `ARCA_TOKEN_CACHE_URL`, `ARCA_TOKEN_CACHE_PREFIX`; `ARCA_TOKEN_CACHE_PATH` debe quedar vacío.

No hay Compose/Dockerfile. Esta PC no tiene Docker, Redis nativo ni WSL disponible. No se instaló nada. Recomendación pendiente de aprobación: Docker Desktop + contenedor oficial Redis fijado, ligado sólo a loopback y sin persistencia para TA locales.

## 4. CREDENCIALES

La pareja externa existente se validó offline con OpenSSL:

- archivos existentes, regulares, legibles y fuera del checkout;
- ACL de key aceptada;
- certificado parseable y vigente;
- ambiente etiquetado homologación;
- CUIT del subject coincidente;
- CN del subject y CN del issuer comparables contra valores esperados;
- cert/key correspondientes;
- sin exponer modulus, fingerprint, rutas ni material privado.

## 5. EMPRESA

Modelo: `core.models.Company`; campo: `cuit`.

Hay cuatro empresas locales. Sólo ID `1`, activa, tiene CUIT y coincide con el certificado: `20*******72`. Las demás no tienen CUIT fiscal configurado. No se modificaron registros.

Doctor/gates ahora aceptan `--company-id`; no se agregó una variable global redundante.

## 6. POS / TIPO

Para empresa ID `1` existe POS local ID `1`, número `1`, homologación, activo/default. Sigue siendo candidato: `arca_verified=false`.

Series locales de esa empresa/POS:

- `FA` → `CbteTipo=1` candidato.
- `FB` → `CbteTipo=6` candidato.

El usuario debe elegir. Ninguno fue confirmado ni persistido como verificado. `FEParamGetPtosVenta` será la fuente futura.

## 7. DOCTOR

Ejecución efímera, con valores conocidos y red bloqueada:

`ARCA_HOMOLOGATION_DOCTOR=WAITING_FOR_USER`

Credencial validada: sí. Motivos restantes: `ticket_cache_disabled`, `point_of_sale_missing`, `voucher_type_missing`.

## 8. GATE WSFE

`FAIL` únicamente por:

- `ticket_cache_disabled`;
- `point_of_sale_missing`;
- `voucher_type_missing`.

## 9. GATE PADRÓN

`FAIL` únicamente por `ticket_cache_disabled`.

`ARCA_TEST_TAXPAYER_CUIT` no es requisito del gate y debe seguir vacío hasta autorización posterior.

## 10. ARCHIVOS MODIFICADOS

- Settings: OpenSSL configurable, identidad esperada y CUIT opcional de prueba.
- Validación criptográfica: CN de sujeto/emisor.
- Doctor: nueva evidencia sanitizada.
- Comandos doctor/gates: selección explícita de empresa/POS.
- Nuevo `arca_phase11_offline_check`: bloquea DNS/socket/HTTP/SOAP y no ejecuta probes.
- Plantillas públicas y reporte Fase 1.1.
- `.env` real, base fiscal y VPS: sin cambios.

## 11. TESTS

- Suite dirigida final: 145 casos; 137 aprobados y 8 skips esperados por PostgreSQL.
- Tests nuevos de identidad CN, setting de CUIT futuro y comando offline.
- Validación criptográfica real offline: PASS.
- Diagnóstico con red mockeada: PASS del guard.
- No se ejecutó ningún probe.

## 12. ACCIONES MANUALES DEL USUARIO

1. Aprobar e instalar Docker Desktop, o proporcionar un Redis privado local equivalente.
2. Iniciar Redis y verificar `PONG`.
3. Completar manualmente la plantilla privada, sin commit.
4. Elegir POS candidato empresa 1 / POS ID 1 / número 1.
5. Elegir `CbteTipo` candidato 1 o 6 según la futura prueba.
6. Ejecutar `arca_phase11_offline_check --company-id 1 --point-of-sale-id 1`.
7. Compartir sólo estados y códigos sanitizados, nunca `.env`, key, Token o Sign.

## 13. ¿ESTÁ LISTO PARA PEDIR AUTORIZACIÓN DEL PROBE READ-ONLY? NO

## 14. JUSTIFICACIÓN

El código y la credencial están listos, pero Redis no existe en esta PC y POS/tipo todavía no fueron elegidos. Doctor y gates no pasan con la configuración real. Recién después de Redis, selección explícita y tres resultados PASS corresponderá pedir autorización separada para un probe read-only. `FECAESolicitar` continúa hard-blocked.

Confirmación: **NO SE REALIZÓ TRÁFICO A ARCA**.
