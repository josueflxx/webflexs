import os
import re

TARGET_DIR = r'c:\Users\Brian\Desktop\webflexs\admin_panel\templates'

def fix_file(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    original_content = content
    
    # 1. Fix missing spaces around == 
    # Left side: non-space, non-= char, followed by ==, not followed by =
    content = re.sub(r'([^\s=])==(?!=)', r'\1 == ', content)
    # Right side: == not preceded by =, followed by non-space, non-= char
    content = re.sub(r'(?<!=)==([^\s=])', r' == \1', content)
    
    # 2. Fix split tags {{ \n ... }}
    # This regex looks for {{, optional whitespace, newline, optional whitespace, content, optional whitespace, newline, optional whitespace, }}
    # And joins it.
    content = re.sub(r'\{\{\s*\n\s*(.*?)\s*\n\s*\}\}', r'{{ \1 }}', content, flags=re.DOTALL)

    # 3. Fix split {% endif %} or similar if they exist
    content = re.sub(r'(%|})\s*\n\s*(%|})', r'\1 \2', content)

    if content != original_content:
        print(f"Fixing {file_path}")
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return True
    return False

count = 0
for root, dirs, files in os.walk(TARGET_DIR):
    for file in files:
        if file.endswith('.html'):
            if fix_file(os.path.join(root, file)):
                count += 1

print(f"Fixed {count} files.")
