#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Тест с детальным логированием"""

import asyncio
import logging
from models import Session, Task, User
from ai_integration.chat import chat_with_ai

# КРИТИЧЕСКИ ВАЖНО: Включить INFO логирование
logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(name)s:%(message)s')

async def main():
    user_id = 888999
    session = Session()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if user:
        session.query(Task).filter_by(user_id=user.id).delete()
        session.commit()
    
    # Создаём задачу
    print("\n1. СОЗДАНИЕ ЗАДАЧИ")
    print("="*80)
    await chat_with_ai("Напомни проверить почту через 30 минут", user_id=user_id)
    
    # Переносим
    print("\n2. ПЕРЕНОС ЗАДАЧИ")
    print("="*80)
    response = await chat_with_ai("Перенеси почту на 15:00", user_id=user_id)
    print(f"\n>>> Ответ AI: {response[:150]}")
    
    session.close()

if __name__ == "__main__":
    asyncio.run(main())
