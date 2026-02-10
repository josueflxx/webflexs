import re
import os

file_path = r'c:\Users\Brian\Desktop\webflexs\catalog\templates\catalog\catalog_v3.html'

if not os.path.exists(file_path):
    print(f"Error: File not found at {file_path}")
    exit(1)

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

print(f"Original content length: {len(content)}")

# Fix 1: Add spaces around == operator being used in if tags
# Pattern: something==something -> something == something
# We be specific to the known failing tags to avoid false positives
patterns = [
    r'active_filters\.fabrication==opt',
    r'active_filters\.diameter==opt',
    r'active_filters\.width==opt',
    r'active_filters\.length==opt',
    r'active_filters\.shape==opt',
]

fixed_count = 0
for pattern in patterns:
    regex = re.compile(pattern)
    if regex.search(content):
        print(f"Found match formed by: {pattern}")
        # Replace with spaces
        new_pattern = pattern.replace('==', ' == ')
        content = re.sub(pattern, new_pattern.replace('\\', ''), content)
        fixed_count += 1

# Generic fallback if specific ones fail (but be careful not to break other things)
# This finds text==text and makes it text == text
# content = re.sub(r'([a-zA-Z0-9_.\"\'\|]+)==([a-zA-Z0-9_.\"\'\|]+)', r'\1 == \2', content)

print(f"Fixed {fixed_count} specific patterns.")

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("File saved.")
