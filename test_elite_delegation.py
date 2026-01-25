#!/usr/bin/env python3
"""Test script for elite partners API with delegation"""

import os
os.environ['LOCAL'] = '1'

import asyncio
import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from main import api_elite_partners_handler
from models import Session, User, Task

async def test_elite_partners_with_delegation():
    """Test the elite partners API with delegation contacts"""

    # Find a Gold user who has delegated tasks
    session = Session()

    # Use the specific Gold user we know has delegation
    gold_user = session.query(User).filter_by(telegram_id=146333757).first()  # aleksandrinsider
    if not gold_user:
        print("Gold user not found")
        session.close()
        return

    print(f"Testing with Gold user: {gold_user.username} (ID: {gold_user.telegram_id})")

    # Check what tasks this user delegated
    user_delegated_tasks = session.query(Task).filter(
        Task.user_id == gold_user.id,
        Task.delegated_to_username.isnot(None),
        Task.delegation_status.in_(['pending', 'accepted'])
    ).all()

    print(f"User delegated {len(user_delegated_tasks)} tasks:")
    for task in user_delegated_tasks:
        print(f"  - To: {task.delegated_to_username}, status: {task.delegation_status}")

    # Check what tasks were delegated TO this user
    username_clean = gold_user.username.replace('@', '').lower() if gold_user.username else ''
    delegated_to_user = session.query(Task).filter(
        Task.delegated_to_username.ilike(username_clean),
        Task.delegation_status == 'accepted'
    ).all()

    print(f"Tasks delegated TO user: {len(delegated_to_user)}")
    for task in delegated_to_user:
        delegator = session.query(User).filter_by(id=task.user_id).first()
        print(f"  - From: {delegator.username if delegator else 'unknown'}")

    session.close()

    # Create mock request
    request = make_mocked_request('GET', '/api/elite_partners')

    # Mock get_user_id_from_request to return our test user
    async def mock_get_user_id(request):
        return gold_user.telegram_id

    # Temporarily replace the function
    import main
    original_get_user_id = main.get_user_id_from_request
    main.get_user_id_from_request = mock_get_user_id

    try:
        # Call the handler
        response = await api_elite_partners_handler(request)

        if response.status == 200:
            import json
            data = json.loads(response.body.decode('utf-8'))  # Parse JSON from response body
            partners = data.get('partners', [])
            print(f"\n✅ API returned {len(partners)} partners")

            # Count by type
            elite_count = sum(1 for p in partners if p.get('type') == 'elite')
            delegation_count = sum(1 for p in partners if p.get('type') == 'delegation')

            print(f"  - Elite (Gold) partners: {elite_count}")
            print(f"  - Delegation partners: {delegation_count}")

            # Show delegation partners details
            if delegation_count > 0:
                print("\nDelegation partners:")
                for partner in partners:
                    if partner.get('type') == 'delegation':
                        contact_info = partner.get('contact_info', 'N/A')
                        reason = partner.get('reason', 'N/A')
                        print(f"  - {contact_info} ({reason})")
            else:
                print("\nNo delegation partners found in API response")

        else:
            import json
            error_data = json.loads(response.body.decode('utf-8'))  # Parse JSON from response body
            print(f"❌ API error: {error_data}")

    except Exception as e:
        print(f"❌ Exception: {e}")
        import traceback
        traceback.print_exc()

    finally:
        # Restore original function
        main.get_user_id_from_request = original_get_user_id

if __name__ == "__main__":
    asyncio.run(test_elite_partners_with_delegation())