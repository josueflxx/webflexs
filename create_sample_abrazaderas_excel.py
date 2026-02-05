
import pandas as pd
import os

# Create sample abrazaderas dataframe using the user's examples
data = {
    'sku': ['ABR-001', 'ABR-002', 'ABR-003', 'ABR-004', 'ABR-ERR'],
    'nombre': [
        'ABRAZADERA TREFILADA DE 1/2 X 85 X 260 CURVA',
        'ABRAZADERA LAMINADA DE 3/4 X 85 X 260 PLANA',
        'ABRAZADERA TREFILADA DE 7/16 X 80 X 240 S/CURVA',
        'ABRAZADERA TREFILADA DE 1 X 100 X 300 SEMICURVA',
        'ABRAZADERA LAMINADA DE 3/4 90 X 200' # Error case
    ],
    'precio': [100, 150, 120, 200, 100],
    'stock': [50, 50, 50, 50, 50],
    'categoria': ['Abrazaderas', 'Abrazaderas', 'Abrazaderas', 'Abrazaderas', 'Abrazaderas'] # Triggers parser
}

df = pd.DataFrame(data)

# Save to excel
file_path = r"c:\Users\Brian\Desktop\webflexs\sample_abrazaderas_batch.xlsx"
df.to_excel(file_path, index=False)
print(f"Sample Abrazaderas Excel created at: {file_path}")
