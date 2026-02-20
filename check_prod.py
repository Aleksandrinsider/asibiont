import sqlalchemy

DB = 'postgresql://postgres:upZTbJrZvoxnoSPdUDaOwnLuOvnNSbML@nozomi.proxy.rlwy.net:52451/railway'
engine = sqlalchemy.create_engine(DB)

with engine.connect() as conn:
    # User memory
    r = conn.execute(sqlalchemy.text(
        "SELECT id, telegram_id, first_name, memory, long_term_memory FROM users WHERE telegram_id=146333757"
    ))
    for row in r:
        print(f'=== USER id={row[0]} tg={row[1]} name={row[2]} ===')
        mem = row[3] or ''
        print(f'\nMEMORY ({len(mem)} chars):')
        print(mem)
        ltm = row[4] or ''
        print(f'\nLONG_TERM_MEMORY ({len(ltm)} chars):')
        print(ltm[:2000] if ltm else '(empty)')

    # Tasks
    print('\n=== TASKS ===')
    r = conn.execute(sqlalchemy.text(
        "SELECT t.title, t.status, t.due_date, t.description FROM tasks t JOIN users u ON t.user_id = u.id WHERE u.telegram_id=146333757"
    ))
    for row in r:
        print(f'  [{row[1]}] {row[0]} (due: {row[2]})')
        if row[3]:
            print(f'    desc: {row[3][:100]}')

    # Goals
    print('\n=== GOALS ===')
    r = conn.execute(sqlalchemy.text(
        "SELECT g.title, g.status FROM goals g JOIN users u ON g.user_id = u.id WHERE u.telegram_id=146333757"
    ))
    rows = list(r)
    if rows:
        for row in rows:
            print(f'  [{row[1]}] {row[0]}')
    else:
        print('  (empty)')

    # Profile
    print('\n=== PROFILE ===')
    r = conn.execute(sqlalchemy.text(
        "SELECT p.skills, p.interests, p.goals, p.city, p.company, p.position FROM user_profiles p JOIN users u ON p.user_id = u.id WHERE u.telegram_id=146333757"
    ))
    for row in r:
        print(f'  skills: {row[0]}')
        print(f'  interests: {row[1]}')
        print(f'  goals: {row[2]}')
        print(f'  city: {row[3]}')
        print(f'  company: {row[4]}')
        print(f'  position: {row[5]}')
