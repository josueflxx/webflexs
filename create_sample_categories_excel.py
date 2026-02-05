
import pandas as pd
import os

# Create a sample categories dataframe
data = {
    'nombre': ['Herramientas', 'Manuales', 'Eléctricas', 'Hogar', 'Decoración'],
    'padre': ['', 'Herramientas', 'Herramientas', '', 'Hogar'], # Hierarchy
    'activo': ['si', 'si', 'si', 'si', 'no']
}
# Expected: 
# 1. Herramientas created (root)
# 2. Manuales created (child of Herramientas)
# 3. Eléctricas created (child of Herramientas)
# 4. Hogar created (root)
# 5. Decoración created (child of Hogar)

df = pd.DataFrame(data)

# Save to excel
file_path = r"c:\Users\Brian\Desktop\webflexs\sample_categories.xlsx"
df.to_excel(file_path, index=False)
print(f"Sample Categories Excel created at: {file_path}")
