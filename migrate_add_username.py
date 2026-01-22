# -*- coding: utf-8 -*-
"""Миграция: добавление поля username в таблицы posts и comments"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models import engine, Session
from sqlalchemy import text, inspect

def migrate():
    """Добавить username в posts и comments"""
    session = Session()
    inspector = inspect(engine)
    
    try:
        # Проверяем, существует ли таблица posts
        if 'posts' in inspector.get_table_names():
            posts_columns = [col['name'] for col in inspector.get_columns('posts')]
            
            if 'username' not in posts_columns:
                print("Добавляем колонку username в таблицу posts...")
                session.execute(text('ALTER TABLE posts ADD COLUMN username VARCHAR(255)'))
                session.commit()
                print("✓ Колонка username добавлена в posts")
                
                # Заполняем username для существующих постов
                print("Заполняем username для существующих постов...")
                session.execute(text('''
                    UPDATE posts 
                    SET username = users.username 
                    FROM users 
                    WHERE posts.user_id = users.id
                '''))
                session.commit()
                print("✓ Username заполнен для существующих постов")
            else:
                print("✓ Колонка username уже существует в posts")
        
        # Проверяем, существует ли таблица comments
        if 'comments' in inspector.get_table_names():
            comments_columns = [col['name'] for col in inspector.get_columns('comments')]
            
            if 'username' not in comments_columns:
                print("Добавляем колонку username в таблицу comments...")
                session.execute(text('ALTER TABLE comments ADD COLUMN username VARCHAR(255)'))
                session.commit()
                print("✓ Колонка username добавлена в comments")
                
                # Заполняем username для существующих комментариев
                print("Заполняем username для существующих комментариев...")
                session.execute(text('''
                    UPDATE comments 
                    SET username = users.username 
                    FROM users 
                    WHERE comments.user_id = users.id
                '''))
                session.commit()
                print("✓ Username заполнен для существующих комментариев")
            else:
                print("✓ Колонка username уже существует в comments")
        
        print("\n✅ Миграция завершена успешно!")
        
    except Exception as e:
        session.rollback()
        print(f"❌ Ошибка миграции: {e}")
        raise
    finally:
        session.close()

if __name__ == "__main__":
    migrate()
