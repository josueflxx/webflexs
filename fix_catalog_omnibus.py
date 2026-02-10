import re
import os

file_path = r'c:\Users\Brian\Desktop\webflexs\catalog\templates\catalog\catalog_v3.html'

if not os.path.exists(file_path):
    print(f"Error: File not found at {file_path}")
    exit(1)

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

print(f"Original content length: {len(content)}")

# --- DEFINITIVE FIXES ---

# 1. Fix newlines inside {{ opt }} tags
# Pattern: {{ followed by whitespace/newline followed by opt followed by whitespace/newline followed by }}
# We want: {{ opt }}
content = re.sub(r'\{\{\s+opt\s+\}\}', '{{ opt }}', content)

# 2. Fix active_filters.field==opt usage
# We specifically target the known failing fields to avoid false positives
fields = ['fabrication', 'diameter', 'width', 'length', 'shape']
for field in fields:
    # Regex to find: active_filters.field==opt (allowing for potential spaces that might be there or not)
    # We replace with: active_filters.field == opt
    pattern = fk = f'active_filters.{field}'
    # This regex looks for the field, followed by == (with optional surrounding space), followed by opt
    regex = re.compile(re.escape(fk) + r'\s*==\s*opt')
    content = regex.sub(f'{fk} == opt', content)
    
    # Also handle the |stringformat:"s" cases for width/length
    # active_filters.width==opt|stringformat:"s"
    regex_str = re.compile(re.escape(fk) + r'\s*==\s*opt\|stringformat:"s"')
    content = regex_str.sub(f'{fk} == opt|stringformat:"s"', content)

# 3. Fix current_val==opt case (and generic == check)
# Pattern: current_val==opt
content = re.sub(r'current_val\s*==\s*opt', 'current_val == opt', content)

# GENERIC FIX: active_filters or order_by or anything else with ==
# We use lookahead/lookbehind to ensure we don't break === comparison in JS
# 1. Fix left side: non-space non-= char, followed by ==, not followed by =
content = re.sub(r'([^\s=])==(?!=)', r'\1 == ', content)
# 2. Fix right side: == not preceded by =, followed by non-space non-= char
content = re.sub(r'(?<!=)==([^\s=])', r' == \1', content)


# 4. Fix split {% endif %} tags
# Pattern: selected{% <newline> endif %}
# We normalize any "selected{% ... endif %}" block to be on one line if possible, or just fix the tag
content = re.sub(r'selected\{%\s+endif\s+%\}', 'selected{% endif %}', content)

# 5. General cleanup of the option tags to ensure they don't have weird newlines
# This targets the specific structure active in this template
# We look for <option ... > ... </option> where the opening tag might be split
def clean_option_tag(match):
    text = match.group(0)
    # Collapse whitespace
    return re.sub(r'\s+', ' ', text).replace('> {{', '>{{').replace('}} <', '}}<')

# Apply to active_filters lines
content = re.sub(r'<option value="\{\{ opt \}\}"\s+\{% if active_filters\.[^>]+>', clean_option_tag, content)

# Apply to current_val lines
content = re.sub(r'<option value="\{\{ opt \}\}"\s+\{% if current_val[^>]+>', clean_option_tag, content)

# 6. Fix split {{ product.price|... }} tag
# Pattern: {{ product.price|calculate_discount:discount|floatformat:2 <newline> }}
# We just want to join it.
content = re.sub(
    r'\{\{\s*product\.price\|calculate_discount:discount\|floatformat:2\s+\}\}',
    '{{ product.price|calculate_discount:discount|floatformat:2 }}',
    content
)

print("Applied omnibus fixes.")

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print(f"File saved. New length: {len(content)}")
