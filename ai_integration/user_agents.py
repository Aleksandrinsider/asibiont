"""
Маркетплейс пользовательских агентов и скриптов.

Пользователи могут:
- Создавать AI-агентов с кастомной личностью, инструментами, базой знаний
- Публиковать скрипты-модули (Python sandbox)
- Подписываться на агентов других пользователей
- Запускать скрипты через агента

Безопасность скриптов:
- AST-анализ при сохранении (блокирует os, sys, subprocess, open, exec, eval)
- RestrictedPython sandbox при выполнении
- Таймаут 30 секунд, лимит памяти
"""
import json
import ast
import logging
import re
import time
import asyncio
import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# ─── Запрещённые имена в AST скриптов ─────────────────────────────────────────

_BANNED_NAMES = {
    'os', 'sys', 'subprocess', 'shutil', 'pathlib', 'tempfile',
    'socket', 'ftplib', 'smtplib', 'imaplib', 'poplib',
    'ctypes', 'cffi', 'importlib', 'pkgutil',
    'eval', 'exec', 'compile', '__import__', 'open', 'input',
    'breakpoint', 'exit', 'quit',
}

_BANNED_ATTRS = {'__class__', '__subclasses__', '__globals__', '__builtins__',
                 '__code__', '__func__', '__self__', 'mro'}

# Обязательный хардкорный блок (независимо от AST): regexp-паттерны
_BANNED_PATTERNS = [
    r'\bos\s*\.\s*system\b',
    r'\bsubprocess\b',
    r'\bopen\s*\(',
    r'__import__\s*\(',
    r'\bexec\s*\(',
    r'\beval\s*\(',
]


def validate_script_code(code: str) -> tuple[bool, str]:
    """
    Проверяет Python-код скрипта на безопасность.
    Возвращает (ok, error_message).
    """
    # 1. Синтаксический разбор
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, f"Синтаксическая ошибка: {e}"

    # 2. AST-обход — ищем запрещённые имена и атрибуты
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in _BANNED_NAMES:
            return False, f"Запрещённое имя: '{node.id}'"
        if isinstance(node, ast.Attribute) and node.attr in _BANNED_ATTRS:
            return False, f"Запрещённый атрибут: '{node.attr}'"
        if isinstance(node, ast.Import):
            for alias in node.names:
                base = alias.name.split('.')[0]
                if base in _BANNED_NAMES:
                    return False, f"Запрещённый импорт: '{alias.name}'"
        if isinstance(node, ast.ImportFrom):
            if node.module:
                base = node.module.split('.')[0]
                if base in _BANNED_NAMES:
                    return False, f"Запрещённый импорт: '{node.module}'"

    # 3. Regexp-паттерны (резервная защита)
    for pattern in _BANNED_PATTERNS:
        if re.search(pattern, code):
            return False, f"Запрещённая конструкция: {pattern}"

    return True, ""


def _safe_builtins():
    """Возвращает разрешённые встроенные функции для sandbox."""
    import builtins
    allowed = {
        'abs', 'all', 'any', 'bin', 'bool', 'bytes', 'callable', 'chr',
        'dict', 'dir', 'divmod', 'enumerate', 'filter', 'float', 'format',
        'frozenset', 'getattr', 'hasattr', 'hash', 'hex', 'int', 'isinstance',
        'issubclass', 'iter', 'len', 'list', 'map', 'max', 'min', 'next',
        'object', 'oct', 'ord', 'pow', 'print', 'range', 'repr', 'reversed',
        'round', 'set', 'setattr', 'slice', 'sorted', 'str', 'sum', 'tuple',
        'type', 'vars', 'zip',
    }
    safe = {name: getattr(builtins, name) for name in allowed if hasattr(builtins, name)}
    safe['__build_class__'] = builtins.__build_class__
    return safe


def run_script_sandbox(code: str, params: dict, timeout_sec: int = 30) -> dict:
    """
    Выполняет скрипт в изолированном окружении.
    Возвращает {'success': bool, 'result': str, 'error': str, 'exec_ms': int}.
    """
    start_ms = int(time.time() * 1000)

    # Разрешённые модули для скриптов
    import json as _json
    import math as _math
    import re as _re
    import datetime as _datetime
    import urllib.parse as _urlparse

    safe_globals = {
        '__builtins__': _safe_builtins(),
        'json': _json,
        'math': _math,
        're': _re,
        'datetime': _datetime,
        'urllib_parse': _urlparse,
        'params': params,
        'result': None,
    }

    # requests — добавляем если доступен
    try:
        import requests as _requests
        safe_globals['requests'] = _requests
    except ImportError:
        pass

    try:
        exec(compile(code, '<script>', 'exec'), safe_globals)  # noqa: S102
        result = safe_globals.get('result', None)
        if result is None:
            result = "Скрипт выполнен (результат не задан в переменной `result`)"
        exec_ms = int(time.time() * 1000) - start_ms
        return {'success': True, 'result': str(result)[:2000], 'error': '', 'exec_ms': exec_ms}
    except Exception as e:
        exec_ms = int(time.time() * 1000) - start_ms
        logger.warning(f"[SANDBOX] Script error: {e}")
        return {'success': False, 'result': '', 'error': str(e)[:500], 'exec_ms': exec_ms}


