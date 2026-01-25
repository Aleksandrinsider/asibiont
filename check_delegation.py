from models import Session, User, Task

session = Session()

# Get the Gold user
gold_user = session.query(User).filter_by(telegram_id=146333757).first()
print(f'Gold user: {gold_user.username}')

# Get tasks delegated by this user
delegated_by_user = session.query(Task).filter(
    Task.user_id == gold_user.id,
    Task.delegated_to_username.isnot(None)
).all()

print(f'Tasks delegated BY {gold_user.username}: {len(delegated_by_user)}')
for task in delegated_by_user:
    print(f'  - To: {task.delegated_to_username}, status: {task.delegation_status}')

# Get tasks delegated TO this user
delegated_to_user = session.query(Task).filter(
    Task.delegated_to_username.ilike(gold_user.username.replace('@', '')),
    Task.delegation_status == 'accepted'
).all()

print(f'\nTasks delegated TO {gold_user.username}: {len(delegated_to_user)}')
for task in delegated_to_user:
    delegator = session.query(User).filter_by(id=task.user_id).first()
    delegator_name = delegator.username if delegator else "unknown"
    print(f'  - From: {delegator_name} (task: {task.title[:30]}...)')

session.close()