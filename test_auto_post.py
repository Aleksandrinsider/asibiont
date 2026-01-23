#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Test auto post generation"""

import asyncio
from models import Session, User
from auto_post_service import generate_progress_post, create_auto_post

async def test_post():
    session = Session()
    
    # Get aleksandrinsider user
    user = session.query(User).filter_by(username='aleksandrinsider').first()
    
    if not user:
        print("User not found")
        return
    
    print(f"Generating post for user: {user.username}")
    
    # Generate post content
    content = await generate_progress_post(user.telegram_id, session)
    
    if content:
        print(f"\n✅ Generated post:\n{content}\n")
        
        # Create post in database
        success = await create_auto_post(user.telegram_id, content, session, notify=False)
        
        if success:
            print("✅ Post created in database!")
        else:
            print("❌ Failed to create post")
    else:
        print("❌ Failed to generate post content")
    
    session.close()

if __name__ == "__main__":
    asyncio.run(test_post())
