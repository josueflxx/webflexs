import os
import sys
import django
import urllib.request
from pathlib import Path

# Dynamic path setup relative to the script's location
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR))

# Use the environment settings module, defaulting to local if not set
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'flexs_project.settings.local')
django.setup()

from django.conf import settings
from catalog.models import Brand

# Define brands and their domains for logo downloads
BRANDS_DATA = [
    {"name": "Ford", "domain": "ford.com"},
    {"name": "Mercedes-Benz", "domain": "mercedes-benz.com"},
    {"name": "Fiat", "domain": "fiat.com"},
    {"name": "Scania", "domain": "scania.com"},
    {"name": "Volvo", "domain": "volvo.com"},
    {"name": "Chevrolet", "domain": "chevrolet.com"},
    {"name": "Volkswagen", "domain": "volkswagen.com"},
    {"name": "Toyota", "domain": "toyota.com"},
    {"name": "Iveco", "domain": "iveco.com"},
    {"name": "Dodge", "domain": "dodge.com"},
    {"name": "Agrale", "domain": "agrale.com.br"},
    {"name": "Peugeot", "domain": "peugeot.com"},
    {"name": "Renault", "domain": "renault.com"}
]

# Get media path dynamically from Django settings
media_dir = Path(settings.MEDIA_ROOT) / 'brands' / 'logos'
media_dir.mkdir(parents=True, exist_ok=True)

print("Starting brand logo download from internet...")

for b_info in BRANDS_DATA:
    name = b_info["name"]
    domain = b_info["domain"]
    slug = name.lower().replace(" ", "-")
    logo_filename = f"{slug}.png"
    logo_path = media_dir / logo_filename
    
    # Try different sources
    urls_to_try = [
        f"https://logos.hunter.io/{domain}",
        f"https://api.companyenrich.com/logo/{domain}"
    ]
    
    downloaded = False
    for logo_url in urls_to_try:
        print(f"Downloading logo for {name} from {logo_url}...")
        try:
            req = urllib.request.Request(
                logo_url, 
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
            )
            with urllib.request.urlopen(req, timeout=12) as response:
                content = response.read()
                # Simple check if response looks like a valid image (has length and is not an HTML error page)
                if len(content) > 100 and b"<html" not in content.lower()[:200]:
                    with open(logo_path, 'wb') as out_file:
                        out_file.write(content)
                    print(f"  Success: Saved to {logo_path}")
                    downloaded = True
                    break
                else:
                    print(f"  Invalid content length or structure from {logo_url}")
        except Exception as e:
            print(f"  Failed from {logo_url}: {e}")
            
    if downloaded:
        db_logo_path = f"brands/logos/{logo_filename}"
    else:
        print(f"  WARNING: Could not download logo from any source for {name}")
        db_logo_path = None

    # Get or update Brand in DB
    brand, created = Brand.objects.get_or_create(
        name=name,
        defaults={
            "is_active": True,
            "order": 10
        }
    )
    if db_logo_path:
        brand.logo = db_logo_path
        brand.save()
        print(f"  Updated DB entry for '{name}' with logo: {brand.logo}")
    else:
        print(f"  Kept existing logo for '{name}' since download failed.")

print("\nFinished populating brands from internet!")
