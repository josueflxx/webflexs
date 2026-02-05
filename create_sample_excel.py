
import pandas as pd
import os

# Create a sample products dataframe
data = {
    'SKU': ['TEST001', 'TEST002', 'TEST003', 'TEST-FAIL'],
    'Nombre': ['Producto Test 1', 'Producto Test 2', 'Producto Test 3', ''], # Empty name for failure
    'Precio': [100.50, 200.00, 350.00, 0],
    'Stock': [10, 5, 20, 0],
    'Categoria': ['Prueba', 'Herramientas', 'NewCategory', ''],
    'Descripcion': ['Desc 1', 'Desc 2', 'Desc 3', ''],
    'Atributos': ['Color:Rojo;Material:Acero', '', 'Peso:1kg', '']
}

df = pd.DataFrame(data)

# Save to excel
file_path = r"c:\Users\Brian\Desktop\webflexs\sample_products.xlsx"
df.to_excel(file_path, index=False)
print(f"Sample Excel created at: {file_path}")
