"""
Маркетплейс пользовательских агентов.
"""
import json
import logging
import datetime
from typing import Optional

logger = logging.getLogger(__name__)


def load_agent_personality(agent_id: int, session=None) -> Optional[dict]:
    """
    Загружает данные агента из БД.
    Возвращает dict с personality, tools_allowed, knowledge_snippets или None.
    """
    close = False
    if session is None:
        from models import Session
        session = Session()
        close = True
    try:
        from models import UserAgent
        agent = session.query(UserAgent).filter_by(id=agent_id, status='active').first()
        if not agent:
            return None
        tools = json.loads(agent.tools_allowed or '[]')
        kb_raw = json.loads(agent.knowledge_base or '[]')
        # Берём до 8 фрагментов базы знаний в контекст (text + url)
        kb_snippets = []
        for item in kb_raw[:8]:
            if item.get('type') == 'text' and item.get('content'):
                kb_snippets.append(item['content'][:800])
            elif item.get('type') == 'url' and item.get('url'):
                title = item.get('name') or item.get('url')
                kb_snippets.append(f"[Ссылка] {title}: {item['url']}")
        return {
            'id': agent.id,
            'name': agent.name,
            'personality': agent.personality or '',
            'tools_allowed': tools,
            'knowledge_snippets': kb_snippets,
            'price_per_message': agent.price_per_message,
            'author_id': agent.author_id,
            'author_royalty_pct': agent.author_royalty_pct,
            'trial_messages': agent.trial_messages,
        }
    finally:
        if close:
            session.close()


def build_agent_system_prompt(agent_data: dict, base_system_prompt: str) -> str:
    """
    Инжектирует личность кастомного агента поверх базового системного промпта.
    Базовый промпт сохраняется — инструменты и правила поведения работают как обычно.
    """
    personality = agent_data.get('personality', '').strip()
    name = agent_data.get('name', 'Агент')
    kb_snippets = agent_data.get('knowledge_snippets', [])

    overlay = f"""
═══════════════════════════════════════════════════════
РЕЖИМ КАСТОМНОГО АГЕНТА: {name}
═══════════════════════════════════════════════════════
Ты сейчас выступаешь как агент «{name}», созданный другим пользователем платформы.
Веди себя ТОЧНО в соответствии с описанием ниже. Сохраняй этот характер постоянно.
Технические возможности, правила биллинга и инструменты работают как обычно.

ЛИЧНОСТЬ И ХАРАКТЕР:
{personality}
"""

    if kb_snippets:
        overlay += "\nБАЗА ЗНАНИЙ АГЕНТА (используй при ответах):\n"
        for i, snippet in enumerate(kb_snippets, 1):
            overlay += f"[{i}] {snippet}\n"

    overlay += "\n═══════════════════════════════════════════════════════\n"

    combined = overlay + "\n" + base_system_prompt

    # Краткое напоминание в КОНЦЕ промпта — чтобы AI не «забыл» личность после длинного контекста
    reminder = (
        f"\n\n[🎭 НАПОМИНАНИЕ: ты сейчас агент «{name}». "
        f"Строго придерживайся описанной выше личности в КАЖДОМ ответе. "
        f"Нарушение характера = провал роли.]"
    )
    return combined + reminder


def get_user_active_agent(user_id: int, session=None) -> Optional[int]:
    """
    Возвращает agent_id если пользователь сейчас общается с кастомным агентом.
    Хранится в user.memory JSON под ключом 'active_agent_id'.
    """
    close = False
    if session is None:
        from models import Session
        session = Session()
        close = True
    try:
        from models import User
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user or not user.memory:
            return None
        try:
            mem = json.loads(user.memory)
            return mem.get('active_agent_id')
        except Exception:
            return None
    finally:
        if close:
            session.close()


def set_user_active_agent(user_id: int, agent_id: Optional[int], session=None):
    """Устанавливает/сбрасывает активного кастомного агента для пользователя."""
    close = False
    if session is None:
        from models import Session
        session = Session()
        close = True
    try:
        from models import User
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return
        mem = {}
        if user.memory:
            try:
                mem = json.loads(user.memory)
            except Exception:
                pass
        if agent_id is None:
            mem.pop('active_agent_id', None)
        else:
            mem['active_agent_id'] = agent_id
        user.memory = json.dumps(mem, ensure_ascii=False)
        session.commit()
    finally:
        if close:
            session.close()


# ─── Биллинг агентов ───────────────────────────────────────────────────────────

def bill_agent_message(user_id: int, agent_id: int, session=None) -> dict:
    """
    Списывает токены за сообщение кастомному агенту.
    Возвращает {'success': bool, 'is_trial': bool, 'error': str}.
    """
    close = False
    if session is None:
        from models import Session
        session = Session()
        close = True
    try:
        from models import User, UserAgent, AgentSubscription, AgentRun
        from config import FREE_ACCESS_MODE
        if FREE_ACCESS_MODE:
            return {'success': True, 'error': ''}

        agent = session.query(UserAgent).filter_by(id=agent_id, status='active').first()
        if not agent:
            return {'success': False, 'error': 'Агент не найден'}

        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return {'success': False, 'error': 'Пользователь не найден'}

        # Ищем/создаём подписку
        sub = session.query(AgentSubscription).filter_by(
            user_id=user.id, agent_id=agent_id).first()
        if not sub:
            sub = AgentSubscription(user_id=user.id, agent_id=agent_id)
            session.add(sub)
            session.flush()

        # Платное сообщение
        cost = agent.price_per_message
        balance = user.token_balance or 0
        if balance < cost:
            return {'success': False,
                    'error': f'Недостаточно токенов. Нужно {cost}, баланс {balance}'}

        author_share = int(cost * agent.author_royalty_pct / 100)
        platform_share = cost - author_share

        user.token_balance = balance - cost
        user.tokens_spent = (user.tokens_spent or 0) + cost

        # Начисляем автору
        author = session.query(User).filter_by(id=agent.author_id).first()
        if author:
            author.token_balance = (author.token_balance or 0) + author_share

        sub.messages_count += 1
        sub.tokens_spent += cost
        sub.last_message_at = datetime.datetime.now(datetime.timezone.utc)
        agent.messages_count += 1

        run = AgentRun(user_id=user.id, agent_id=agent_id,
                       tokens_charged=cost, author_earnings=author_share,
                       platform_earnings=platform_share, is_trial=False)
        session.add(run)
        session.commit()
        return {'success': True, 'error': ''}
    except Exception as e:
        session.rollback()
        logger.error(f"[BILLING] bill_agent_message error: {e}")
        return {'success': False, 'error': str(e)}
    finally:
        if close:
            session.close()
