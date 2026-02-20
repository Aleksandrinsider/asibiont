import sqlalchemy

DB = 'postgresql://postgres:upZTbJrZvoxnoSPdUDaOwnLuOvnNSbML@nozomi.proxy.rlwy.net:52451/railway'
engine = sqlalchemy.create_engine(DB)

with engine.connect() as conn:
    # Clear garbage from memory for user 146333757
    result = conn.execute(sqlalchemy.text(
        "UPDATE users SET memory = '' WHERE telegram_id = 146333757"
    ))
    conn.commit()
    print(f"Cleared memory for user 146333757, rows affected: {result.rowcount}")
    
    # Verify
    r = conn.execute(sqlalchemy.text(
        "SELECT memory FROM users WHERE telegram_id = 146333757"
    ))
    for row in r:
        print(f"Memory after cleanup: '{row[0]}'")
