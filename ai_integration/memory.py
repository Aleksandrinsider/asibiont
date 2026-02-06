# Memory management functions: no encryption

import logging
import json
import datetime

logger = logging.getLogger(__name__)


def encrypt_data(data):
    """Return data as is (no encryption)"""
    return data


def decrypt_data(data):
    """Return data as is (no decryption)"""
    return data


def update_user_memory(info, user_id=None):
    """Update user memory with new information"""
    from models import Session, User

    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if user:
            # Decrypt existing memory
            existing_decrypted = ""
            if user.memory:
                try:
                    existing_decrypted = decrypt_data(user.memory)
                except Exception:
                    existing_decrypted = ""
            # Add new information
            if existing_decrypted:
                existing_decrypted += "\n" + info
            else:
                existing_decrypted = info
            # Encrypt and save
            encrypted = encrypt_data(existing_decrypted)
            user.memory = encrypted
            session.commit()
            result = "Сохранена информация."
        else:
            result = "Пользователь не найден."
        return result
    finally:
        session.close()


class LongTermMemory:
    """Class for managing long-term memory with project history, preferences, and patterns"""

    def __init__(self, user_id):
        self.user_id = user_id

    def save_project_context(self, project_name, tasks, insights):
        """Save context of a project for future reference"""
        from models import Session, User
        import json

        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=self.user_id).first()
            if user:
                # Load existing long-term memory
                ltm = {}
                if user.long_term_memory:
                    try:
                        ltm = json.loads(decrypt_data(user.long_term_memory))
                    except Exception as e:
                        logger.warning(f"[MEMORY] Failed to parse long_term_memory: {e}")
                        ltm = {}

                # Add project context
                if 'projects' not in ltm:
                    ltm['projects'] = {}
                ltm['projects'][project_name] = {
                    'tasks': tasks,
                    'insights': insights,
                    'saved_at': str(datetime.datetime.now())
                }

                # Save back
                user.long_term_memory = encrypt_data(json.dumps(ltm))
                session.commit()
                return True
        finally:
            session.close()
        return False

    def recall_similar_situations(self, current_task):
        """Recall similar situations from past projects"""
        from models import Session, User
        import json

        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=self.user_id).first()
            if user and user.long_term_memory:
                ltm = json.loads(decrypt_data(user.long_term_memory))
                projects = ltm.get('projects', {})

                similar = []
                for project_name, data in projects.items():
                    tasks = data.get('tasks', [])
                    if any(current_task.lower() in task.lower() for task in tasks):
                        similar.append({
                            'project': project_name,
                            'insights': data.get('insights', [])
                        })

                return similar
        finally:
            session.close()
        return []
