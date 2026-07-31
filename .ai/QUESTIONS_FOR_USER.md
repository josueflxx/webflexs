# Decisiones reales pendientes para ARCA

Fecha: 2026-07-28

Este archivo excluye decisiones ya documentadas en `docs/arca/ARCA_OPEN_QUESTIONS.md`: precios de catálogo sin IVA, factura con total final con IVA, alcance inicial A/B + NCA/NCB, notas de débito fuera del primer alcance, uso previsto de consulta CUIT con revisión manual, inmutabilidad post-CAE y visibilidad general de ventas.

## Necesarias antes de WSAA

1. **Emisor de homologación:** ¿qué empresa/CUIT se usará, cuál es su condición IVA oficial y quién gestionará el certificado de homologación y la relación del CEE con `wsfe`? No incluir la clave privada en la respuesta ni en Git.
2. **Custodia de la clave:** ¿qué gestor de secretos o montaje seguro utilizará el entorno de prueba, quién podrá leerlo y cuál será el procedimiento de rotación/revocación?
3. **Infraestructura de prueba:** ¿en qué host/contenedor aislado se hará la prueba y cómo se verifican NTP, egress limitado a homologación, acceso a logs y base de datos separada?

## Necesarias antes de consultas WSFE

4. **Punto de venta:** ¿qué número está dado de alta para Web Services en homologación y para qué CUIT/tipos? Debe confirmarse fuera del repositorio y luego con `FEParamGetPtosVenta`.
5. **Datos fiscales del emisor:** falta confirmar fecha de inicio de actividades y texto legal/domicilio/ingresos brutos que deban imprimirse. El modelo actual no guarda la fecha de inicio.
6. **Receptores de prueba:** ¿qué CUIT/DNI ficticios o habilitados por homologación se usarán para Factura A, B, consumidor final y notas de crédito?

## Necesarias antes de la primera autorización

7. **Primer caso:** entre los tipos ya decididos (A, B, NCA, NCB), ¿cuál será el primero y con qué condición IVA de receptor? Recomendación técnica: el caso mínimo permitido por la matriz oficial del emisor.
8. **Consumidor final:** ¿qué umbrales y requisitos de identificación vigentes aplicará el negocio y cómo se obtendrán/actualizarán? Debe validarlo el asesor fiscal.
9. **Confirmación:** ¿la primera etapa y la operación habitual requerirán aprobación de un administrador o facturación podrá emitir directamente? El código actual permite emitir a perfiles con capacidad `issue_documents`.
10. **Notas de crédito:** ¿qué roles pueden solicitarlas, aprobarlas y emitirlas, qué motivos son obligatorios y se admite corrección parcial? No existe permiso específico.
11. **Redondeo:** ¿qué política contable se adopta para precio, descuento, base, IVA por línea/alícuota y total (modo y momento de cuantización)? Validarla con el asesor fiscal.
12. **Tolerancia:** ¿se permite alguna diferencia entre pedido y comprobante? La tolerancia actual de ARS 2,00 requiere confirmación o eliminación.
13. **Datos CUIT:** ¿qué proveedor autorizado se usará realmente para autocompletar clientes, con qué SLA/credenciales/validez y qué flujo se aplica cuando no responde? El endpoint actual sólo devuelve fallback local.
14. **Resultado incierto:** ¿quién queda de guardia, cuál es el SLA y quién autoriza desbloquear una serie cuando `FECompConsultar` no resuelve el caso?

## Documento, entrega y conservación

15. **Contenido visual:** además de los campos fiscales obligatorios, ¿qué logo, IIBB, condición de venta, textos y datos comerciales deben aparecer? ¿Qué marca exacta se usará en homologación?
16. **Entrega al cliente:** ¿descarga, correo, ambos u otro canal? Si hay correo, definir remitente, consentimiento, reintentos y evidencia de entrega.
17. **Conservación y respaldo:** ¿plazo legal/operativo para comprobantes, snapshots, requests/responses sanitizados, intentos y logs? ¿RPO/RTO, cifrado, ubicación y responsable de las restauraciones?
18. **Acceso histórico:** aunque ya se documentó que los vendedores pueden ver ventas ajenas, ¿también verán el detalle fiscal completo, intentos y errores, o esos datos quedan sólo para facturación/administración?

Estas decisiones no autorizan por sí solas ninguna conexión. Deben documentarse con fecha, responsable y aprobación fiscal/técnica cuando corresponda.
