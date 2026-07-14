import os

directory = 'c:/Users/Brian/Desktop/webflexs/catalog'
for root, dirs, files in os.walk(directory):
    for file in files:
        if file.endswith('.py'):
            path = os.path.join(root, file)
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
                if 'class Category' in content:
                    print(f"Match found in: {file}")
                    # Print lines around the class definition
                    lines = content.split('\n')
                    for idx, line in enumerate(lines):
                        if 'class Category' in line:
                            for offset in range(-2, 15):
                                if 0 <= idx + offset < len(lines):
                                    print(f"{idx+offset+1}: {lines[idx+offset]}")