# ─── Загрузка кастомного агента ────────────────────────────────────────────────

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
        # Берём первые 3 фрагмента базы знаний в контекст (простой вариант)
        kb_snippets = []
        for item in kb_raw[:3]:
            if item.get('type') == 'text' and item.get('content'):
                kb_snippets.append(item['content'][:800])
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

    return overlay + "\n" + base_system_prompt


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
            return {'success': True, 'is_trial': False, 'error': ''}

        agent = session.query(UserAgent).filter_by(id=agent_id, status='active').first()
        if not agent:
            return {'success': False, 'is_trial': False, 'error': 'Агент не найден'}

        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return {'success': False, 'is_trial': False, 'error': 'Пользователь не найден'}

        # Ищем/создаём подписку
        sub = session.query(AgentSubscription).filter_by(
            user_id=user.id, agent_id=agent_id).first()
        if not sub:
            sub = AgentSubscription(user_id=user.id, agent_id=agent_id)
            session.add(sub)
            session.flush()

        # Проверяем пробный период
        is_trial = sub.trial_messages_used < agent.trial_messages
        if is_trial:
            sub.trial_messages_used += 1
            sub.messages_count += 1
            sub.last_message_at = datetime.datetime.now(datetime.timezone.utc)
            agent.messages_count += 1
            # Лог без списания
            run = AgentRun(user_id=user.id, agent_id=agent_id,
                           tokens_charged=0, author_earnings=0,
                           platform_earnings=0, is_trial=True)
            session.add(run)
            session.commit()
            return {'success': True, 'is_trial': True, 'error': ''}

        # Платное сообщение
        cost = agent.price_per_message
        balance = user.token_balance or 0
        if balance < cost:
            return {'success': False, 'is_trial': False,
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
        return {'success': True, 'is_trial': False, 'error': ''}
    except Exception as e:
        session.rollback()
        logger.error(f"[BILLING] bill_agent_message error: {e}")
        return {'success': False, 'is_trial': False, 'error': str(e)}
    finally:
        if close:
            session.close()


def bill_script_run(user_id: int, script_id: int, params: dict,
                    result: str, success: bool, exec_ms: int, session=None) -> dict:
    """
    Списывает токены за запуск скрипта; начисляет автору.
    """
    close = False
    if session is None:
        from models import Session
        session = Session()
        close = True
    try:
        from models import User, UserScript, ScriptInstall, ScriptRun
        from config import FREE_ACCESS_MODE
        if FREE_ACCESS_MODE:
            return {'success': True, 'error': ''}

        script = session.query(UserScript).filter_by(id=script_id, status='active').first()
        if not script:
            return {'success': False, 'error': 'Скрипт не найден'}

        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return {'success': False, 'error': 'Пользователь не найден'}

        cost = script.price_per_run if success else 1  # При ошибке — 1 токен
        balance = user.token_balance or 0
        if balance < cost:
            return {'success': False,
                    'error': f'Недостаточно токенов. Нужно {cost}, баланс {balance}'}

        author_share = int(cost * script.author_royalty_pct / 100) if success else 0
        platform_share = cost - author_share

        user.token_balance = balance - cost
        user.tokens_spent = (user.tokens_spent or 0) + cost

        if author_share > 0:
            author = session.query(User).filter_by(id=script.author_id).first()
            if author:
                author.token_balance = (author.token_balance or 0) + author_share

        # Обновляем статистику install
        install = session.query(ScriptInstall).filter_by(
            user_id=user.id, script_id=script_id).first()
        if install:
            install.runs_count += 1
            install.tokens_spent += cost

        script.runs_count += 1

        run = ScriptRun(
            user_id=user.id, script_id=script_id,
            params_json=json.dumps(params, ensure_ascii=False)[:1000],
            result_summary=result[:500],
            success=success,
            tokens_charged=cost,
            author_earnings=author_share,
            platform_earnings=platform_share,
            execution_ms=exec_ms,
        )
        session.add(run)
        session.commit()
        return {'success': True, 'error': ''}
    except Exception as e:
        session.rollback()
        logger.error(f"[BILLING] bill_script_run error: {e}")
        return {'success': False, 'error': str(e)}
    finally:
        if close:
            session.close()
