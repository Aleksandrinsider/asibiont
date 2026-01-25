#!/usr/bin/env python3
"""Test delegation contacts in regular partners API"""

import os
os.environ['LOCAL'] = '1'

import asyncio
import sys
import json
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from aiohttp.test_utils import make_mocked_request
from main import api_partners_handler
from models import Session, User

async def test_delegation_contacts():
    """Test delegation contacts in partners API"""

    session = Session()
    # Use aleksandrinsider who has delegation
    gold_user = session.query(User).filter_by(username='aleksandrinsider').first()
    
    if not gold_user:
        print("Gold user not found")
        session.close()
        return

    print(f"Testing with user: {gold_user.username} (ID: {gold_user.telegram_id})")
    session.close()

    # Create mock request
    request = make_mocked_request('GET', '/api/partners')

    # Mock get_user_id_from_request
    async def mock_get_user_id(request):
        return gold_user.telegram_id

    import main
    original_get_user_id = main.get_user_id_from_request
    main.get_user_id_from_request = mock_get_user_id

    try:
        response = await api_partners_handler(request)

        if response.status == 200:
            data = json.loads(response.body.decode('utf-8'))
            partners = data.get('partners', [])
            
            print(f"\n✅ API returned {len(partners)} total partners")
            
            # Count by type
            type_counts = {}
            for partner in partners:
                ptype = partner.get('type', 'unknown')
                type_counts[ptype] = type_counts.get(ptype, 0) + 1
            
            print("\nPartners by type:")
            for ptype, count in type_counts.items():
                print(f"  - {ptype}: {count}")
            
            # Show delegation contacts
            delegating_to_me = [p for p in partners if p.get('type') == 'delegating_to_me']
            delegating_by_me = [p for p in partners if p.get('type') == 'delegating_by_me']
            
            if delegating_to_me:
                print(f"\n📥 Делегирует мне ({len(delegating_to_me)}):")
                for contact in delegating_to_me:
                    username = contact.get('contact_info', 'N/A')
                    reason = contact.get('reason', 'N/A')
                    print(f"  - {username}: {reason}")
            else:
                print("\n❌ No 'delegating_to_me' contacts found")
            
            if delegating_by_me:
                print(f"\n📤 Делегирую я ({len(delegating_by_me)}):")
                for contact in delegating_by_me:
                    username = contact.get('contact_info', 'N/A')
                    reason = contact.get('reason', 'N/A')
                    print(f"  - {username}: {reason}")
            else:
                print("\n❌ No 'delegating_by_me' contacts found")

        else:
            error_data = json.loads(response.body.decode('utf-8'))
            print(f"❌ API error: {error_data}")

    except Exception as e:
        print(f"❌ Exception: {e}")
        import traceback
        traceback.print_exc()

    finally:
        main.get_user_id_from_request = original_get_user_id

if __name__ == "__main__":
    asyncio.run(test_delegation_contacts())