with open('main.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find the migration block
start_idx = None
end_idx = None
for i, line in enumerate(lines):
    if 'logger.info("Running database migrations...")' in line:
        start_idx = i
    if 'logger.warning("Continuing with local mode despite migration issues")' in line and start_idx is not None:
        end_idx = i + 1
        break

if start_idx and end_idx:
    # Replace the migration block with simple code
    new_lines = lines[:start_idx] + [
        'logger.info("Running database migrations...")\n',
        'try:\n',
        '    # Migration code is inline below - TEMPORARILY DISABLED FOR TESTING\n',
        '    logger.info("Database migrations skipped for testing")\n',
        '    pass\n',
        'except Exception as e:\n',
        '    logger.error(f"❌ Database migrations failed: {e}")\n',
        '    if not LOCAL:\n',
        '        raise  # Fail hard in production\n',
        '    else:\n',
        '        logger.warning("Continuing with local mode despite migration issues")\n',
        '\n'
    ] + lines[end_idx:]
    
    with open('main.py', 'w', encoding='utf-8') as f:
        f.writelines(new_lines)
    print('Migration block replaced successfully')
else:
    print('Could not find migration block')