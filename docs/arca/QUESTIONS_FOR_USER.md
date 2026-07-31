# Checklist para continuar con ARCA homologación

Estado actual:

```text
READY_ARCA_HOMOLOGACION_READONLY=false
```

No se debe conectar a WSAA ni WSFEv1 mientras el usuario no confirme todos los
puntos siguientes.

## WSASS y material criptográfico

- [ ] Ingresé personalmente a WSASS con clave fiscal.
- [ ] Generé la clave privada fuera del repositorio.
- [ ] Generé el CSR con el DN exigido por el manual vigente.
- [ ] Subí solamente el CSR.
- [ ] Descargué el certificado X.509 de testing.
- [ ] Certificado y clave están en una carpeta privada fuera del repositorio.
- [ ] Restringí los permisos de esa carpeta y de la clave.
- [ ] Creé la autorización entre certificado, servicio y CUIT representado.
- [ ] Confirmé en el catálogo de WSASS el identificador exacto del servicio.
- [ ] Establecí localmente `ARCA_WSASS_AUTHORIZATION_CONFIRMED=true`.

## Datos no secretos confirmados

- [ ] Confirmé localmente el CUIT representado.
- [ ] Confirmé el punto de venta de homologación.
- [ ] Confirmé que el punto corresponde a WSFEv1.
- [ ] Confirmé el tipo de comprobante para la consulta.
- [ ] Configuré un alias local no sensible para la credencial.

## Configuración local

- [ ] Cargué las variables siguiendo
      `docs/arca/arca-homologacion.env.example`.
- [ ] No guardé valores reales en archivos rastreados por Git.
- [ ] Configuré un caché Django compartido seguro para el Ticket de Acceso.
- [ ] Elegí exactamente `redis` o `memcached` en
      `ARCA_TOKEN_CACHE_BACKEND`.
- [ ] Configuré `ARCA_TOKEN_CACHE_URL` sólo hacia un servicio local/controlado.
- [ ] Configuré un `ARCA_TOKEN_CACHE_PREFIX` no sensible y exclusivo.
- [ ] `ARCA_TOKEN_CACHE_PATH` está vacío.
- [ ] `ARCA_PRIVATE_KEY_PASSPHRASE_FILE` está vacío.
- [ ] `ARCA_HOMOLOGATION_EMISSION_ENABLED=false`.
- [ ] `ARCA_PRODUCTION_ENABLED=false`.
- [ ] No compartí clave fiscal, clave privada, certificado completo, `.env`,
      Token, Sign, contraseñas, cookies ni sesiones.

## Confirmación final

Cuando todo lo anterior esté completo, establecé localmente:

```text
READY_ARCA_HOMOLOGACION_READONLY=true
```

No pegues aquí CUIT, rutas personales ni contenido criptográfico. Informá
solamente que el checklist quedó completo y pedí ejecutar la compuerta local.
