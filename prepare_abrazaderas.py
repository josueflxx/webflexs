
import pandas as pd
import os

file_path = r"c:\Users\Brian\Desktop\webflexs\abrazadera.xlsx"
output_path = r"c:\Users\Brian\Desktop\webflexs\abrazaderas_normalized.xlsx"

print(f"Reading: {file_path}")

try:
    df = pd.read_excel(file_path)
    
    # 1. Rename Columns
    # Map: CODIGO -> sku, DESCRIPCION -> nombre, PRECIO -> precio
    rename_map = {
        'CODIGO': 'sku',
        'DESCRIPCION': 'nombre',
        'PRECIO': 'precio'
    }
    df.rename(columns=rename_map, inplace=True)
    
    # 2. Add Missing Columns
    if 'categoria' not in df.columns:
        print("Adding 'categoria' = 'Abrazaderas'")
        df['categoria'] = 'Abrazaderas'
        
    if 'stock' not in df.columns:
        print("Adding 'stock' = 1000")
        df['stock'] = 1000
    
    # 3. Clean Data
    # Ensure no empty rows
    df.dropna(subset=['sku', 'nombre'], inplace=True)
    
    # 4. Save
    df.to_excel(output_path, index=False)
    print(f"\nSUCCESS! Created normalized file at: {output_path}")
    print("Columns:", list(df.columns))
    
except Exception as e:
    print(f"Error: {e}")
