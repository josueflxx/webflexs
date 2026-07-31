# Runbook futuro de homologación ARCA en solo lectura

Este documento es un plan, no una autorización. No fue ejecutado durante la
preparación offline. Requiere una orden separada del usuario, credenciales de
testing custodiadas fuera del repositorio y compuerta local en `PASS`.

Nunca pegar en comandos, tickets ni chats: clave fiscal, clave privada,
certificado completo, `.env`, Token, Sign, contraseñas, cookies o sesiones.
Los únicos endpoints admitidos son los fijos de homologación. Producción y
`FECAESolicitar` permanecen bloqueados.

Los pasos 9 a 15 forman una única ejecución del probe. No ejecutar el probe más
de una vez para “probar valores” ni repetir pasos individuales.

## 1. Verificar el estado de Git

- Comando orientativo: `git status --short` y `git rev-parse HEAD`.
- Resultado esperado: HEAD conocido, staging conocido y los tres archivos
  `MM` preservados.
- Detenerse si: aparece una pérdida, un conflicto o un cambio no explicado.
- Evidencia sanitizada: hash de HEAD y cantidades por estado; no contenido.
- Reversión: ninguna operación automática; no usar `reset`, `restore` ni
  `clean`. Pedir revisión humana.

## 2. Verificar la señal del usuario

- Comando orientativo:
  `python manage.py shell -c "from django.conf import settings; print(settings.READY_ARCA_HOMOLOGACION_READONLY is True)"`.
- Resultado esperado: `True`, confirmado por el usuario después de completar
  WSASS y la configuración local.
- Detenerse si: devuelve `False`, error o un valor ambiguo.
- Evidencia sanitizada: únicamente `user_signal=true/false`.
- Reversión: establecer nuevamente la señal local en `false`.

## 3. Ejecutar el doctor offline

- Comando orientativo: `python manage.py arca_homologation_doctor`.
- Resultado esperado: `ARCA_HOMOLOGATION_DOCTOR=PASS`.
- Detenerse si: devuelve `FAIL` o `WAITING_FOR_USER`.
- Evidencia sanitizada: estado, booleanos y códigos `reason`; nunca rutas ni
  identificadores completos.
- Reversión: corregir sólo la configuración local y repetir desde el paso 1.

## 4. Ejecutar la compuerta offline

- Comando orientativo: `python manage.py arca_homologation_gate`.
- Resultado esperado: `ARCA_HOMOLOGATION_READINESS_GATE=PASS`.
- Detenerse si: aparece cualquier `FAIL` o `reason=...`.
- Evidencia sanitizada: una línea `PASS` o códigos de razón.
- Reversión: deshabilitar red/lectura/señal y corregir localmente.

## 5. Exigir PASS

- Comando orientativo: revisión humana literal del resultado del paso 4; no
  usar una excepción ni ignorar el código.
- Resultado esperado: una única línea `PASS` sin razones.
- Detenerse si: no puede demostrarse el `PASS` de la misma configuración que
  se usará en los pasos siguientes.
- Evidencia sanitizada: fecha, HEAD y `gate=PASS`.
- Reversión: volver al paso 3; nunca forzar flags desde código.

## 6. Verificar DNS

- Comando orientativo:
  `Resolve-DnsName wsaahomo.afip.gov.ar` y
  `Resolve-DnsName wswhomo.afip.gov.ar`.
- Resultado esperado: resolución de ambos hostnames oficiales, sin CNAME ni
  destino inesperado fuera del control aceptado.
- Detenerse si: falla DNS, aparece otro hostname configurado o hay duda sobre
  la resolución.
- Evidencia sanitizada: hostname consultado, estado y cantidad de respuestas;
  no cookies ni cabeceras.
- Reversión: no cambiar DNS automáticamente; deshabilitar las banderas y pedir
  revisión de red.

## 7. Verificar TLS

- Comando orientativo:
  `Test-NetConnection wsaahomo.afip.gov.ar -Port 443` y
  `Test-NetConnection wswhomo.afip.gov.ar -Port 443`.
- Resultado esperado: puerto 443 accesible; la aplicación seguirá validando
  certificado TLS y hostname.
- Detenerse si: el puerto falla, hay interceptación no aprobada o sería
  necesario desactivar TLS.
- Evidencia sanitizada: hostname, puerto y booleano de conectividad.
- Reversión: no desactivar `ARCA_TLS_VERIFY`; cerrar la prueba y revisar red.

## 8. Verificar el WSDL fijo

- Comando orientativo:
  `curl.exe --proto "=https" --tlsv1.2 --fail --silent --show-error --output NUL "https://wswhomo.afip.gov.ar/wsfev1/service.asmx?WSDL"`.
- Resultado esperado: respuesta HTTPS válida desde la URL exacta allowlisted,
  sin redirección.
- Detenerse si: cambia host, puerto, path, query, TLS o hay redirección.
- Evidencia sanitizada: URL allowlisted, código de salida y tamaño; no guardar
  el documento ni cabeceras de sesión.
- Reversión: deshabilitar flags y cerrar; no aceptar endpoints alternativos.

## 9. Obtener Ticket WSAA

- Comando orientativo, ejecutar una sola vez:
  `python manage.py arca_homologation_readonly_probe --company-id <ID_DB_CONFIRMADO> --point-of-sale-id <ID_DB_CONFIRMADO>`.
