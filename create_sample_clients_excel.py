
import pandas as pd
import os

# Create a sample clients dataframe
data = {
    'cuit_dni': ['20123456789', '30998877665', '27555555555', ''],
    'company_name': ['Cliente Nuevo SRL', 'Empresa Test SA', 'Ferretería El Tornillo', 'Sin Nombre'],
    'email': ['nuevo@cliente.com', 'contacto@testsa.com', 'admin@ferreteria.com', 'error@mail.com'],
    'phone': ['1144445555', '1122334455', '', ''],
    'address': ['Calle Falsa 123', 'Av. Siempre Viva 742', '', ''],
    'province': ['CABA', 'Buenos Aires', '', ''],
    'discount': [10.5, 0, 5, 0]
}

df = pd.DataFrame(data)

# Save to excel
file_path = r"c:\Users\Brian\Desktop\webflexs\sample_clients.xlsx"
df.to_excel(file_path, index=False)
print(f"Sample Clients Excel created at: {file_path}")
