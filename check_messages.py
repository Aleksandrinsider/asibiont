from models import Session, User, Interaction

s = Session()
u = s.query(User).filter_by(telegram_id=146333757).first()

if u:
    print(f'User: {u.username} (ID: {u.id})')
    ints = s.query(Interaction).filter_by(user_id=u.id).order_by(Interaction.created_at).all()
    print(f'Interactions in DB: {len(ints)}')
    
    if ints:
        print('\nLast 10 messages:')
        for i in ints[-10:]:
            content = i.content[:80] + '...' if len(i.content) > 80 else i.content
            print(f'  [{i.message_type}] {i.created_at} - {content}')
    else:
        print('No interactions found!')
else:
    print('User not found!')
    
s.close()
