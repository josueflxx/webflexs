# Logica de Codificacion de Abrazaderas (ABL/ABT)

## 1) Estructura general
Formato:

`[PREFIJO][BLOQUE_NUMERICO][FORMA]`

- `PREFIJO`:
  - `ABL` -> Abrazadera Laminada
  - `ABT` -> Abrazadera Trefilada
- `FORMA` (ultima letra):
  - `C` -> Curva
  - `P` -> Plana
  - `S` -> Semicurva
- `BLOQUE_NUMERICO`:
  - `MEDIDA_COMPACTADA + ANCHO + LARGO`

Ejemplos:
- `ABL1135400C` -> `ABL` + `1` + `135` + `400` + `C`
- `ABT91685270P` -> `ABT` + `916` + `85` + `270` + `P`
- `ABT3480220S` -> `ABT` + `34` + `80` + `220` + `S`

## 2) Medida compactada (diametro/medida principal)

La medida compactada no tiene longitud fija.

Mapeos validados por defecto:
- `3/4` -> `34`
- `9/16` -> `916`
- `11/16` -> `1116`
- `7/16` -> `716`
- `1/2` -> `12`
- `1` -> `1`

Tambien se incluyen equivalencias enteras usadas en el sistema (`18`, `20`, `22`, `24`).

Si aparece una medida no mapeada (ej. `1 1/8`), el sistema:
- la compacta removiendo simbolos (`1 1/8` -> `118`), y
- marca `diametro_requiere_mapeo = true` para estandarizarla despues.

## 3) Parseo robusto (codigo -> atributos)

El parser usa estrategia hibrida:
1. Lee prefijo (`ABL`/`ABT`).
2. Separa la ultima letra como forma (`C/P/S`).
3. Del bloque numerico, prueba cortes posibles para `ANCHO` (2 o 3 digitos) y `LARGO` (preferencia 3 digitos).
4. Evalua candidatos por:
   - diametro compactado conocido (tabla)
   - anchos/largos plausibles
   - coincidencia con catalogos conocidos (si se pasan `known_widths`/`known_lengths`)
5. El resto inicial queda como medida compactada.

Si hay empate de score, devuelve warning de parseo ambiguo.

## 4) Generacion inversa (atributos -> codigo)

Entrada:
- tipo (`ABL`/`ABT` o `LAMINADA`/`TREFILADA`)
- diametro/medida (string humano)
- ancho (int)
- largo (int)
- forma (`C/P/S` o `CURVA/PLANA/SEMICURVA`)

Proceso:
1. Normaliza tipo y forma.
2. Convierte medida humana a compactada:
   - primero con tabla de mapeo
   - si no existe, compacta removiendo simbolos
3. Concatena: `PREFIJO + MEDIDA_COMPACTADA + ANCHO + LARGO + FORMA`

## 5) API interna implementada

Archivo: `catalog/services/clamp_code.py`

Funciones principales:
- `parsearCodigo(codigo, known_widths=None, known_lengths=None, diameter_compact_to_human=None)`
- `generarCodigo(tipo, diametro, ancho, largo, forma, human_to_compact=None, strict_diameter_mapping=False, with_metadata=False)`

Alias snake_case:
- `parsear_codigo(...)`
- `generar_codigo(...)`

## 6) Integracion con cotizador

El cotizador usa `generarCodigo(...)` para sugerir codigo automaticamente en base a:
- tipo abrazadera
- diametro
- ancho
- largo
- forma

Si el diametro requiere estandarizacion, el resultado incluye warning y bandera de mapeo.
