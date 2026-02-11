"""Fix indentation in main.py for migration block"""

with open('main.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find the migration block boundaries
start_line = None
end_line = None

for i, line in enumerate(lines):
    if 'session = Session()' in line and i > 120 and i < 135:
        start_line = i
    if 'session.close()' in line and 'Migration session closed successfully' in lines[i+1] if i+1 < len(lines) else False:
        end_line = i + 1  # Include the "Migration session closed" line
        break

if start_line is None or end_line is None:
    print(f"Could not find migration block boundaries: start={start_line}, end={end_line}")
    exit(1)

print(f"Found migration block: lines {start_line+1} to {end_line+1}")

# Add 4 spaces of indentation to all lines in the migration block
fixed_lines = []
for i, line in enumerate(lines):
    if start_line <= i <= end_line:
        # Don't add indentation to empty lines
        if line.strip() == '':
            fixed_lines.append(line)
        else:
            fixed_lines.append('    ' + line)
    else:
        fixed_lines.append(line)

# Write fixed file
with open('main.py', 'w', encoding='utf-8') as f:
    f.writelines(fixed_lines)

print(f"Fixed indentation for {end_line - start_line + 1} lines")
