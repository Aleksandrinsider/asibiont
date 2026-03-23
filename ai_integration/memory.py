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
    """Update user memory with new information.
    
    Preserves JSON structure if memory was stored by save_user_rule ({"rules": [...]}).
    Adds new info under "notes" key in that case.
    """
    from models import Session, User

    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if user:
            existing_decrypted = ""
            if user.memory:
                try:
                    existing_decrypted = decrypt_data(user.memory)
                except Exception:
                    existing_decrypted = ""

            # Try to preserve JSON structure (used by save_user_rule)
            mem_dict = None
            if existing_decrypted:
                try:
                    mem_dict = json.loads(existing_decrypted)
                    if isinstance(mem_dict, dict):
                        # JSON format — add info under "notes" key
                        notes = mem_dict.get('notes', [])
                        if isinstance(notes, str):
                            notes = [notes] if notes else []
                        notes.append(info)
                        # Keep last 30 notes to avoid bloat
                        mem_dict['notes'] = notes[-30:]
                        serialized = json.dumps(mem_dict, ensure_ascii=False)
                        if len(serialized) > 5000:
                            mem_dict['notes'] = mem_dict['notes'][-15:]
                            serialized = json.dumps(mem_dict, ensure_ascii=False)
                        user.memory = encrypt_data(serialized)
                        session.commit()
                        return "Сохранена информация."
                except (json.JSONDecodeError, TypeError):
                    mem_dict = None

            # Plain text format (legacy)
            if existing_decrypted:
                existing_decrypted += "\n" + info
            else:
                existing_decrypted = info
            # Ограничиваем размер памяти (максимум 5000 символов)
            if len(existing_decrypted) > 5000:
                lines = existing_decrypted.split('\n')
                trimmed = []
                total = 0
                for line in reversed(lines):
                    if total + len(line) + 1 > 4000:
                        break
                    trimmed.insert(0, line)
                    total += len(line) + 1
                existing_decrypted = '\n'.join(trimmed)
                logger.info(f"[MEMORY] Trimmed memory for user {user_id} to {len(existing_decrypted)} chars")
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

    def save_search_query(self, query, results_summary, insights=None):
        """Save search query and results for future personalization"""
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

                # Add search history
                if 'search_history' not in ltm:
                    ltm['search_history'] = []
                
                search_entry = {
                    'query': query,
                    'results_summary': results_summary,
                    'insights': insights or [],
                    'timestamp': str(datetime.datetime.now()),
                    'topics': self._extract_topics(query, results_summary)
                }
                
                # Keep only last 50 searches to avoid bloat
                ltm['search_history'].append(search_entry)
                ltm['search_history'] = ltm['search_history'][-50:]

                # Update interests based on search patterns
                self._update_interests(ltm, query, results_summary)

                # Save back
                user.long_term_memory = encrypt_data(json.dumps(ltm))
                session.commit()
                return True
        finally:
            session.close()
        return False

    def _extract_topics(self, query, results_summary):
        """Extract topics from search query and results"""
        topics = []
        
        # Keywords that indicate topics
        topic_keywords = {
            'AI': ['ai', 'ml', 'искусственный интеллект', 'машинное обучение', 'нейросеть', 'deep learning', 'нейронная сеть'],
            'бизнес': ['бизнес', 'стартап', 'компания', 'предпринимательство'],
            'программирование': ['python', 'javascript', 'разработка', 'код', 'программирование'],
            'маркетинг': ['маркетинг', 'продвижение', 'реклама', 'продажи'],
            'финансы': ['финансы', 'инвестиции', 'криптовалюта', 'блокчейн'],
            'здоровье': ['здоровье', 'спорт', 'фитнес', 'медицина'],
            'образование': ['образование', 'курсы', 'обучение', 'университет']
        }
        
        query_lower = query.lower()
        for topic, keywords in topic_keywords.items():
            if any(keyword in query_lower for keyword in keywords):
                topics.append(topic)
        
        return topics

    def _update_interests(self, ltm, query, results_summary):
        """Update user interests based on search patterns"""
        if 'interests' not in ltm:
            ltm['interests'] = {}
        
        topics = self._extract_topics(query, results_summary)
        
        for topic in topics:
            if topic not in ltm['interests']:
                ltm['interests'][topic] = 0
            ltm['interests'][topic] += 1



    def get_cached_search_result(self, query):
        """Get cached search result if available and recent"""
        from models import Session, User
        import json
        from datetime import datetime, timedelta

        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=self.user_id).first()
            if user and user.long_term_memory:
                ltm = json.loads(decrypt_data(user.long_term_memory))
                
                search_history = ltm.get('search_history', [])
                
                # Look for similar recent queries (last 7 days)
                week_ago = datetime.now() - timedelta(days=7)
                
                for search in reversed(search_history):
                    search_time = datetime.fromisoformat(search['timestamp'])
                    if search_time > week_ago:
                        # Simple similarity check
                        if self._queries_similar(query, search['query']):
                            return {
                                'cached': True,
                                'results': search['results_summary'],
                                'insights': search.get('insights', []),
                                'cached_at': search['timestamp']
                            }
        finally:
            session.close()
        return None

    def _queries_similar(self, query1, query2):
        """Check if two queries are similar"""
        q1_words = set(query1.lower().split())
        q2_words = set(query2.lower().split())
        
        # If 70% of words overlap, consider similar
        intersection = q1_words.intersection(q2_words)
        union = q1_words.union(q2_words)
        
        if union:
            similarity = len(intersection) / len(union)
            return similarity >= 0.7
        
        return False
