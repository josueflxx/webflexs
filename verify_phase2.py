
import os
import django
import json

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "flexs_project.settings.local")
django.setup()

from catalog.models import Category, CategoryAttribute, Product

def run_verification():
    print("--- Verifying Phase 2 ---")
    
    # 1. Setup Data
    cat, _ = Category.objects.get_or_create(name="Test Abrazaderas", slug="test-abrazaderas")
    
    # Create Attribute with Regex
    # Regex: Look for "Diametro: <value>"
    attr, created = CategoryAttribute.objects.get_or_create(
        category=cat,
        slug="diametro",
        defaults={
            'name': "Diámetro", 
            'type': "text",
            'regex_pattern': r"Di[áa]metro:\s*([\d/]+(?:mm)?)"
        }
    )
    if not created:
        attr.regex_pattern = r"Di[áa]metro:\s*([\d/]+(?:mm)?)"
        attr.save()
        
    print(f"Category: {cat.name}")
    print(f"Attribute: {attr.name} (Regex: {attr.regex_pattern})")
    
    # 2. Test Parser Logic
    description = "Abrazadera de alta calidad. Diámetro: 10mm. Fabricada en acero."
    
    p = Product(description=description, category=cat)
    extracted = p.extract_attributes_from_description()
    
    print(f"\nDescription: {description}")
    print(f"Extracted Attributes: {extracted}")
    
    if extracted.get('diametro') == '10mm':
        print("[OK] SUCCESS: Attribute extracted correctly.")
    else:
        print(f"[FAIL] FAILED: Expected '10mm', got '{extracted.get('diametro')}'")
        
    # 3. Test Saving Product with Attributes
    p.sku = "TEST-PARSE-001"
    p.name = "Test Product Parser"
    p.price = 100
    p.stock = 10
    p.attributes = extracted
    
    # Clean up previous run
    Product.objects.filter(sku="TEST-PARSE-001").delete()
    
    p.save()
    
    # Reload
    p_refetched = Product.objects.get(sku="TEST-PARSE-001")
    print(f"Saved Attributes: {p_refetched.attributes}")
    
    if p_refetched.attributes.get('diametro') == '10mm':
        print("[OK] SUCCESS: Attributes saved and retrieved correctly.")
    else:
        print("[FAIL] FAILED: Saved attributes mismatch.")

if __name__ == "__main__":
    run_verification()
