# Confirmacion manual del flujo comercial

Esta guia deja un caso demo repetible para validar el circuito central sin tocar datos reales importantes.

## Preparar demo

Ejecutar:

```bash
python manage.py seed_demo_sales_flow --actor josue
```

Si el usuario staff tiene otro nombre, cambiar `josue` por el username correcto. Si no se pasa `--actor`, el comando usa el primer staff activo.

## Recorrido

1. Abrir `/admin-panel/solicitudes/` y entrar a la solicitud demo.
2. Confirmar que la solicitud diga que viene del catalogo o portal, y que no sea tratada como pedido operativo hasta convertirse.
3. Abrir `/admin-panel/pedidos/` y entrar al pedido demo.
4. Confirmar que la pantalla use formato de ficha de venta: datos basicos, cliente, productos, observaciones y totales.
5. Abrir `/admin-panel/fiscal/documentos/` y revisar la factura demo.
6. Confirmar que la factura tenga cliente, productos, totales e impresion habilitada solo si el movimiento esta cerrado.
7. Abrir `/admin-panel/pagos/` y confirmar que el cobro demo exista y este activo.
8. Abrir la ficha del cliente demo y revisar cuenta corriente.

## Resultado esperado

- Solicitud web visible y trazable.
- Pedido operativo conectado a la solicitud.
- Remito interno generado cuando corresponde.
- Factura manual fiscal generada y cerrada.
- Pago registrado y movimiento cerrado.
- Saldo final del cliente demo en cero si factura y cobro tienen el mismo total.

## Uso recomendado

Ejecutar este comando antes de pasar cambios grandes al host o antes de probar ARCA en homologacion.
