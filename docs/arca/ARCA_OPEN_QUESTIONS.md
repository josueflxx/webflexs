# Preguntas abiertas para cerrar el alcance ARCA

Fecha: 24 de julio de 2026

Las respuestas ya dadas por el usuario se consideran decisiones. Las preguntas restantes deben resolverse antes de producción; no todas bloquean el inicio técnico de homologación.

## 1. Decisiones ya confirmadas

1. Las empresas deben cargarse oficialmente con ARCA aunque ya existan datos locales.
2. Los precios del catálogo son sin IVA.
3. La factura electrónica muestra precio final con IVA.
4. El usuario ingresa CUIT y el sistema completa los demás datos mediante ARCA.
5. Un conflicto o duplicado se envía a revisión manual.
6. El vendedor sirve para atribución y estadísticas; todos pueden ver ventas y la asignación puede cambiarse.
7. Los descuentos dependen de la categoría de cliente.
8. El stock es opcional por producto.
9. Un comprobante con CAE no puede modificarse.
10. Una venta bajo costo requiere observación.
11. Alcance electrónico inicial: Factura A/B y Nota de Crédito A/B.
12. Notas de débito no entran en la primera entrega.

## 2. Preguntas bloqueantes antes de homologación con credenciales

### Q1. Emisores

Por cada empresa:

- razón social legal exacta;
- CUIT emisor;
- condición IVA;
- domicilio fiscal;
- ¿ambas empresas emitirán desde el primer release?

No enviar claves ni certificados por chat o repositorio. Solo se necesita definir quién los administrará.

### Q2. Puntos de venta de homologación

- número previsto por empresa;
- si será exclusivo de Web Service;
- quién lo dará de alta/confirmará;
- si se usará un punto distinto por entorno.

### Q3. Certificados y relaciones

- ¿quién tiene acceso de Clave Fiscal para WSASS y producción?
- ¿quién generará la clave privada?
- ¿qué persona aprobará la asociación a `wsfe` y `ws_sr_constancia_inscripcion`?
- ¿se acepta el baseline seguro del VPS o se contratará un gestor de secretos?

### Q4. CUIT de prueba

- ¿qué CUIT oficial de homologación autoriza ARCA para `getPersona_v2`?
- ¿se usarán exclusivamente casos provistos por ARCA?

No utilizar clientes reales para pruebas si no es necesario.

## 3. Preguntas fiscales/contables

### Q5. Regla Factura A vs B

Propuesta:

- receptor Responsable Inscripto: A;
- consumidor final, monotributista o exento: B, sujeto a validación de la tabla ARCA vigente.

¿El contador confirma esta matriz para todas las operaciones de FLEXS?

### Q6. Condiciones IVA admitidas

El modelo local solo contempla:

- Responsable Inscripto;
- Monotributista;
- Exento;
- Consumidor Final.

¿Se deben admitir otros identificadores que devuelva `FEParamGetCondicionIvaReceptor`?

### Q7. Consumidor final

- umbrales de identificación aplicables;
- documento obligatorio;
- ventas sin CUIT;
- forma de seleccionar consumidor final genérico.

Esto debe definirse con normativa vigente al momento de implementar.

### Q8. Concepto

El código actual fija `Concepto=1` (productos). ¿Todas las facturas son exclusivamente de bienes o habrá:

- servicios;
- productos y servicios;
- fabricación/servicio a medida facturable como servicio?

### Q9. No gravado, exento y otros tributos

¿Habrá operaciones:

- no gravadas;
- exentas;
- percepciones;
- impuestos internos;
- otros tributos?

Si la respuesta inicial es no, se bloquearán explícitamente para evitar una emisión incompleta.

### Q10. Moneda

¿La primera versión será solo ARS/PES? Si se requiere moneda extranjera, hay que definir:

- moneda;
- cotización;
- `CanMisMonExt`;
- política oficial de cotización;
- representación PDF.

Propuesta: limitar primera homologación a pesos.

### Q11. Fecha y condición de venta

- ¿factura al contado o cuenta corriente?
- plazo y fecha de vencimiento;
- fecha permitida respecto del pedido/entrega;
- zona horaria oficial.

### Q12. Notas de crédito

- ¿solo total o también parciales?
- ¿pueden devolver cantidades específicas?
- ¿deben reponer stock siempre o depende del motivo?
- ¿se permiten múltiples notas contra una factura hasta el saldo?
- catálogo de motivos;
- permisos para crearlas.

