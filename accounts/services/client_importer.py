
from core.services.importer import BaseImporter, ImportRowResult
from django.contrib.auth.models import User
from accounts.models import ClientProfile
from django.utils.text import slugify

class ClientImporter(BaseImporter):
    """
    Importer for Clients.
    Required Headers: cuit_dni, company_name, email
    Optional: phone, address, province, discount
    """
    
    def __init__(self, file):
        super().__init__(file)
        self.required_columns = ['cuit_dni', 'company_name', 'email']

    def process_row(self, row, dry_run=True):
        result = ImportRowResult(row_number=0, data=row)
        errors = []
        
        # 1. Validation
        cuit = str(row.get('cuit_dni', '')).strip()
        if not cuit:
            errors.append("CUIT/DNI es requerido")
            
        company_name = str(row.get('company_name', '')).strip()
        if not company_name:
            errors.append("Razón Social (company_name) es requerida")
            
        email = str(row.get('email', '')).strip()
        if not email:
            errors.append("Email es requerido")

        if errors:
            result.success = False
            result.errors = errors
            result.action = "error"
            return result

        # 2. Logic (Update or Create)
        # Check by CUIT or Email
        client_exists = ClientProfile.objects.filter(cuit_dni=cuit).exists()
        user_exists = User.objects.filter(email=email).exists()
        
        if dry_run:
            result.success = True
            if client_exists:
                result.action = "updated" 
            elif user_exists:
                result.action = "error"
                result.errors.append(f"El email {email} ya existe pero no coincide con el CUIT (posible conflicto)")
                result.success = False
            else:
                result.action = "created"
            return result
            
        # Actual DB Operation
        try:
            # Updating existing client
            if client_exists:
                client = ClientProfile.objects.get(cuit_dni=cuit)
                client.company_name = company_name
                client.phone = str(row.get('phone', client.phone or '')).strip()
                client.address = str(row.get('address', client.address or '')).strip()
                client.province = str(row.get('province', client.province or '')).strip()
                
                # discount should be float
                try:
                    disc = float(row.get('discount', 0))
                    if disc > 0:
                        client.discount = disc
                except:
                    pass # Keep existing if invalid
                
                client.save()
                result.action = "updated"
                
            else:
                # Create NEW User and Client
                if user_exists:
                     # Edge case: User email exists but not bound to this CUIT? 
                     # For safety, let's skip or error.
                     raise ValueError(f"El usuario con email {email} ya existe pero no es un cliente registrado.")
                
                # Username strategy: email is safe
                user = User.objects.create_user(
                    username=email,
                    email=email,
                    password=cuit, # Default password = CUIT
                    first_name=company_name[:30]
                )
                
                ClientProfile.objects.create(
                    user=user,
                    company_name=company_name,
                    cuit_dni=cuit,
                    email=email, # Redundant but in model
                    phone=str(row.get('phone', '')).strip(),
                    address=str(row.get('address', '')).strip(),
                    province=str(row.get('province', '')).strip(),
                    discount=float(row.get('discount', 0) or 0)
                )
                
                result.action = "created"

            result.success = True
            
        except Exception as e:
            result.success = False
            result.errors.append(str(e))
            result.action = "error"

        return result
