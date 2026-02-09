import re

file_path = r'c:\Users\Brian\Desktop\webflexs\catalog\templates\catalog\catalog_v3.html'

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

print(f"Read {len(content)} bytes from {file_path}")

# Step 1: Fix all == spacing issues (add spaces around ==)
content = re.sub(r'([^\s])==([^\s])', r'\1 == \2', content)
print("Applied == spacing fix.")

# Step 2: Fix split {% endif %} tags
content = re.sub(r'\{%\s+endif\s+%\}', r'{% endif %}', content)
print("Applied split tag fix.")

# Step 3: Fix split {{ opt }} tags
# Pattern: >{{ followed by newline and whitespace and opt }}
content = re.sub(r'>{{\s*\n\s*opt\s*}}', r'>{{ opt }}', content)
# Pattern: {{ opt followed by newline and }}
content = re.sub(r'{{\s*opt\s*\n\s*}}', r'{{ opt }}', content)
print("Applied split {{ opt }} fix.")

# Step 4: Ensure all filter options display correctly
# Fix any remaining "selected{%" patterns
content = re.sub(r'selected\{%\s+endif\s+%\}', r'selected{% endif %}', content)
print("Applied selected tag fix.")

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print(f"Fixed {file_path} successfully.")
