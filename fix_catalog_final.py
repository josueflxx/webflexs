import re

file_path = r'c:\Users\Brian\Desktop\webflexs\catalog\templates\catalog\catalog_v3.html'

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Fix broken {% endif %} tags that were split
# Pattern: {%\n                                     endif %}
content = re.sub(r'\{%\s+endif\s+%\}', r'{% endif %}', content)

# Fix the specific broken pattern on line 121-122
# {% if active_filters.width == opt|stringformat:"s" %}selected{%
#     endif %}
content = re.sub(
    r'(\{% if [^%]+%\})selected\{%\s+endif\s+%\}',
    r'\1selected{% endif %}',
    content
)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Fixed broken template tags")
