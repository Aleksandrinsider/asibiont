import os
# Clear LOCAL env to use production database
if 'LOCAL' in os.environ:
    del os.environ['LOCAL']

from models import Session, User, Task

session = Session()

# Find your user
user = session.query(User).filter_by(telegram_id=146333757).first()
if not user:
    print('User not found in production DB')
else:
    print(f'User: {user.username} (ID: {user.id})')
    
    # Check tasks delegated TO this user
    username_clean = user.username.replace('@', '') if user.username else ''
    delegated_to = session.query(Task).filter(
        Task.delegated_to_username.ilike(username_clean),
        Task.delegation_status.in_(['pending', 'accepted']),
        Task.status != 'deleted'
    ).all()
    
    print(f'\nTasks delegated TO {user.username}: {len(delegated_to)}')
    for task in delegated_to:
        delegator = session.query(User).filter_by(id=task.user_id).first()
        delegator_name = delegator.username if delegator else 'unknown'
        print(f'  - From: {delegator_name} (status: {task.delegation_status})')
    
    # Check tasks delegated BY this user
    delegated_by = session.query(Task).filter(
        Task.user_id == user.id,
        Task.delegated_to_username.isnot(None),
        Task.delegation_status.in_(['pending', 'accepted']),
        Task.status != 'deleted'
    ).all()
    
    print(f'\nTasks delegated BY {user.username}: {len(delegated_by)}')
    for task in delegated_by:
        print(f'  - To: {task.delegated_to_username} (status: {task.delegation_status})')

session.close()