- Resultado esperado: el probe supera WSAA sin imprimir Token ni Sign.
- Detenerse si: la compuerta deja de estar en `PASS`, WSAA falla o el comando
  solicita valores fiscales por argumento.
- Evidencia sanitizada: `token_obtained=True` y `sign_obtained=True`; nunca sus
  valores.
- Reversión: el comando invalida su entrada exacta de caché; ante duda,
  deshabilitar flags y detener el backend local.

## 10. Ejecutar FEDummy

- Comando orientativo: no ejecutar otro comando; observar la misma ejecución
  iniciada en el paso 9.
- Resultado esperado: `service_status_ok=True`.
- Detenerse si: el estado no es verdadero; el probe corta antes de catálogos.
- Evidencia sanitizada: sólo el booleano del estado de servicio.
- Reversión: limpieza automática del Ticket, flags en `false` y cierre.

## 11. Consultar parámetros

- Comando orientativo: ninguno adicional; el probe usa sólo la allowlist
  interna de métodos `FEParamGet*`.
- Resultado esperado: contadores no negativos para tipos de comprobante,
  documento, IVA, monedas y conceptos.
- Detenerse si: falla un catálogo o aparece un método fuera de la allowlist.
- Evidencia sanitizada: líneas `catalog_count_*`; no respuestas SOAP completas.
- Reversión: limpieza automática del Ticket y cierre sin fallback.

## 12. Consultar puntos de venta

- Comando orientativo: ninguno adicional; se usa `FEParamGetPtosVenta` dentro
  del mismo probe.
- Resultado esperado: `catalog_count_points_of_sale` mayor que cero.
- Detenerse si: la consulta falla o devuelve una lista no interpretable.
- Evidencia sanitizada: contador y no la lista completa.
- Reversión: limpieza automática del Ticket y flags en `false`.

## 13. Confirmar el punto configurado

- Comando orientativo: ninguno adicional; el probe compara el registro local
  confirmado con el catálogo, sin aceptar un número por CLI.
- Resultado esperado: `configured_point_found=True` y salida enmascarada
  `point_of_sale=...`.
- Detenerse si: no coincide; el código no ejecuta el paso 14.
- Evidencia sanitizada: booleano y número enmascarado.
- Reversión: no probar puntos al azar; corregir sólo tras confirmación humana.

## 14. Ejecutar FECompUltimoAutorizado

- Comando orientativo: ninguno adicional; se ejecuta una vez para el tipo
  configurado y previamente confirmado.
- Resultado esperado: `last_authorized_number` entero no negativo y
  `configured_voucher_type_found=True`.
- Detenerse si: el tipo no fue confirmado, la respuesta falla o se intenta
  emitir.
- Evidencia sanitizada: tipo confirmado y último número; sin XML ni Auth.
- Reversión: limpieza automática del Ticket y cierre sin reintento aleatorio.

## 15. Eliminar Token y Sign del caché

- Comando orientativo: ninguno adicional; el `finally` del comando elimina la
  clave exacta, sin enumerar el backend.
- Resultado esperado: `ticket_cache_cleared=True`.
- Detenerse si: la limpieza falla; el comando devuelve error aunque las
  lecturas hayan sido correctas.
- Evidencia sanitizada: booleano de limpieza, nunca clave, Token ni Sign.
- Reversión: detener el backend local, conservar flags en `false` y solicitar
  limpieza administrada antes de cualquier repetición.

## 16. Revisar logs

- Comando orientativo:
  `rg -l -i "token|sign|authorization|private.?key" <DIRECTORIO_DE_LOGS_CONFIRMADO>`.
- Resultado esperado: ningún archivo con coincidencias sensibles no
  redactadas; el comando lista nombres, no contenido.
- Detenerse si: aparece una coincidencia o un volcado SOAP.
- Evidencia sanitizada: cantidad de archivos y estado de redacción.
- Reversión: aislar el log, rotarlo mediante el procedimiento operativo y no
  continuar hasta completar el análisis de exposición.

## 17. Deshabilitar nuevamente las banderas

- Comando orientativo: editar únicamente el entorno secreto local para dejar
  `ARCA_ENABLED=false`, `ARCA_HOMOLOGATION_NETWORK_ENABLED=false`,
  `ARCA_HOMOLOGATION_READ_ENABLED=false`,
  `READY_ARCA_HOMOLOGACION_READONLY=false` y
  `ARCA_WSASS_AUTHORIZATION_CONFIRMED=false`.
- Resultado esperado: todas las capacidades de red/lectura quedan cerradas;
  emisión y producción continúan en `false`.
- Detenerse si: alguna bandera no puede verificarse o se propone versionar el
  `.env`.
- Evidencia sanitizada: sólo nombres y booleanos, nunca el archivo completo.
- Reversión: no aplica; éste es el estado seguro de reposo.

## 18. Cerrar sin emitir

- Comando orientativo: `python manage.py arca_homologation_doctor` y luego
  `python manage.py arca_homologation_gate`.
- Resultado esperado: doctor `WAITING_FOR_USER` y gate `FAIL` por banderas
  deshabilitadas; `FECAESolicitar` nunca fue invocado.
- Detenerse si: el gate permanece en `PASS`, emisión está habilitada o hay una
  entrada de caché sin limpiar.
- Evidencia sanitizada: estados finales, HEAD y confirmación
  `emission_disabled=yes`.
- Reversión: forzar el estado seguro local, detener el caché de homologación y
  escalar cualquier exposición o posibilidad de emisión.