### Q13. Transparencia fiscal

¿El contador aprueba el texto y desglose del PDF para la Ley 27.743 y normativa complementaria?

## 4. Preguntas comerciales y de clientes

### Q14. Aplicación de datos ARCA

Cuando ARCA difiera del dato local:

- ¿qué campos se reemplazan automáticamente?
- ¿cuáles requieren confirmación?
- ¿se conserva un domicilio de entrega distinto del fiscal?
- ¿quién resuelve conflicto?

Propuesta: nunca sobrescribir automáticamente; mostrar comparación y confirmar.

### Q15. Vigencia de consulta

¿Cuánto dura un snapshot padronal antes de exigir actualización?

Propuesta:

- caché interactiva: 24 horas;
- revalidación para facturar: 30 días como máximo o ante conflicto;
- botón de consulta forzada.

El contador debe confirmar si la condición IVA requiere una ventana menor.

### Q16. Persona humana

Para persona humana:

- ¿nombre comercial separado?
- ¿razón social visible como apellido + nombre?
- ¿qué campos son editables?

### Q17. Duplicados

La unicidad actual se evalúa dentro de la empresa. ¿El mismo cliente/CUIT puede existir como perfil único compartido entre FLEXS y U-bolt con reglas comerciales separadas?

Propuesta: identidad única global de cliente y relación `ClientCompany` por empresa.

## 5. Preguntas operativas

### Q18. SLA de emisión

- tiempo máximo aceptable;
- ¿la venta puede continuar si ARCA está caído?
- ¿se permite dejar factura en cola?
- ¿quién ve y resuelve `uncertain`?

Propuesta: permitir pedido/entrega según política comercial, pero no presentar como factura autorizada hasta CAE.

### Q19. Horarios y soporte

- responsables ante errores;
- horario de operación;
- contacto contable;
- contacto técnico;
- criterio para detener emisiones.

### Q20. PDF y entrega

- formato A4;
- logo y datos legales;
- envío por email;
- descarga por cliente;
- copias;
- archivo histórico;
- textos legales finales.

### Q21. Reportes

- Libro IVA/contabilidad;
- exportación necesaria;
- conciliación con contador;
- indicadores por vendedor;
- tratamiento de notas de crédito.

## 6. Preguntas de seguridad

### Q22. Gestor de secretos

Elegir:

1. gestor administrado;
2. baseline seguro de archivos en VPS como etapa transitoria.

### Q23. MFA y doble aprobación

- proveedor de MFA;
- usuarios con configuración fiscal;
- quiénes aprueban producción;
- recuperación de cuenta.

### Q24. Retención

Definir con asesor:

- comprobantes;
- respuestas de padrón;
- logs;
- auditoría;
- backups;
- datos de clientes inactivos.

### Q25. Acceso de soporte

¿Soporte puede ver CUIT completo, domicilio y payload fiscal, o solo códigos redactados?

Propuesta: mínimo privilegio y elevación temporal auditada.

## 7. Preguntas de despliegue

### Q26. Infraestructura

- ¿se mantiene el VPS único?
- ¿Redis y PostgreSQL siguen locales?
- ¿hay staging separado?
- ¿se creará un worker exclusivo para fiscal?

Propuesta mínima: staging/homologación separado lógicamente y worker fiscal con cola propia.

### Q27. Ventana de mantenimiento

- horario de migración;
- duración;
- plan de rollback;
- responsable de validación.

### Q28. Backups

- frecuencia;
- retención;
- ubicación cifrada;
- quién puede restaurar;
- fecha de último restore test.

## 8. Decisiones técnicas que no requieren pregunta

Se adoptan salvo objeción:

- backend-only;
- un comprobante por `FECAESolicitar`;
- request canónico con hash;
- estado `uncertain`;
- reconciliación antes de reenvío;
- locks PostgreSQL + Redis;
- producción deshabilitada por defecto;
- PDF/QR desde snapshot;
- fallar cerrado ante alícuota o condición desconocida;
- parámetros ARCA cacheados pero refrescables;
- respuestas y logs redactados;
- no guardar secretos en DB/repo.

## 9. Orden sugerido para responder

Para comenzar homologación técnica bastan Q1 a Q4 sin compartir secretos. Antes de diseñar casos fiscales finales: Q5 a Q13. Antes de producción: todas las preguntas de seguridad, operación y despliegue.
