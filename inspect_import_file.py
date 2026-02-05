
import pandas as pd
import os
import sys

# Add project root to path
sys.path.append(os.getcwd())
# Setup Django
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'flexs_project.settings')
django.setup()

from catalog.services.clamp_parser import ClampParser

file_path = r"c:\Users\Brian\Desktop\webflexs\abrazaderas_normalized.xlsx"

print(f"--- INSPECTING: {file_path} ---")

try:
    df = pd.read_excel(file_path)
    print(f"COLUMNS FOUND: {list(df.columns)}")
    
    required = ['sku', 'nombre', 'precio']
    missing = [c for c in required if c not in df.columns]
    
    if missing:
        print(f"\n[CRITICAL ERROR] Missing required columns: {missing}")
    else:
        print("\n[OK] Required columns present.")

    # Check for description or name for parsing
    parse_source = 'descripcion' if 'descripcion' in df.columns else 'nombre'
    print(f"\nParsing Source Column: {parse_source}")
    
    print("\n--- SIMULATING PARSER ON FAST 5 ROWS ---")
    
    for i, row in df.head(5).iterrows():
        text = str(row.get(parse_source, ''))
        print(f"\nRow {i+1} Text: '{text}'")
        if not text or text == 'nan':
            print("  Skipping (empty)")
            continue
            
        result = ClampParser.parse(text)
        # Format for readability
        compact = {k:v for k,v in result.items() if v is not None and k not in ['parse_warnings', 'parse_confidence']}
        print(f"  -> Extracted: {compact}")
        if result['parse_warnings']:
            print(f"  -> Warnings: {result['parse_warnings']}")
        print(f"  -> Confidence: {result['parse_confidence']}%")

except Exception as e:
    print(f"\n[ERROR] Could not read file: {e}")
