
from catalog.services.clamp_parser import ClampParser
import json

examples = [
    "ABRAZADERA TREFILADA DE 1/2 X 85 X 260 CURVA",
    "ABRAZADERA LAMINADA DE 3/4 X 85 X 260 PLANA",
    "ABRAZADERA TREFILADA DE 7/16 X 80 X 240 S/CURVA",
    "ABRAZADERA TREFILADA DE 1 X 100 X 300 SEMICURVA",
    "ABRAZADERA LAMINADA DE 3/4 90 X 200" # Invalid case example
]

print("--- Testing Clamp Parser ---")

for text in examples:
    print(f"\nInput: '{text}'")
    result = ClampParser.parse(text)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    
print("\n--- End Test ---")
