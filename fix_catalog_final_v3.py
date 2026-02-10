import re
import os

file_path = r'c:\Users\Brian\Desktop\webflexs\catalog\templates\catalog\catalog_v3.html'

if not os.path.exists(file_path):
    print(f"Error: File not found at {file_path}")
    exit(1)

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

print(f"Original content length: {len(content)}")

# Fix: Join split {% endif %} tags
# The pattern we see is: selected{% <newline> endif %}
# We want: selected{% endif %}

# 1. Fix the specific case where {% is at end of line and endif %} is at start of next
# Regex explanation:
# selected\{%\s+  -> matches "selected{% " followed by whitespace (including newlines)
# endif\s+%\}     -> matches "endif %}"
pattern = r'selected\{%\s+endif\s+%\}'
content = re.sub(pattern, 'selected{% endif %}', content)

# 2. Also cleanup the "== " spacing just in case (ensure single spaces)
# (This is safe to re-run)
# patterns = [
#     r'active_filters\.fabrication\s*==\s*opt',
#     # ... redundant if we trust previous fix, but let's just focus on the newlines
# ]

# 3. Clean up the content inside option tags to be single line for these filters
# This is a more aggressive cleanup to ensure everything is perfect.
# We look for the entire option block for each filter type.

def normalize_option(match):
    # This function takes the full match of the option tag and cleans it
    text = match.group(0)
    # Remove newlines and extra spaces
    cleaned = re.sub(r'\s+', ' ', text)
    # Fix the specific "selected { % endif % }" spacing if regex didn't catch it nicely
    cleaned = cleaned.replace('selected { % endif % }', 'selected{% endif %}')
    cleaned = cleaned.replace('selected{% endif % }', 'selected{% endif %}')
    cleaned = cleaned.replace('selected { % endif %}', 'selected{% endif %}')
    # Ensure > {{ opt }} is clean
    cleaned = cleaned.replace('> {{ opt }}', '>{{ opt }}')
    return cleaned

# Apply normalization to the 5 specific option blocks
# We search for <option value="{{ opt }}" ... </option>
# We use a broad match but limit it to the ones with active_filters to avoid hitting other things (though safe enough)
content = re.sub(r'<option value="\{\{ opt \}\}"\s+\{% if active_filters\.[^%]+%\}selected\s*\{%\s*endif\s*%\}\s*>[^<]+</option>', normalize_option, content)

# Manual fix for the specific split cases if regex sub above missed them due to complexity
# Case: Width
# <option value="{{ opt }}" {% if active_filters.width == opt|stringformat:"s" %}selected{%
#                                     endif %}>{{ opt }} mm</option>
content = re.sub(
    r'(active_filters\.width == opt\|stringformat:"s" %\}selected)\{%\s+endif\s+%\}',
    r'\1{% endif %}',
    content
)

# Case: Length
content = re.sub(
    r'(active_filters\.length == opt\|stringformat:"s" %\}selected)\{%\s+endif\s+%\}',
    r'\1{% endif %}',
    content
)

# Fix any remaining {{ opt }} splits
# {{ \n opt }} -> {{ opt }}
content = re.sub(r'\{\{\s+\n\s+opt\s+\}\}', r'{{ opt }}', content)
content = re.sub(r'\{\{\s+opt\s+\n\s+\}\}', r'{{ opt }}', content)


print("Applied fixes.")

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print(f"File saved. New length: {len(content)}")
