"""
Скрипт для исправления проблемы с post_id в таблице comments
Удаляет комментарии без post_id
"""
import logging
from models import Session, Comment

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def fix_comments():
    """Удалить комментарии с null post_id"""
    session = Session()
    try:
        # Найти комментарии без post_id
        bad_comments = session.query(Comment).filter(Comment.post_id == None).all()
        
        if bad_comments:
            logger.info(f"Найдено {len(bad_comments)} комментариев без post_id")
            for comment in bad_comments:
                logger.info(f"Удаление комментария id={comment.id}, user_id={comment.user_id}, content='{comment.content[:50]}'")
                session.delete(comment)
            
            session.commit()
            logger.info(f"Успешно удалено {len(bad_comments)} комментариев")
        else:
            logger.info("Не найдено комментариев без post_id")
        
    except Exception as e:
        logger.error(f"Ошибка при исправлении комментариев: {e}", exc_info=True)
        session.rollback()
    finally:
        session.close()

if __name__ == '__main__':
    fix_comments()
    logger.info("Готово!")
