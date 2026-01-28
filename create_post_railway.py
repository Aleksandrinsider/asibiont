#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Script to create auto-post for user 146333757 in Railway production
"""

import asyncio
import sys
import os

# Add current directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from auto_post_service import create_auto_post
from models import Session

async def create_post_for_user():
    """Create auto-post for user 146333757"""
    session = Session()
    try:
        # Create post for user 146333757
        result = await create_auto_post(
            146333757,
            'Тестовый автопост для проверки ленты новостей в продакшене',
            session,
            notify=False
        )
        print(f'Результат создания поста: {result}')

        if result:
            print('✅ Пост успешно создан!')
        else:
            print('❌ Ошибка при создании поста')

    except Exception as e:
        print(f'❌ Ошибка: {e}')
    finally:
        session.close()

if __name__ == "__main__":
    asyncio.run(create_post_for_user())