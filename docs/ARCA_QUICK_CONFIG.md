# ARCA Quick Config (Flexs / Ubolt)

## 1) Definir `ARCA_COMPANY_CONFIG_JSON` en `.env`

Usa este formato (una sola linea JSON):

```env
ARCA_COMPANY_CONFIG_JSON={"Flexs":{"homologation":{"cuit":"20113124572","cert_path":"/etc/flexs/arca/flexs_homo.crt","key_path":"/etc/flexs/arca/flexs_homo.key"},"production":{"cuit":"20113124572","cert_path":"/etc/flexs/arca/flexs_prod.crt","key_path":"/etc/flexs/arca/flexs_prod.key"}},"ubolt":{"homologation":{"cuit":"REEMPLAZAR_CUIT_UBOLT","cert_path":"/etc/flexs/arca/ubolt_homo.crt","key_path":"/etc/flexs/arca/ubolt_homo.key"},"production":{"cuit":"REEMPLAZAR_CUIT_UBOLT","cert_path":"/etc/flexs/arca/ubolt_prod.crt","key_path":"/etc/flexs/arca/ubolt_prod.key"}}}
```

Notas:
- El sistema acepta clave por `slug` de empresa (ej. `Flexs`, `ubolt`) o por `id`.
- El matching de slug es case-insensitive.
- Si el punto de venta esta en `production`, necesitas `ARCA_ALLOW_PRODUCTION=True`.

## 2) Variables fiscales recomendadas

```env
FISCAL_RETRY_MINUTES=10
FISCAL_MAX_AUTO_RETRIES=5
FISCAL_SUBMITTING_TIMEOUT_MINUTES=20
FISCAL_ARCA_LAST_AUTH_SYNC_POLICY=first
FISCAL_ARCA_REQUIRE_LAST_AUTH_SYNC=False
FISCAL_AUTO_ITEM_TAX_ENABLED=True
FISCAL_ITEM_TAX_CALCULATION_MODE=gross
FISCAL_APPLY_TAX_TO_MANUAL_DOCS=False
```

## 3) Validacion

1. `python manage.py check`
2. Admin -> Configuracion -> Factura electronica
3. En cada punto de venta, click en `Probar ARCA`

Si falla, revisa:
- cuit en JSON
- paths de `cert_path` y `key_path`
- permisos de lectura del usuario que ejecuta gunicorn
- entorno correcto (`homologation`/`production`) del punto de venta
