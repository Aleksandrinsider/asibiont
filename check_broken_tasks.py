import os
if 'LOCAL' in os.environ:
    del os.environ['LOCAL']

from models import Session, User, Task

session = Session()

# Find tasks delegated to aleksandrinsider
user = session.query(User).filter_by(telegram_id=146333757).first()
if user:
    username_clean = user.username.replace('@', '')
    delegated_to = session.query(Task).filter(
        Task.delegated_to_username.ilike(username_clean),
        Task.delegation_status.in_(['pending', 'accepted']),
        Task.status != 'deleted'
    ).all()
    
    print(f'Tasks delegated TO {user.username}:')
    for task in delegated_to:
        print(f'\n  Task ID: {task.id}')
        print(f'  user_id: {task.user_id}')
        print(f'  title: {task.title}')
        print(f'  delegated_to: {task.delegated_to_username}')
        print(f'  delegation_status: {task.delegation_status}')
        
        # Try to find delegator
        if task.user_id:
            delegator = session.query(User).filter_by(id=task.user_id).first()
            if delegator:
                print(f'  delegator: {delegator.username} (EXISTS)')
            else:
                print(f'  delegator: NOT FOUND (user_id {task.user_id} does not exist)')
                
                # Try to find if there's a user with similar name in task
                print(f'  Looking for user in task title...')
                # The task might contain username information
        else:
            print(f'  delegator: user_id is NULL!')

session.close()