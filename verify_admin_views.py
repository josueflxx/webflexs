
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "flexs_project.settings.local")
django.setup()

from django.test import Client
from django.contrib.auth.models import User
from django.urls import reverse
from catalog.models import Category, CategoryAttribute

def run_test():
    print("--- Verifying Admin Attribute Views ---")
    
    # 1. Setup Admin User
    password = 'password123'
    username = 'admin_tester'
    if not User.objects.filter(username=username).exists():
        user = User.objects.create_superuser(username, 'admin@test.com', password)
    else:
        user = User.objects.get(username=username)
        
    client = Client()
    client.force_login(user)
    
    # 2. Setup Category
    cat, _ = Category.objects.get_or_create(name="Test Admin Attr", slug="test-admin-attr")
    print(f"Category created: {cat.pk}")
    
    # 3. Test Create Attribute
    url_create = reverse('admin_category_attribute_create', args=[cat.pk])
    print(f"Testing URL: {url_create}")
    
    response = client.post(url_create, {
        'name': 'Material Test',
        'slug': 'material-test',
        'type': 'text',
        'required': 'on',
        'regex_pattern': r'Material:\s*(\w+)'
    })
    
    if response.status_code == 302:
        print("[OK] Create Attribute Redirected (Success)")
    else:
        print(f"[FAIL] Create Attribute Status: {response.status_code}")
        print(response.content.decode())
        
    # Verify DB
    attr = CategoryAttribute.objects.filter(slug='material-test', category=cat).first()
    if attr:
        print(f"[OK] Attribute created in DB: {attr.name}")
    else:
        print("[FAIL] Attribute NOT found in DB")
        return

    # 4. Test Edit Attribute
    url_edit = reverse('admin_category_attribute_edit', args=[cat.pk, attr.pk])
    response = client.post(url_edit, {
        'name': 'Material Test Edited',
        'slug': 'material-test', # Keep slug
        'type': 'text'
    })
    
    attr.refresh_from_db()
    if attr.name == 'Material Test Edited':
        print("[OK] Attribute Edited Successfully")
    else:
        print(f"[FAIL] Attribute Name Mismatch: {attr.name}")
        
    # 5. Test Delete
    url_delete = reverse('admin_category_attribute_delete', args=[cat.pk, attr.pk])
    response = client.get(url_delete) # View uses GET for delete (simple link) or redirection logic? 
    # Logic in view was: no confirm method check, just delete. (Usually GET if linked directly)
    
    if response.status_code == 302:
        print("[OK] Delete Redirected")
    else:
        print(f"[FAIL] Delete Status: {response.status_code}")
        
    if not CategoryAttribute.objects.filter(pk=attr.pk).exists():
        print("[OK] Attribute Deleted from DB")
    else:
        print("[FAIL] Attribute still exists in DB")

if __name__ == "__main__":
    run_test()
