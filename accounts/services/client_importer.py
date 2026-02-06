
from core.services.importer import BaseImporter, ImportRowResult
from django.contrib.auth.models import User
from accounts.models import ClientProfile
from django.db import transaction
from django.utils.text import slugify

class ClientImporter(BaseImporter):
    """
    Importer for Clients via Excel.
    Expected Columns (Flexible):
      - Usuario (username)
      - Contraseña (password)
      - Nombre (company_name/razón social)
      - Email
      - CUIT/DNI
      - Tipo de cliente
      - Cond. IVA
      - Descuento
      - Provincia, Domicilio, Telefonos, Contacto
    """
    
    def __init__(self, file):
        super().__init__(file)
        # Required internal keys (after normalization)
        # We will map Excel headers to these in process_row
        self.required_columns = [] # validation done dynamically inside process for aliases

    def _normalize_header(self, header):
        """Helper to normalize excel headers keys."""
        h = str(header).lower().strip()
        # Common aliases
        if h in ['usuario', 'user', 'username']: return 'username'
        if h in ['contraseña', 'password', 'pass', 'clave']: return 'password'
        if h in ['nombre', 'razón social', 'razon social', 'empresa']: return 'company_name'
        if h in ['email', 'correo', 'mail']: return 'email'
        if h in ['cuit/dni', 'cuit', 'dni', 'identificacion']: return 'cuit_dni'
        if h in ['tipo de cliente', 'tipo', 'rubro']: return 'client_type'
        if h in ['cond. iva', 'cond iva', 'iva', 'condicion de iva']: return 'iva_condition'
        if h in ['descuento', 'desc', 'desc.']: return 'discount'
        if h in ['telefonos', 'telefono', 'tel', 'celular']: return 'phone'
        if h in ['domicilio', 'direccion', 'calle']: return 'address'
        if h in ['provincia', 'estado']: return 'province'
        if h in ['contacto', 'persona de contacto']: return 'contact_name'
        return h

    def process_row(self, row, dry_run=True):
        # 0. Pre-process row keys to normalized names
        data = {self._normalize_header(k): v for k, v in row.items()}
        
        result = ImportRowResult(row_number=0, data=row)
        errors = []
        
        # 1. Extraction & Validation
        username = str(data.get('username', '')).strip()
        company_name = str(data.get('company_name', '')).strip()
        
        # Fallback: if no username, try to generate one from company or email, or fail?
        # User requirement: "Usuario debe ser único. Si viene repetido... registrar error"
        # So username is mandatory.
        if not username:
             # Try email as fallback
             username = str(data.get('email', '')).strip()
        
        if not username:
            errors.append("Falta campo 'Usuario' (u 'Email' como fallback).")

        if not company_name:
            errors.append("Falta campo 'Nombre' (Razón Social).")
            
        if errors:
            result.success = False
            result.errors = errors
            result.action = "error"
            return result

        # Optional fields
        password = str(data.get('password', '')).strip()
        email = str(data.get('email', '')).strip()
        cuit = str(data.get('cuit_dni', '')).strip()
        phone = str(data.get('phone', '')).strip()
        address = str(data.get('address', '')).strip()
        province = str(data.get('province', '')).strip()
        contact_name = str(data.get('contact_name', '')).strip()
        
        # Discount logic
        discount_val = 0.0
        try:
            d_raw = data.get('discount', 0)
            if pd.isna(d_raw) or d_raw == '': d_raw = 0
            discount_val = float(d_raw)
        except:
            discount_val = 0.0
            
        # Mappings
        # IVA
        iva_raw = str(data.get('iva_condition', '')).lower()
        iva_map = {
            'responsable inscripto': 'responsable_inscripto',
            'ri': 'responsable_inscripto',
            'monotributista': 'monotributista',
            'exento': 'exento',
            'consumidor final': 'consumidor_final',
            'cf': 'consumidor_final'
        }
        iva_choice = iva_map.get(iva_raw, 'consumidor_final') # Default
        
        # Client Type
        type_raw = str(data.get('client_type', '')).lower()
        type_map = {
            'taller': 'taller',
            'distribuidora': 'distribuidora',
            'flota': 'flota',
            'otro': 'otro'
        }
        client_type_choice = type_map.get(type_raw, 'otro')

        # Notes
        notes_parts = []
        if contact_name:
            notes_parts.append(f"Contacto: {contact_name}")
        # Add N° reference if exists
        n_ref = row.get('N°') or row.get('n°')
        if n_ref:
            notes_parts.append(f"Ref Excel: {n_ref}")
            
        notes = " | ".join(notes_parts)

        # 2. Logic (Update or Create)
        user_exists = User.objects.filter(username=username).exists()
        
        if dry_run:
            result.success = True
            if user_exists:
                result.action = "updated" 
            else:
                result.action = "created"
            return result
            
        # Actual DB Operation with Atomic Transaction
        try:
            with transaction.atomic():
                user = None
                if user_exists:
                    user = User.objects.get(username=username)
                    # Update password if provided
                    if password and password.lower() != 'nan':
                        user.set_password(password)
                    
                    # Update email if provided
                    if email:
                        user.email = email
                    
                    user.save()
                    result.action = "updated"
                else:
                    # Create User
                    # If password empty, set random or CUIT
                    final_pass = password if (password and password.lower() != 'nan') else (cuit or 'Flexs123')
                    user = User.objects.create_user(
                        username=username,
                        email=email,
                        password=final_pass
                    )
                    result.action = "created"
                
                # Check Profile
                profile, created = ClientProfile.objects.get_or_create(user=user)
                
                # Update Profile Fields
                profile.company_name = company_name
                if cuit: profile.cuit_dni = cuit
                if phone: profile.phone = phone
                if address: profile.address = address
                if province: profile.province = province
                profile.discount = discount_val
                profile.iva_condition = iva_choice
                profile.client_type = client_type_choice
                
                if notes:
                    if profile.notes:
                        profile.notes += f"\nImport: {notes}"
                    else:
                        profile.notes = notes
                
                profile.save()
                
            result.success = True
            
        except Exception as e:
            result.success = False
            result.errors.append(str(e))
            result.action = "error"

        return result
