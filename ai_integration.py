import aiohttp
import requests
from config import (
    DEEPSEEK_API_KEY, 
    DEEPSEEK_MODEL,
    AI_CACHE_ENABLED,
    AI_MAX_TOKENS_RESPONSE,
    AI_MAX_TOKENS_ANALYSIS,
    AI_TEMPERATURE_LOW,
    AI_TEMPERATURE_HIGH,
    ENCRYPTION_KEY
)
import json
from datetime import datetime, timezone, timedelta
import re
import logging
import asyncio
from cryptography.fernet import Fernet, InvalidToken
from models import User, UserProfile
import pytz
import hashlib
import time

cipher = Fernet(ENCRYPTION_KEY.encode())
logger = logging.getLogger(__name__)

def analyze_interaction_for_profile_update(user_id, message, ai_response):
    """
    ����������� �������������� ������������ ��� ����������� ���������� �������.
    ���������� ����������� ���������� ������� ��� None.
    """
    from models import Session, UserProfile
    import re
    
    if not user_id or not message:
        return None
    
    session = Session()
    try:
        # �������� ������� �������
        profile = session.query(UserProfile).filter_by(user_id=user_id).first()
        if not profile:
            # ������� �� ���������� - ���������� �������
            return "����� ����� �������� ����, ����� �������� �������. �������� � ����: ��� ������, ��� �����������, ����� � ���� ��������?"
        
        # ���������, ����� ���� ������� ������
        empty_fields = []
        suggestions = []
        
        if not profile.city or profile.city.strip() == "":
            empty_fields.append("city")
            # ���� ���������� ������ � ���������
            city_keywords = ["������", "�����", "���", "������������", "�����������", "������", "������ ��������", "���������", "����", "������", "������", "���", "����������", "�������", "�����", "���������"]
            for city in city_keywords:
                if city.lower() in message.lower():
                    suggestions.append(f"����, �� �������� {city.title()}. �������� � ������� ��� ���� �����?")
                    break
        
        if not profile.interests or profile.interests.strip() == "":
            empty_fields.append("interests")
            # ���� �������� � ���������
            interest_keywords = {
                "�����": ["���", "������", "����������", "�����", "����", "��������"],
                "����������������": ["���", "����������������", "python", "js", "����������", "������"],
                "�����������": ["�����������", "������", "������", "�������"],
                "������": ["������", "�������", "������", "�����"],
                "���������": ["�������", "��������", "�����", "����"],
                "������": ["�����", "������", "����������"],
                "�����": ["��������", "������", "�����", "���"]
            }
            for interest, keywords in interest_keywords.items():
                for keyword in keywords:
                    if keyword.lower() in message.lower():
                        suggestions.append(f"���� ������� � {interest}. �������� '{interest}' � ���� ��������?")
                        break
        
        if not profile.skills or profile.skills.strip() == "":
            empty_fields.append("skills")
            # ���� ������ � ���������
            skill_keywords = ["����", "����", "����", "���� �", "������� �", "����������", "�����������"]
            for keyword in skill_keywords:
                if keyword in message.lower():
                    # ��������� ����� �� ��������� - ���������� ������
                    # ���� �������� ���� "���� X", "���� Y", "������� � Z"
                    patterns = [
                        rf"{keyword}\s+(.+?)(?:\s|$|[.,!?;])",
                        rf"{keyword}\s+(.+?)(?:\s+�\s+|$|[.,!?;])",
                        rf"{keyword}\s+(.+?)(?:\s+��\s+|$|[.,!?;])"
                    ]
                    for pattern in patterns:
                        skill_match = re.search(pattern, message.lower())
                        if skill_match:
                            skill = skill_match.group(1).strip()
                            # ��������� �������� ������
                            if (len(skill) > 3 and len(skill) < 50 and 
                                not any(word in skill.lower() for word in ["���", "���", "���", "�����", "������"])):
                                suggestions.append(f"����, � ���� ���� ����� '{skill}'. �������� � �������?")
                                break
                    if suggestions and "skills" in [s.split()[-1] for s in suggestions]:
                        break
        
        if not profile.company or profile.company.strip() == "":
            empty_fields.append("company")
            # ���� ���������� �������� - ���������� ������
            company_indicators = ["������� �", "��������", "�����", "�����������", "������������"]
            for indicator in company_indicators:
                if indicator in message.lower():
                    # ���� �������� �������� ����� ����������
                    patterns = [
                        rf"{indicator}\s+(.+?)(?:\s|$|[.,!?;])",
                        rf"{indicator}\s+(.+?)(?:\s+���\s+|$|[.,!?;])",
                        rf"{indicator}\s+(.+?)(?:\s+��\s+|$|[.,!?;])"
                    ]
                    for pattern in patterns:
                        company_match = re.search(pattern, message.lower())
                        if company_match:
                            company = company_match.group(1).strip()
                            # ��������� �������� �������� ��������
                            if (len(company) > 2 and len(company) < 100 and 
                                not any(word in company.lower() for word in ["�������", "���������", "�����", "������", "����"])):
                                suggestions.append(f"����, �� ��������� � '{company}'. �������� �������� � �������?")
                                break
                    if suggestions and "�������?" in [s.split()[-1] for s in suggestions]:
                        break
        
        # ���� ���� ������ ���� � �����������, ���������� ������ ����������
        if empty_fields and suggestions:
            return suggestions[0]
        
        # ���� ������� ����� ������, �� �� �� ����� ���������� �����������
        filled_fields = 0
        if profile.city and profile.city.strip():
            filled_fields += 1
        if profile.interests and profile.interests.strip():
            filled_fields += 1
        if profile.skills and profile.skills.strip():
            filled_fields += 1
        if profile.company and profile.company.strip():
            filled_fields += 1
        
        # ���� ��� ����������� �� �������� ����, �� ������� �������� � ��������� ������� - ���������� ��
        if not suggestions and empty_fields and len(message.split()) > 5:
            ai_suggestion = analyze_with_ai(profile, message)
            if ai_suggestion:
                return ai_suggestion
        
        if filled_fields < 2 and len(message.split()) > 5:  # ������� ���������
            return "����� ����� ��������� ��� ���� ��������� � ������������, ������� �������. ��� ���� ���������� ��� ��� �� �����������?"
        
        return None
        
    except Exception as e:
        logger.error(f"Error in analyze_interaction_for_profile_update: {e}")
        return None
    finally:
        session.close()

def analyze_with_ai(profile, message):
    """
    ����������� ��������� � ������� �� ��� ����������� ���������� �������.
    """
    import requests
    
    empty_fields = []
    if not profile.city or profile.city.strip() == "":
        empty_fields.append("�����")
    if not profile.interests or profile.interests.strip() == "":
        empty_fields.append("��������")
    if not profile.skills or profile.skills.strip() == "":
        empty_fields.append("������")
    if not profile.company or profile.company.strip() == "":
        empty_fields.append("��������")
    
    if not empty_fields:
        return None
    
    prompt = f"""
    ������������� ��������� ������������ � �������� ���������� �������.
    ������ ���� �������: {', '.join(empty_fields)}
    
    ���������: "{message}"
    
    ���� � ��������� ���� ����������, ����������� � ������ �����, �������� ���������� ����������.
    ������ ������: "����, [���-��]. �������� '[��������]' � [����]?"
    ���� ������ ����������� ���, ������ ������ "None".
    
    �������:
    - ��� �������: "����, � ���� ���� ����� '���������������� �� Python'. �������� � �������?"
    - ��� ��������: "����, �� ��������� � 'Google'. �������� �������� � �������?"
    - ��� ������: "����, �� �������� '������'. �������� � ������� ��� ���� �����?"
    - ��� ���������: "���� ������� � '������'. �������� '�����' � ���� ��������?"
    """
    
    try:
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        data = {
            "model": DEEPSEEK_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 150,
            "temperature": AI_TEMPERATURE_LOW
        }
        
        response = requests.post(url, headers=headers, json=data, timeout=10)
        if response.status_code == 200:
            result = response.json()
            content = result["choices"][0]["message"]["content"].strip()
            if content and "None" not in content and len(content) > 10:
                return content
        return None
    except Exception as e:
        logger.error(f"AI analysis error: {e}")
        return None

# UNUSED FUNCTION REMOVED: extract_tasks_with_ai (never called anywhere)
# UNUSED FUNCTION REMOVED: find_partners_with_ai (never called anywhere)

# ������ ���������� ������� �������
try:
    from improved_prompts_final import (
        get_optimized_prompt_final,
        improved_classify_intent,
        improved_fallback
    )
    PROMPTS_V2_AVAILABLE = True
    logger.info("[PROMPTS V2] Loaded improved_prompts_final.py successfully")
except ImportError:
    PROMPTS_V2_AVAILABLE = False
    logger.warning("[PROMPTS V2] improved_prompts_final.py not found, using legacy prompts")

# Redis client - ����� ������������ �� main.py
redis_client = None


def set_redis_client(client):
    """��������� Redis ������� �� main.py"""
    global redis_client
    redis_client = client


def post_process_tool_calls(intent, tool_calls, message):
    """
    ����-��������� tool calls ��� ��������� ������ AI.
    ���������� ������������ tool_calls ��� None ���� ��������� �� �����.
    """
    if not tool_calls:
        return None

    corrected_calls = []

    for call in tool_calls:
        function_name = call.get("function", {}).get("name", "")
        args = call.get("function", {}).get("arguments", "{}")

        try:
            args_dict = json.loads(args) if isinstance(args, str) else args
        except:
            args_dict = {}

        # 1. ������: ���� intent ������, �� ��� list_tasks - ���������
        if intent["type"].startswith("emotion_") and function_name != "list_tasks":
            corrected_calls.append({
                "index": len(corrected_calls),
                "id": f"call_corrected_{len(corrected_calls)}",
                "type": "function",
                "function": {
                    "name": "list_tasks",
                    "arguments": "{}"
                }
            })

        # 2. ���������� �����: ���� intent add_task, �� ��� add_task - ���������
        elif intent["type"] == "add_task" and function_name != "add_task":
            # ��������� ������ �� ���������
            task_title = message
            time_indicators = ["������", "�������", "�����", "�", "��", "�", "��"]
            for indicator in time_indicators:
                if indicator in message.lower():
                    # ������� ��������� ����� ���������� �����
                    time_match = re.search(r"(\d{4}-\d{2}-\d{2} \d{1,2}:\d{2})", message)
                    if time_match:
                        args_dict["reminder_time"] = time_match.group(1)
                    else:
                        # ���� ����������� ���, ��������� ������� ������������� �����
                        relative_patterns = [
                            r"�����\s+(\d+)\s*���",
                            r"�����\s+(\d+)\s*�����",
                            r"�����\s+(\d+)\s*���",
                            r"�����\s+(\d+)\s*����",
                            r"�����\s+(\d+)\s*�����"
                        ]
                        for pattern in relative_patterns:
                            rel_match = re.search(pattern, message, re.IGNORECASE)
                            if rel_match:
                                # ��������� ��� ����� �������������� �������
                                full_match = re.search(r"(�����\s+\d+\s*(?:���|�����|���|����|�����))", message, re.IGNORECASE)
                                if full_match:
                                    args_dict["reminder_time"] = full_match.group(1)
                                break
                    break

            corrected_calls.append({
                "index": len(corrected_calls),
                "id": f"call_corrected_{len(corrected_calls)}",
                "type": "function",
                "function": {
                    "name": "add_task",
                    "arguments": json.dumps({
                        "title": task_title,
                        "reminder_time": args_dict.get("reminder_time")
                    })
                }
            })

        # 3. ����������: ���� intent complete_task, �� ��� complete_task - ���������
        elif intent["type"] == "complete_task" and function_name != "complete_task":
            task_title = intent.get("params", {}).get("task_title", "")
            if task_title:
                corrected_calls.append({
                    "index": len(corrected_calls),
                    "id": f"call_corrected_{len(corrected_calls)}",
                    "type": "function",
                    "function": {
                        "name": "complete_task",
                        "arguments": json.dumps({"title": task_title})
                    }
                })

        # 4. �������: ���� intent update_profile, �� ��� update_profile - ���������
        elif intent["type"] == "update_profile" and function_name != "update_profile":
            field = intent.get("params", {}).get("field", "interests")
            value = message
            corrected_calls.append({
                "index": len(corrected_calls),
                "id": f"call_corrected_{len(corrected_calls)}",
                "type": "function",
                "function": {
                    "name": "update_profile",
                    "arguments": json.dumps({field: value})
                }
            })

        # 5. �������������: ���� intent delegate_task, �� ��� delegate_task - ���������
        elif intent["type"] == "delegate_task" and function_name != "delegate_task":
            delegated_to = intent.get("params", {}).get("delegated_to", "")
            task_title = intent.get("params", {}).get("task_title", "")
            reminder_time = intent.get("params", {}).get("reminder_time")

            if delegated_to and task_title:
                corrected_calls.append({
                    "index": len(corrected_calls),
                    "id": f"call_corrected_{len(corrected_calls)}",
                    "type": "function",
                    "function": {
                        "name": "delegate_task",
                        "arguments": json.dumps({
                            "title": task_title,
                            "delegated_to": delegated_to,
                            "reminder_time": reminder_time
                        })
                    }
                })

        # ���� ��������� �� �����, ��������� ������������ call
        else:
            corrected_calls.append(call)

    return corrected_calls if corrected_calls != tool_calls else None


# DEPRECATED FUNCTIONS REMOVED - Use improved_prompts_final.py

def smart_fallback_handler(message, mentions_str, user_id, ai_response_content=""):
    """
    Умный fallback-обработчик: пытается выполнить действие, если AI не справился.
    Анализирует намерение пользователя и выполняет соответствующие действия напрямую.
    """
    fallback_actions = []
    
    # ����������� ��������� �����������
    greeting_words = ["������", "����������", "���", "hello", "hi", "������", "������������"]
    is_greeting = len(message.strip()) <= 20 and any(  # �������� ���������
        word in message.lower() for word in greeting_words
    )  # �������� ����� �����������

    if is_greeting and len(ai_response_content.strip()) < 50:  # ����� AI ������� ��������
        logger.info("[SMART FALLBACK] Greeting detected, enhancing response")
        # �������� ������ ����� ��� ���������� ������
        from models import Session

        db_session = Session()
        try:
            tasks_result = list_tasks(user_id=user_id, session=db_session)

            # ������� ��������� �����������, ������������ �������� � ���������� �����������
            enhanced_greeting = f"������! �������, ��� �� ����� - � ��� ���������� ������ �� ����� �����. {tasks_result}\n\n"
            
            # ��������� �������� �������� � ���������� ������������
            enhanced_greeting += "���������, ��� �� ������ ������� ��� ������� � ������ ��� � ������ ����������� - ������ ��� � ����� �����, � ������ ������������� � �������������. "
            enhanced_greeting += "� ��� � ���� ������ ����� ���������������� ��� ���������� ����������� - ����� � �������� ����������, ��������� �� ������ ��� ���������� ��� ��������. "
            enhanced_greeting += "��� ���������� �������? ����� �������� ������ ������ ��� ����� ����� ��� ���������� ���������?"

            fallback_actions.append(
                {
                    "function": "enhanced_greeting",
                    "result": enhanced_greeting,
                    "reason": "����������� ������� ��������, ������ ���������",
                }
            )
        finally:
            db_session.close()
        return fallback_actions  # ���������� �����, ��� ���������� ���������

    # ����������� ����������� AI �� ������ ������ � tool calls
    ai_confidence = 0.5  # ������� �����������

    # ���� AI ������ ������ ����� ��� ����������� ����� - ������ �����������
    if not ai_response_content or len(ai_response_content.strip()) < 10:
        ai_confidence = 0.1
    elif any(tech_word in ai_response_content.lower() for tech_word in ["error", "������", "����������", "json"]):
        ai_confidence = 0.2
    elif "�����" in ai_response_content.lower() or "������" in ai_response_content.lower():
        ai_confidence = 0.8  # AI ��� �������������� �����

    # �������������� ������: ���������, ������ �� ��� AI ������� tool calls
    from improved_prompts_final import improved_classify_intent
    intent = improved_classify_intent(message, mentions_str)
    
    # ���� ��� ������ ����������� ������� - fallback �� �����
    if intent["type"] == "conversation":
        logger.info("[SMART FALLBACK] Conversation detected, no fallback needed")
        return []
    
    should_have_tool_calls = intent["type"] in [
        "add_task",
        "complete_task",
        "delegate_task",
        "list_tasks",
        "find_partners",
        "update_profile",
        "delete_all_tasks",
        "delete_task",
        "edit_task",
        "check_subscription",
        "create_payment",
    ]

    # ���� ������ ������� �������� � AI �� ������ tool calls - ��������� fallback
    if should_have_tool_calls and intent["confidence"] >= 0.9:  # �������� ����� � 0.7 �� 0.9
        ai_confidence = 0.2  # ������������� ������ ����������� ��� fallback

    # ���� ������ ������� ��������, �� AI �� ��� �������������� ����� - ������ �����������
    if should_have_tool_calls and ai_confidence < 0.6:
        ai_confidence = 0.3
        logger.info(
            f"[SMART FALLBACK] Request requires action ({intent['type']}) but AI confidence low ({ai_confidence})"
        )

    # ���� ����������� ������ - ��������� �������-������
    if ai_confidence < 0.4:
        logger.info(
            f"[SMART FALLBACK] Applying fallback: message='{message[:50]}...', mentions='{mentions_str}', ai_response='{ai_response_content[:50]}...', intent_type='{intent['type']}', confidence={intent['confidence']}"
        )

        if intent["confidence"] >= 0.7:  # ������� ����������� � �������������
            logger.info(f"[SMART FALLBACK] Executing {intent['type']} with params: {intent['params']}")

            # ��������� ��������������� ��������
            if intent["type"] == "add_task":
                task_title = intent["params"].get("task_title", "").strip()
                reminder_time = intent["params"].get("reminder_time")
                
                # �� ������� ������, ���� ��� �������� ��� ������� ��� �����������
                if not task_title:
                    logger.info("[SMART FALLBACK] Skipping add_task: no task title provided")
                    return []  # �� ��������� fallback
                
                result = add_task(
                    title=task_title,
                    description=intent["params"].get("description", ""),
                    reminder_time=reminder_time,
                    user_id=user_id,
                )
                fallback_actions.append({"function": "add_task", "result": result, "reason": "AI �� ������ ������"})

            elif intent["type"] == "complete_task":
                result = complete_task(
                    task_id=intent["params"].get("task_id"),
                    task_title=intent["params"].get("task_title"),
                    user_id=user_id,
                )
                fallback_actions.append(
                    {"function": "complete_task", "result": result, "reason": "AI �� ������� ������ �����������"}
                )

            elif intent["type"] == "update_profile":
                print(
                    f"[DEBUG FALLBACK] Executing update_profile with city={intent['params'].get('city')}, interests={intent['params'].get('interests')}"
                )  # DEBUG
                result = update_profile(
                    city=intent["params"].get("city"), interests=intent["params"].get("interests"), user_id=user_id
                )

            elif intent["type"] == "list_tasks":
                result = list_tasks(user_id=user_id)
                fallback_actions.append(
                    {"function": "list_tasks", "result": result, "reason": "AI �� ������� ������ �����"}
                )

            elif intent["type"] == "delegate_task":
                result = delegate_task(
                    title=intent["params"].get("task_title", "������"),
                    delegated_to_username=intent["params"].get("delegated_to"),
                    reminder_time=intent["params"].get("reminder_time"),
                    user_id=user_id,
                )
                fallback_actions.append(
                    {"function": "delegate_task", "result": result, "reason": "AI �� ��������� �������������"}
                )

            elif intent["type"] == "find_partners":
                result = find_partners(user_id=user_id)
                fallback_actions.append(
                    {"function": "find_partners", "result": result, "reason": "AI �� �������� ����� ���������"}
                )

            elif intent["type"] == "delete_task":
                result = delete_task(
                    task_id=intent["params"].get("task_id"),
                    task_title=intent["params"].get("task_title"),
                    user_id=user_id,
                )
                fallback_actions.append({"function": "delete_task", "result": result, "reason": "AI �� ������ ������"})

            elif intent["type"] == "edit_task":
                result = edit_task(
                    task_id=intent["params"].get("task_id"),
                    task_title=intent["params"].get("task_title"),
                    title=intent["params"].get("title"),
                    description=intent["params"].get("description"),
                    reminder_time=intent["params"].get("reminder_time"),
                    user_id=user_id,
                )
                fallback_actions.append({"function": "edit_task", "result": result, "reason": "AI �� ������� ������"})

            elif intent["type"] == "check_subscription":
                result = check_subscription_status(user_id=user_id)
                fallback_actions.append(
                    {
                        "function": "check_subscription_status",
                        "result": result,
                        "reason": "AI �� �������� ������ ��������",
                    }
                )

            elif intent["type"] == "create_payment":
                result = create_subscription_payment(user_id=user_id)
                fallback_actions.append(
                    {"function": "create_subscription_payment", "result": result, "reason": "AI �� ������ ������"}
                )

            elif intent["type"] == "delete_task":
                result = delete_task(
                    task_id=intent["params"].get("task_id"),
                    task_title=intent["params"].get("task_title"),
                    user_id=user_id,
                )
                fallback_actions.append({"function": "delete_task", "result": result, "reason": "AI �� ������ ������"})

            elif intent["type"] == "delete_all_tasks":
                result = delete_all_tasks(user_id=user_id)
                fallback_actions.append(
                    {"function": "delete_all_tasks", "result": result, "reason": "AI �� �������� �������� �����"}
                )

    return fallback_actions


def encrypt_data(data):
    if data:
        return cipher.encrypt(data.encode()).decode()
    return data


def decrypt_data(data):
    if data is None:
        return None
    if not isinstance(data, str):
        raise ValueError("Data must be a string")
    if data:
        try:
            return cipher.decrypt(data.encode()).decode()
        except InvalidToken:
            # If decryption fails, assume it's plain text (for backward compatibility)
            return data
    return data


def determine_timezone_from_time(user_time_str, user_id):
    """���������� timezone ������������ �� ������ ���������� �������"""
    import re
    from datetime import datetime
    import pytz

    # ������ ����� �� ������ (HH:MM)
    time_match = re.search(r"(\d{1,2}):(\d{2})", user_time_str)
    if not time_match:
        return None

    user_hour = int(time_match.group(1))
    # user_minute = int(time_match.group(2))

    # ������� UTC �����
    now_utc = datetime.now(pytz.UTC)

    # ������� datetime ������ ��� ������������
    # user_now = now_utc.replace(hour=user_hour, minute=user_minute)

    # ��������� ������� � �����
    hour_diff = user_hour - now_utc.hour

    # ������������ ������� ����� �����
    if hour_diff > 12:
        hour_diff -= 24
    elif hour_diff < -12:
        hour_diff += 24

    # ���������� timezone �� ������ �������
    timezone_map = {
        -12: "Pacific/Kwajalein",  # UTC-12
        -11: "Pacific/Midway",  # UTC-11
        -10: "Pacific/Honolulu",  # UTC-10
        -9: "America/Anchorage",  # UTC-9
        -8: "America/Los_Angeles",  # UTC-8
        -7: "America/Denver",  # UTC-7
        -6: "America/Chicago",  # UTC-6
        -5: "America/New_York",  # UTC-5
        -4: "America/Halifax",  # UTC-4
        -3: "America/Sao_Paulo",  # UTC-3
        -2: "Atlantic/South_Georgia",  # UTC-2
        -1: "Atlantic/Azores",  # UTC-1
        0: "Europe/London",  # UTC+0
        1: "Europe/Paris",  # UTC+1
        2: "Europe/Kiev",  # UTC+2
        3: "Europe/Moscow",  # UTC+3
        4: "Asia/Dubai",  # UTC+4
        5: "Asia/Karachi",  # UTC+5
        6: "Asia/Dhaka",  # UTC+6
        7: "Asia/Bangkok",  # UTC+7
        8: "Asia/Shanghai",  # UTC+8
        9: "Asia/Tokyo",  # UTC+9
        10: "Australia/Sydney",  # UTC+10
        11: "Pacific/Noumea",  # UTC+11
        12: "Pacific/Auckland",  # UTC+12
    }

    # ������� ��������� timezone
    closest_diff = min(timezone_map.keys(), key=lambda x: abs(x - hour_diff))
    return timezone_map[closest_diff]


def parse_time_to_datetime(time_text, user_id):
    """������ ����� �� ������ ������������"""
    import re
    from datetime import datetime, timedelta
    import pytz
    from models import Session, User

    # �������� timezone ������������
    session = Session()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    user_tz = pytz.timezone(user.timezone) if user and user.timezone else pytz.UTC
    session.close()
    now = datetime.now(user_tz)

    time_text = time_text.lower().strip()

    # ��������� "����� X �����/�����"
    through_time_match = re.search(r"�����\s+(\d+)\s+(�����|���)", time_text)
    if through_time_match:
        amount = int(through_time_match.group(1))
        unit = through_time_match.group(2).lower()

        if "�����" in unit:
            target_dt = now + timedelta(minutes=amount)
        else:  # ���/�����
            target_dt = now + timedelta(hours=amount)

        return target_dt.strftime("%Y-%m-%d %H:%M")

    # ��������� "������/������� � XX:XX"
    time_match = re.search(r"(������|�����������|�������)\s+(?:�\s+)?(\d{1,2}):(\d{2})", time_text)
    if time_match:
        day_word = time_match.group(1).lower()
        hour = int(time_match.group(2))
        minute = int(time_match.group(3))

        if "������" in day_word:
            target_date = (now + timedelta(days=1)).date()
        elif "�����������" in day_word:
            target_date = (now + timedelta(days=2)).date()
        else:
            target_date = now.date()

        target_dt = datetime.combine(target_date, datetime.min.time().replace(hour=hour, minute=minute))
        target_dt = user_tz.localize(target_dt)
        return target_dt.strftime("%Y-%m-%d %H:%M")

    # ��������� ������ "� HH:MM"
    simple_time_match = re.search(r"(?:�\s+)?(\d{1,2}):(\d{2})", time_text)
    if simple_time_match:
        hour = int(simple_time_match.group(1))
        minute = int(simple_time_match.group(2))

        # ���� ����� ��� ������ ������� - ������ �� ������
        target_time = datetime.min.time().replace(hour=hour, minute=minute)
        if target_time <= now.time():
            target_date = (now + timedelta(days=1)).date()
        else:
            target_date = now.date()

        target_dt = datetime.combine(target_date, target_time)
        target_dt = user_tz.localize(target_dt)
        return target_dt.strftime("%Y-%m-%d %H:%M")

    # ��������� "�����", "�������", "����"
    time_word_match = re.search(r"(�����|�������|����)", time_text)
    if time_word_match:
        time_word = time_word_match.group(1).lower()
        if "�����" in time_word:
            hour, minute = 8, 0
        elif "�������" in time_word:
            hour, minute = 18, 0
        elif "����" in time_word:
            hour, minute = 12, 0

        target_time = datetime.min.time().replace(hour=hour, minute=minute)
        # ���� ����� ��� ������ ������� - ������ �� ������
        if target_time <= now.time():
            target_date = (now + timedelta(days=1)).date()
        else:
            target_date = now.date()

        target_dt = datetime.combine(target_date, target_time)
        target_dt = user_tz.localize(target_dt)
        return target_dt.strftime("%Y-%m-%d %H:%M")

    return None


def replace_placeholders(content, user_now=None, current_time_str=None):
    """�������� ������������ ���� {{current_time}} �� �������� ��������"""
    if content is None:
        return ""
    if not isinstance(content, str):
        raise ValueError("Content must be a string")

    if not user_now:
        user_now = datetime.now(pytz.UTC)
    if not current_time_str:
        current_time_str = user_now.strftime("%H:%M")

    # ����������� ���� ��-������
    months = [
        "������",
        "�������",
        "�����",
        "������",
        "���",
        "����",
        "����",
        "�������",
        "��������",
        "�������",
        "������",
        "�������",
    ]
    current_date_str = f"{user_now.day} {months[user_now.month - 1]} {user_now.year}"

    content = content.replace("{{current_time}}", current_time_str)
    content = content.replace("{{current_date}}", current_date_str)
    content = content.replace("{{tomorrow}}", (user_now + timedelta(days=1)).strftime("%Y-%m-%d"))
    content = content.replace("{{day_after}}", (user_now + timedelta(days=2)).strftime("%Y-%m-%d"))

    return content


class AIIntegration:
    async def generate_reminder(self, user_id, task_title):
        return await generate_reminder(user_id, task_title)

    async def generate_result_check(self, user_id, task_title):
        return await generate_result_check(user_id, task_title)

    async def generate_proactive_message(self, user_id):
        return await generate_proactive_message(user_id)

    async def generate_daily_report(self, user_id):
        return generate_daily_report(user_id)

    async def generate_overdue_reminder(self, user_id, overdue_tasks):
        return generate_overdue_reminder(user_id, overdue_tasks)


def validate_response_compliance(response_text, intent_type=None):
    """
    ��������� ������������ ������ �������� �������� �������
    ���������� (is_compliant, issues_list)
    """
    issues = []

    # �������� �� ����������� �������� (����� list_tasks)
    if intent_type != "list_tasks":
        # ����������� ����������� ������
        forbidden_emojis = ["??", "?", "??", "??", "??", "??", "??", "?", "??", "??", "??", "??", "??", "???"]
        if any(emoji in response_text for emoji in forbidden_emojis):
            issues.append("������������ ����������� ����������� ������")
        
        # ��������� 1-2 ���������� ������ ��� �������
        allowed_emojis = ["??", "??", "?", "??", "??", "??", "??", "??", "??", "??"]
        emoji_count = sum(1 for emoji in allowed_emojis if emoji in response_text)
        if emoji_count > 2:
            issues.append("������ 2 ����������� ������ � ���������")
            
        if "**" in response_text:
            issues.append("������������ ������ �����")

    if re.search(r"^\s*[-�*]\s+", response_text, re.MULTILINE) and intent_type != "list_tasks":
        issues.append("������������ ������������� ������")

    if re.search(r"^\s*\d+\.\s+", response_text, re.MULTILINE):
        issues.append("������������ ���������")

    # �������� �� ����������� ����� ������ ��� ���������� �������� � ��������
    # ������ ����� �������� �� �������� ������ - AI ������ ������������ ����� ��� ��������
    
    # ������������� �������� ��� ������ ����� intent - ���������� �������
    if intent_type == "list_tasks":
        # ��� ��������� ����� - ��������� ������, �� �� ������� �������
        if len(response_text) > 800:
            issues.append("����� �� list_tasks ������� �������")
        if len(response_text) < 100:
            issues.append("����� �� list_tasks ������� �������� ��� �������")
        if "���� ������:" in response_text or "������ �����:" in response_text:
            issues.append("��������� ����� ������ �������")

    return len(issues) == 0, issues


async def enforce_prompt_compliance(response_text, intent_type, user_id, context, system_prompt, messages, url, headers):
    """
    ���������� AI ��������� ������� �������� ������� ����� ��������� �������
    """
    max_attempts = 2
    original_response = response_text

    for attempt in range(max_attempts):
        is_compliant, issues = validate_response_compliance(response_text, intent_type)

        if is_compliant:
            return response_text

        logger.warning(f"[COMPLIANCE] Response not compliant (attempt {attempt + 1}): {issues}")

        # ������� �������������� ������
        correction_prompt = f"""���� ���������� ����� �� ������������� �������� �������� �������:

��������:
{chr(10).join(f"- {issue}" for issue in issues)}

������ ���������:
- ������ ����������� ����������� ������ (?? ? ?? ?? ?? ?? ?? ? ?? ??), �� ����� �������� 1-2 ���������� (?? ?? ? ?? ?? ??)
- ������ ������ �����, ������, ��������� (����� list_tasks)
- ������������ ����� ������ ��� ��������: �������� ��� ������� ��������, ��������� ��� �������
- ��� add_task �������� 1-2 ������� ������ (�������� 1-2 �����������), ��� ������������ �������, ����� � ��������
- ������ ��������� ������� ��� ���������� ������������
- ������������ ������������ ����������� �����
- ��������� �������� ��� ����������� �������

�������� ����� ���������:"""

        # ��������� �������������� ������ � ����������
        correction_messages = messages.copy()
        correction_messages.append({"role": "assistant", "content": original_response})
        correction_messages.append({"role": "user", "content": correction_prompt})

        try:
            correction_data = {
                "model": "deepseek-reasoner",
                "messages": correction_messages,
                "temperature": 0.1,  # ����� ����������������� ��� �����������
            }

            async with aiohttp.ClientSession() as correction_session:
                async with correction_session.post(
                    url, headers=headers, json=correction_data, timeout=aiohttp.ClientTimeout(total=30)
                ) as correction_response:
                    if correction_response.status == 200:
                        correction_result = await correction_response.json()
                        corrected_response = correction_result["choices"][0]["message"]["content"]
                        response_text = corrected_response
                        logger.info(f"[COMPLIANCE] Corrected response (attempt {attempt + 1})")
                    else:
                        logger.error(f"[COMPLIANCE] Correction API error: {correction_response.status}")
                        break

        except Exception as e:
            logger.error(f"[COMPLIANCE] Error during correction: {e}")
            break

    return response_text


def analyze_user_context_for_advice(user_id, message, context=None):
    """
    �������� ������ ��������� ������������ ��� ��������� ������������������� �������.
    ���������� ����������������� ������ ��� ������������� � �������.
    """
    from models import Session, User, UserProfile, Task
    from datetime import datetime, timedelta
    import pytz

    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return {"error": "������������ �� ������"}

        analysis = {
            "profile": {},
            "tasks": {},
            "patterns": {},
            "context_insights": {},
            "recommendations": {}
        }

        # 1. ������ �������
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if profile:
            analysis["profile"] = {
                "city": profile.city or "�� ������",
                "company": profile.company or "�� �������",
                "position": profile.position or "�� �������",
                "bio": profile.bio or "�� �������",
                "languages": profile.languages or "�� �������",
                "skills": profile.skills or "�� �������",
                "interests": profile.interests or "�� �������",
                "goals": profile.goals or "�� �������",
                "filled_fields": sum([1 for field in [profile.city, profile.company, profile.position, profile.bio, profile.languages, profile.skills, profile.interests, profile.goals] if field])
            }

        # 2. ������ �����
        all_tasks = session.query(Task).filter_by(user_id=user.id).all()
        pending_tasks = [t for t in all_tasks if t.status == "pending"]
        completed_tasks = [t for t in all_tasks if t.status == "completed"]

        analysis["tasks"] = {
            "total": len(all_tasks),
            "pending": len(pending_tasks),
            "completed": len(completed_tasks),
            "completion_rate": len(completed_tasks) / max(len(all_tasks), 1),
            "overdue": len([t for t in pending_tasks if t.reminder_time and (t.reminder_time.replace(tzinfo=pytz.UTC) if t.reminder_time.tzinfo is None else t.reminder_time) < datetime.now(pytz.UTC)]),
            "delegated": len([t for t in all_tasks if t.delegated_to_username])
        }

        # 3. ������ ���������
        # ������ ��� �����
        task_titles = [t.title.lower() for t in all_tasks]
        themes = {
            "development": sum(1 for title in task_titles if any(word in title for word in ["����������", "���", "����������������", "dev", "backend", "frontend"])),
            "meetings": sum(1 for title in task_titles if any(word in title for word in ["�������", "���������", "������", "meeting"])),
            "documents": sum(1 for title in task_titles if any(word in title for word in ["��������", "�����", "�����������", "������������"])),
            "communication": sum(1 for title in task_titles if any(word in title for word in ["������", "���������", "��������", "��������"])),
            "learning": sum(1 for title in task_titles if any(word in title for word in ["�������", "�������", "����", "�������"])),
            "business": sum(1 for title in task_titles if any(word in title for word in ["��������", "�������", "������", "�������", "������"]))
        }

        analysis["patterns"] = {
            "main_themes": sorted(themes.items(), key=lambda x: x[1], reverse=True)[:3],
            "task_frequency": len(all_tasks) / max((datetime.now() - user.created_at.replace(tzinfo=None)).days, 1),
            "delegation_ratio": len([t for t in all_tasks if t.delegated_to_username]) / max(len(all_tasks), 1),
            "overdue_ratio": analysis["tasks"]["overdue"] / max(analysis["tasks"]["pending"], 1)
        }

        # 4. ������ ��������� ���������
        message_lower = message.lower()
        analysis["context_insights"] = {
            "urgency_level": "high" if any(word in message_lower for word in ["������", "�������", "������", "�������", "����������"]) else "normal",
            "emotional_state": "stressed" if any(word in message_lower for word in ["������", "��������", "��������", "�������", "������"]) else
                            "motivated" if any(word in message_lower for word in ["����", "�������������", "�����", "����������"]) else "neutral",
            "request_type": "advice" if any(word in message_lower for word in ["���", "��� ������", "�����", "������"]) else
                          "action" if any(word in message_lower for word in ["������", "������", "�����", "������"]) else "info"
        }

        # 5. ������������������� ������������
        recommendations = []

        # �� ������ �������
        if analysis["profile"].get("skills") and "python" in analysis["profile"]["skills"].lower():
            recommendations.append("������������ Python-���������� ��� ������������� �������� �����")

        if analysis["profile"].get("company") and "tech" in analysis["profile"]["company"].lower():
            recommendations.append("�������� agile-����������� � ��������� ������")

        # �� ������ ��������� �����
        if analysis["patterns"]["overdue_ratio"] > 0.3:
            recommendations.append("�������� ������� ������������� ����� (Eisenhower matrix)")

        if analysis["patterns"]["delegation_ratio"] < 0.1:
            recommendations.append("������ ������������ �������� ������ ��� ������ �� ��������������")

        # �� ������ ���
        main_theme = analysis["patterns"]["main_themes"][0][0] if analysis["patterns"]["main_themes"] else None
        if main_theme == "development":
            recommendations.append("�������� code review ������� � ������������������ ������������")
        elif main_theme == "business":
            recommendations.append("������� ������� ������������ ������ ������� � ���������� ������")

        analysis["recommendations"] = recommendations[:5]  # ���������� �� 5 ������������

        return analysis

    finally:
        session.close()


def clean_technical_details(text):
    if text is None:
        return ""
    if not isinstance(text, str):
        raise ValueError("Text must be a string")

    import logging

    logger = logging.getLogger(__name__)
    original_text = text
    import re

    # ������� ������ ������� � ���������� �������: [add_task(...)]
    before = text
    text = re.sub(r"\[[\w_]+\([^]]*\)\]", "", text)
    if before != text:
        pass

    # ������� ������ ���������� ������
    before = text
    text = re.sub(r"\[\s*\]", "", text)
    if before != text:
        pass

    # ������� �������� ������� (� �������� � ���)
    before = text
    text = re.sub(
        r"\b(list_tasks|add_task|delete_task|complete_task|delegate_task|update_profile|find_partners|update_user_memory|set_reminder|edit_task|get_task_details)(\s*\(\s*\))?",
        "",
        text,
        flags=re.IGNORECASE,
    )
    if before != text:
        pass

    # ������� ����� � ������ �������
    patterns_to_remove = [
        r"�������\s+\w+(\(\))?",
        r"������\s+\w+(\(\))?",
        r"������\s+������",
        r"����\s+��������",
        r"Args for.*?(?=\n|$)",
        r"??\s*����������� �������:.*?(?=\n\n|\Z)",
        r"??\s*\*\*��������:\*\*.*?(?=\n|$)",
        r"??\s*\*\*���������:\*\*.*?(?=\n\n|\Z)",
        r"����������� �������.*?(?=\n\n|\Z)",
    ]

    for pattern in patterns_to_remove:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE | re.DOTALL)

    # ������� ����� ���� Python - ������ ���� ��� �������� ����������� ����������
    # �� ������� json �����, ������� ����� ��������� �������� ������
    text = re.sub(r"```python.*?```", "", text, flags=re.DOTALL)
    # ������� ������ ����� ����
    text = re.sub(r"```\s*```", "", text)

    # ���������� �����: ������� JSON ����� � tool_calls - ��� �� ������ �������� � ����� ������������
    # ������� ������ JSON ����� � tool_calls
    text = re.sub(r'```json\s*\{[^}]*"tool_calls"[^}]*\}```', "", text, flags=re.DOTALL)
    text = re.sub(r"```json.*?tool_calls.*?(```|$)", "", text, flags=re.DOTALL | re.IGNORECASE)
    # ������� ����� ���������� JSON ����� � tool_calls
    text = re.sub(r'\{[^}]*"tool_calls"[^}]*\}', "", text, flags=re.DOTALL)
    text = re.sub(r'"tool_calls"\s*:\s*\[.*?\]', "", text, flags=re.DOTALL)
    # ������� ����� JSON ����� � ������� ������, ���� ��� �������� tool_calls
    text = re.sub(r"```json[\s\S]*?tool_calls[\s\S]*?```", "", text, flags=re.IGNORECASE)
    # ������� ����� ���������� ```json �����
    text = re.sub(r"```json[\s\S]*?```", "", text, flags=re.IGNORECASE)

    # ������� ������ - ������ �����������, ��������� ���������� ��� �������
    # (AI ������ ����� ������������ 1-2 ���������� ������ �������� �������)
    # ������� ������ ����������� ������, ������� ����� ������
    technical_emojis = ['??', '?', '??', '??', '??', '??', '??', '?', '??', '??', '??', '??', '??', '??', '???']
    for emoji in technical_emojis:
        text = text.replace(emoji, '')

    # ����������� ��������: ���� ����� ������� ������ �� ��������,
    # ������ AI ������ ������ ����������� ������, ������� ��������
    if not text.strip():
        logger.warning(f"[CLEAN] Content was completely cleaned, returning original: '{original_text}'")
        return original_text.strip()

    if original_text != text:
        logger.warning(f"[CLEAN] Original: '{original_text[:100]}...' -> Cleaned: '{text[:100]}...'")

    return text.strip()


# Alias for backward compatibility
clean_content = clean_technical_details


def enrich_response_with_engagement(content, user_id=None, original_message=""):
    """
    ������������� ��������� �������� ������ ������������ ����������:
    - �������
    - ������������
    - ����������� ��������
    �������� �����������, ��� ��������� ���� - ������ ��������� ����� ������ � ��������
    """
    # ��������� ����� ������ (� ������������)
    sentences = [s.strip() for s in re.split(r"[.!?]+", content) if s.strip()]

    # ���� ����� ���������� ���������� (3+ �����������) ��� ��� �������� ������ - �� �������
    if len(sentences) >= 3 or "?" in content:
        return content

    # ��������� ����� ���������� ������ ��� ����� �������� ������� (1-2 �����������)
    # AI ��� ������ ������������ ����������� �������, �� ������ �����������������
    import random

    # ��������������� ��������, ������� �� �����������
    minimal_engagement = [" ��� ������?", " ��� ��� ������?", " ����� �����?"]

    # ������ ��� ����� �������� ������� (1 �����������)
    if len(sentences) <= 1:
        enrichment = random.choice(minimal_engagement)
        return content + enrichment

    return content


def get_optimized_system_prompt():
    """���������������� ������ v12 - ��������� ������"""
    return """�� - ������ ��-�������� � ���� ��� ���������� ������. ���� �����, ������������ ������ ��� ��������� �������.

================================================================================
������� ������� �������������� (�������� ����������):
================================================================================

? ����������� �������� (������� �� ������������):
- ������ �����: **�����**
- ������������ ������: 1. 2. 3. ��� 1) 2) 3)
- ������������� ������: � - *
- ���������: ## ###
- ����������� ������: ?? ? ?? ?? ?? ?? ?? ? ?? ?? ?? ?? ??

? ����������� ��������:
- ������� ����� ��� ��������������
- ����������� �����
- ������������ �������
- �������� ������ � ������� (�� ����� 2-3 ����)
- 1-2 ���������� ������ � ��������� (������ ����������: ?? ?? ? ?? ?? ??)

================================================================================
������� ����������� ��������� (�������� �� ���):
================================================================================

1. ������ - ���������:
������������: "� ��� ����� �� ���� ���� �����"
���������� �����: ������� ������� list_tasks(), ����� ���������������� ��������, ���������� ���� ������, ������ 3-4 �������.

2. ���������� �����:
������������: "������� ��� ��������� ������� ������ � 15:00"
���������� �����: ����� ������� add_task() � �����������, ��������� ��������, ������ ���������� �������.

3. ���������� �����:
������������: "� �������� ������ �� ������"
���������� �����: ������� complete_task(), ���������, ���������������� ��������, ���������� ��������� ������.

4. �������:
������������: "� ���� ��������������� �� python"
���������� �����: ������� update_profile() ��� ���������� ������, ��������� ������, ������ ������� � �������������.

5. �������������:
������������: "@testuser ������� ��� ������ � 10:00"
���������� �����: ������� delegate_task(), ��������� ������ �������������, ������ ������� � ����������.

================================================================================
��������� ������� (�������� ������):
================================================================================

?? ����� ����� ("������ ������" / "��� ������" / "������"):
   1. �������: list_tasks() - ������ ��� ������
   2. ������: ����� ��������, ����������, ��������
   3. ������������: ���������� ������ �� �����������
   4. �������: ������ ��� ���������� � �����

?? ���������� ������ ("������ ������" / "�������" / "�������"):
   1. ���������: ������ + ����� (���� ����)
   2. �����: add_task(title, reminder_time) - ������ ����������
   3. �������: ������ ��� ������ �����, ��� �������� � ����
   4. �������: ������ ��� ������, ���������, ��������� ������

?? ���������� ������ ("��������" / "������" / "������"):
   1. ���������: ������ �������� ������ �� ���������
   2. �����: complete_task(task_title) - ��������� ������ �������� ������
   3. �������: �������� �������� � �����������
   4. ������: ������ ��������, ��� ����������
   5. ��������� ���: ��������, ��� ������ ������

?? ������������� (@username � ���������):
   1. �����: delegate_task(title, delegated_to_username, reminder_time)
   2. �������: ������ ������������� �������, ��������� �����
   3. �����: ����� ������ ����� ��������
   4. �������: ������ ��� ������ ��� ��������

?? ������� (�����/��������/��������/������/����):
   1. ��� ������/��������: update_profile() �����
   2. ��� ���������/�������: update_profile() ����� + ������� ������
   3. ��� �����: update_profile() + �������� ���������� ����
   4. �������: ������ ��� ������ �������

?? ���������� ������� ("������ ���" / ����������� / �����):
   1. �� �������: list_tasks() - �� ����� ��������
   2. ��������: �������� 3-4 �������� ��� ������������ ��� ����� � ����
   3. ������: ����� ���������� ������� ��� ���������
   4. ������: �������� ������ � ����������� ����������

================================================================================
����� ������� - ����� �������:
================================================================================

? ��� ����� �������:
- ��������� ����������� �����: "�, ����!", "�������", "�������!", "�������!"
- ���� �������������: ������� �������, ����������� ����������
- ������ ������������: "�������, ���...", "���������, ���..."
- ����� ������: "���������...", "����� ����...", "��������..."

? ��������� ������:
1. ������������� ������� (1-2 �����������)
2. �������� � ������������ (���� �����)
3. �������� ������ �������� (2-3 ������)
4. ���������� ������������ � ������������
5. 3-4 ����� ������� ��� ����������� �������

? �����: 80-150 ���� (�� ������!) - ����������� ��������
? �������: ������ 3-4 ������� � ����� - ����� ������ �����

? �� �����:
- �������� ����� ������
- ������ "���������" ��� "������"
- ������ ��� ������� � ��������
- ���������� ��� ��� ������

================================================================================
������� ����������� ���������:
================================================================================

? �����: "������� ������. ������."
? ������: "�������! ������� ������ '�������� �����' �� ������ � 15:00. ��� ������ ������, ��� ��� ����� ����� ��� ������� � ��������. ����, ��� � ���� ��� ���� ��������� ������� ��� - ����� ����� ���������� �����, ����������� ���-�� ����� ������? ������, ����� ������ ������ ����� - ����������� ��� ��������? � ���� �� � ���� ��� ���������� ��� ����?"

? �����: "��� ���� ������: [������]"
? ������: "��������� ���� ������ - �� ������ 5, � � ������� ���������� �������. � ���� ����� ���������������� ����� (3 �� 5), � ���� ������ ������������. ��� ������� � ���, ��� �� ������� ��������� � ������. ���������� ������������� ��� ������ �� ���� ���� ������� - ��� ����� �����������. ����� ������� ������ - '��������� �������', ������� ����� 2 ����. ������ � �����? ��� ���� ���-�� ����� ������������?"

? �����: "�� �����, ��� �� ������ � ����."
? ������: "��, �� ������ �����, ��� �� ������ �������. ���� ��������� ���������: 1) �������� ����� ������, 2) ���������� ������� ������, 3) ��������� �����-�� ������, 4) ����� ����� ��� �������. ��� �� ����� ����� �����? ��� �� ���� � ���� ���-�� ������?"

================================================================================
������� (��������� ����������):
================================================================================

list_tasks() - �������� ������ + ������
add_task(title, reminder_time) - �������� ������ + ������
  ������� reminder_time:
  - "����� 5 �����" (����������� ��������� ��� ����!)
  - "����� 2 ����" (����������� ��������� ��� ����!)
  - "������ � 10:00"
  - "2026-01-13 15:30"
complete_task(task_title) - ��������� + ������������
delegate_task(title, delegated_to_username, reminder_time) - ������������ + ������
find_partners() - ����� ����� + ������������
update_profile(city, company, interests, skills, goals) - �������� �������

================================================================================
����: ���� ����� ������ � ����������, � �� �������
================================================================================"""


# DEPRECATED get_system_prompt REMOVED - Use get_optimized_prompt_final from improved_prompts_final.py


def get_extended_system_prompt(user_now, current_time_str, user_username, mentions_str, user_memory, context=None, intent=None):
    from improved_prompts_final import get_optimized_prompt_final
    return get_optimized_prompt_final(user_now, current_time_str, user_username, mentions_str, user_memory)
    

def parse_relative_time(message, current_time):
    """Parse relative time expressions like '����� 5 �����', '����� 2 ����' and return datetime.
    
    Args:
        message: String containing relative time expression
        current_time: Current datetime in user's local timezone (not UTC!)
    
    Returns:
        Datetime object in the same timezone as current_time, or None if parsing failed
    """
    from datetime import datetime, timedelta
    import re

    if not message or not isinstance(message, str):
        raise ValueError("Message must be a non-empty string")
    if not current_time or not isinstance(current_time, datetime):
        raise ValueError("Current time must be a datetime object")

    # Patterns for Russian time expressions
    patterns = [
        (r"�����\s+(\d+)\s*���", lambda m: timedelta(minutes=int(m.group(1)))),
        (r"�����\s+(\d+)\s*�����", lambda m: timedelta(minutes=int(m.group(1)))),
        (r"�����\s+(\d+)\s*���", lambda m: timedelta(hours=int(m.group(1)))),
        (r"�����\s+(\d+)\s*����", lambda m: timedelta(hours=int(m.group(1)))),
        (r"�����\s+(\d+)\s*�����", lambda m: timedelta(hours=int(m.group(1)))),
    ]

    for pattern, delta_func in patterns:
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            delta = delta_func(match)
            # ���������� ����� � ��� �� timezone ��� � current_time
            return current_time + delta

    return None


def parse_absolute_time(message):
    """Parse absolute time expressions like '������ 12:18', '����� 15:30' and return HH:MM"""
    if not message or not isinstance(message, str):
        raise ValueError("Message must be a non-empty string")

    import re

    # Patterns for absolute time
    patterns = [
        r"������\s+(\d{1,2}):(\d{2})",
        r"�����\s+(\d{1,2}):(\d{2})",
        r"(\d{1,2}):(\d{2})",  # Just HH:MM
    ]

    for pattern in patterns:
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            hours = int(match.group(1))
            minutes = int(match.group(2))
            if 0 <= hours <= 23 and 0 <= minutes <= 59:
                return f"{hours:02d}:{minutes:02d}"

    return None


def parse_tool_arguments(arguments_str):
    """Parse tool arguments from string, fallback to empty dict if parsing fails"""
    if arguments_str is None:
        return {}
    if not isinstance(arguments_str, str):
        raise ValueError("Arguments must be a string")

    try:
        return json.loads(arguments_str)
    except (json.JSONDecodeError, ValueError):
        return {}


def generate_task_recommendations(title, description, user_id):
    """���������� 2-3 ������� ������������ ��� ������ (��� ������ ����������)"""
    try:
        import requests
        from config import DEEPSEEK_API_KEY
        
        prompt = f"""������������� ������ � ��� 2-3 ������� ������������ (�������� 3-4 �����).

������: {title}

������: ������ ���������� ��������, ��� ������ ����.

�������:
- ��������� ������ �������
- �������� ���� ��������
- ��������� ���������"""

        response = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "deepseek-reasoner",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 150,
                "temperature": 0.5
            },
            timeout=8
        )
        
        if response.status_code == 200:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            
            # ������ ������������
            recommendations = []
            for line in content.split('\n'):
                line = line.strip()
                if line.startswith('-') or line.startswith('�'):
                    rec = line.lstrip('-�').strip()
                    if rec and len(rec) <= 50:  # �������� 50 ��������
                        recommendations.append(rec)
            
            return recommendations[:3]  # �������� 3 ������������
        else:
            return []
    except Exception as e:
        import logging
        logging.warning(f"Error generating recommendations: {e}")
        return []


def add_task(title, description="", reminder_time=None, due_date=None, user_id=None, session=None):
    import logging

    logger = logging.getLogger(__name__)
    logger.info(f"[ADD_TASK] Called with title='{title}', user_id={user_id}, reminder_time={reminder_time}")
    from models import Session, Task, User
    from datetime import datetime
    import pytz

    if session is None:
        session = Session()
        close_session = True
        logger.info("[ADD_TASK] Created new session")
    else:
        close_session = False
        logger.info("[ADD_TASK] Using provided session")
    # ���������, ���������� �� ������������
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        user = User(telegram_id=user_id)
        session.add(user)
        session.commit()

    # ���������, ���������� �� ������ � ����� �� ���������
    existing_task = session.query(Task).filter_by(user_id=user.id, title=title).first()
    if existing_task:
        # �������� ������������ ������
        if reminder_time:
            try:
                user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
                local_dt = datetime.strptime(reminder_time, "%Y-%m-%d %H:%M")
                local_dt = user_tz.localize(local_dt)
                existing_task.reminder_time = local_dt.astimezone(pytz.UTC)
            except ValueError:
                pass
        if description:
            existing_task.description = encrypt_data(description)
        session.commit()
        task_id = existing_task.id
        task = existing_task  # ��� ����������� �������������
    else:
        # ������� ����� ������
        task = Task(user_id=user.id, title=title, description=encrypt_data(description))
        if reminder_time:
            try:
                # �������� timezone ������������
                user_tz = pytz.UTC
                if user.timezone:
                    try:
                        user_tz = pytz.timezone(user.timezone)
                    except pytz.exceptions.UnknownTimeZoneError:
                        import logging
                        logging.warning(f"Unknown timezone {user.timezone}, using UTC")
                        user_tz = pytz.UTC
                
                # ���������, �������� �� ����� �������������
                if "�����" in reminder_time.lower():
                    # ������������ parse_relative_time ��� �������������� �������
                    # �����: ���������� ��������� ����� ������������, �� UTC!
                    current_time = datetime.now(user_tz)
                    parsed_time = parse_relative_time(reminder_time, current_time)
                    if parsed_time:
                        # parsed_time ��� � ��������� �������, ������������ � UTC ��� ��������
                        if parsed_time.tzinfo is None:
                            parsed_time = user_tz.localize(parsed_time)
                        task.reminder_time = parsed_time.astimezone(pytz.UTC)
                        import logging
                        logging.info(f"Task {title} relative time parsed: '{reminder_time}' -> local: {parsed_time} -> UTC: {task.reminder_time}")
                    else:
                        # ���� �� ������� ����������, ������������
                        pass
                else:
                    # ������� ��� ���������� �����
                    local_dt = datetime.strptime(reminder_time, "%Y-%m-%d %H:%M")
                    # ������������ � timezone ������������
                    local_dt = user_tz.localize(local_dt)
                    # �������������� � UTC ��� ��������
                    task.reminder_time = local_dt.astimezone(pytz.UTC)
                    import logging
                    logging.info(f"Task {title} absolute time parsed: {reminder_time} -> local: {local_dt} -> UTC: {task.reminder_time}")
            except ValueError:
                pass  # ������������ �������� ������
        if due_date:
            try:
                user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
                local_dt = datetime.strptime(due_date, "%Y-%m-%d %H:%M")
                local_dt = user_tz.localize(local_dt)
                task.due_date = local_dt.astimezone(pytz.UTC)
            except ValueError:
                pass
        session.add(task)
        
        # ���������� ������������ ��� ������
        try:
            import logging
            logger = logging.getLogger(__name__)
            logger.info(f"[ADD_TASK] Generating recommendations for task '{title}'")
            recommendations = generate_task_recommendations(title, description, user.telegram_id)
            logger.info(f"[ADD_TASK] Generated {len(recommendations) if recommendations else 0} recommendations")
            if recommendations:
                import json
                task.recommendations = json.dumps(recommendations, ensure_ascii=False)
                logger.info(f"[ADD_TASK] Saved recommendations to task: {task.recommendations}")
        except Exception as e:
            import logging
            logging.warning(f"Could not generate recommendations for task {title}: {e}")
        
        session.commit()
        task_id = task.id

    # ����������� ����������� ���� ������� reminder_time
    if task.reminder_time:
        try:
            from main import reminder_service

            if reminder_service:
                reminder_service.schedule_reminder(
                    task_id=task.id, reminder_time=task.reminder_time, user_id=user.telegram_id, task_title=task.title
                )
        except Exception as e:
            import logging

            logging.warning(f"Could not schedule reminder for task {task_id} (scheduler may not be running yet): {e}")

    # �������� ��������� �������
    profile = session.query(UserProfile).filter_by(user_id=user.id).first()
    if profile:
        profile.total_tasks_created = (profile.total_tasks_created or 0) + 1
        session.commit()

    # ��������� ��������� ����� � ID ��� edit_task
    result_msg = f"��������� ������ '{title}' (ID: {task_id})"
    if task.reminder_time:
        # ���������� ����� � timezone ������������
        user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
        local_time = task.reminder_time.astimezone(user_tz)
        result_msg += f" � ������������ �� {local_time.strftime('%d.%m.%Y %H:%M')}"

    if close_session:
        session.close()
        logger.info(f"[ADD_TASK] Closed session, returning: {result_msg}")
    else:
        logger.info(f"[ADD_TASK] Session not closed, returning: {result_msg}")
    return result_msg


def delete_task(task_id=None, task_title=None, user_id=None, session=None):
    """Delete a specific task by ID or title"""
    from models import Session, Task, User

    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False

    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            if close_session:
                session.close()
            return "������������ �� ������."

        task = None
        if task_id:
            try:
                task_id_int = int(task_id)
                task = session.query(Task).filter(Task.id == task_id_int, Task.user_id == user.id).first()
            except (ValueError, TypeError):
                pass

        if not task and task_title:
            # Try to find by title (case-insensitive partial match)
            task = session.query(Task).filter(Task.user_id == user.id, Task.title.ilike(f"%{task_title}%")).first()

        if not task:
            if close_session:
                session.close()
            return "������ �� �������."

        # Delete the task
        session.delete(task)
        session.commit()

        # Update profile analytics
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if profile and profile.total_tasks_created:
            profile.total_tasks_created = max(0, (profile.total_tasks_created or 0) - 1)
            session.commit()

        if close_session:
            session.close()
        return f"������ '{task.title}' �������."

    except Exception as e:
        if close_session:
            session.close()
        return f"������ �������� ������: {str(e)}"


def delete_all_tasks(user_id=None, session=None):
    """Delete all tasks for a user"""
    from models import Session, Task, User, UserProfile

    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False

    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            if close_session:
                session.close()
            return "������������ �� ������."

        # Count tasks before deletion
        task_count = session.query(Task).filter_by(user_id=user.id).count()

        # Delete all tasks
        session.query(Task).filter_by(user_id=user.id).delete()
        session.commit()

        # Reset profile analytics
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if profile:
            profile.total_tasks_created = 0
            profile.completed_tasks = 0
            profile.skipped_tasks = 0
            session.commit()

        if close_session:
            session.close()
        return f"������� {task_count} �����."

    except Exception as e:
        if close_session:
            session.close()
        return f"������ �������� �����: {str(e)}"


def complete_task(task_id=None, task_title=None, user_id=None, session=None):
    from models import Session, Task, UserProfile, Interaction
    from datetime import datetime
    from sqlalchemy import or_

    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        if close_session:
            session.close()
        return "������������ �� ������."

    # ����� ������ �� ID ��� �� ��������
    if task_id:
        # ���� ������: ��������� ���� ��� �������������� ���
        try:
            task_id_int = int(task_id)
        except (ValueError, TypeError):
            if close_session:
                session.close()
            return f"������������ ID ������: {task_id}"

        task = (
            session.query(Task)
            .filter(
                Task.id == task_id_int, or_(Task.user_id == user.id, Task.delegated_to_username.ilike(user.username))
            )
            .first()
        )
    elif task_title:
        # ���� �� ������ � �������� ��� ����� ������� ������
        words = task_title.lower().split()
        # OR ������ AND - ���� ������ ���������� ���� �� ���� �� ����
        conditions = [Task.title.ilike(f"%{word}%") for word in words]
        task = session.query(Task).filter(Task.user_id == user.id, Task.status != "completed", or_(*conditions)).first()
    else:
        if close_session:
            session.close()
        return "�� ������ �� task_id, �� task_title."

    if task:
        task.status = "completed"
        task.actual_completion_time = datetime.now(timezone.utc)
        session.commit()

        # �������� ��������� �������
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if profile:
            completion_time = (
                datetime.now(timezone.utc) - task.created_at.replace(tzinfo=timezone.utc)
            ).total_seconds() / 60
            profile.completed_tasks = (profile.completed_tasks or 0) + 1
            prev_avg = profile.average_completion_time or 0
            # ������ �� ������� �� ����
            if profile.completed_tasks > 0:
                profile.average_completion_time = (
                    (prev_avg * (profile.completed_tasks - 1)) + completion_time
                ) / profile.completed_tasks
            session.commit()
        result = f"��������� ������ '{task.title}'."

        # ��������� ��������� � ������� ��������������
        interaction = Interaction(user_id=user.id, message_type="ai", content=result)
        session.add(interaction)
        session.commit()
    else:
        result = "������ �� �������."
    if close_session:
        session.close()
    return result


def analyze_task(task_id=None, user_id=None, session=None):
    """����������� ������ � ������� AI � ���� ������������"""
    from models import Session, Task, UserProfile, Interaction
    from datetime import datetime
    from sqlalchemy import or_
    import asyncio

    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False
    
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        if close_session:
            session.close()
        return "������������ �� ������."

    # ����� ������ �� ID
    if task_id:
        try:
            task_id_int = int(task_id)
        except (ValueError, TypeError):
            if close_session:
                session.close()
            return f"������������ ID ������: {task_id}"

        task = (
            session.query(Task)
            .filter(
                Task.id == task_id_int, or_(Task.user_id == user.id, Task.delegated_to_username.ilike(user.username))
            )
            .first()
        )
    else:
        if close_session:
            session.close()
        return "�� ������ ID ������."

    if task:
        # �������� ������� ������������ ��� ���������
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        
        # ������� ���������� � ������ ��� �������
        task_info = f"""
        ������ ��� �������:
        ��������: {task.title}
        ��������: {task.description or '�� �������'}
        ������: {task.status}
        ����� �����������: {task.reminder_time.strftime('%Y-%m-%d %H:%M') if task.reminder_time else '�� �����������'}
        ������������: {'��' if task.delegated_to_username else '���'}
        """
        
        # �������� ���������� �� ������� ������������
        profile_info = ""
        if profile:
            profile_info = f"""
        ���������� � ������������:
        ������: {profile.skills or '�� �������'}
        ��������: {profile.interests or '�� �������'}
        ����: {profile.goals or '�� �������'}
        �����: {profile.city or '�� ������'}
        """
        
        # ������ � AI ��� �������
        analysis_prompt = f"""{task_info}{profile_info}

        ������������� ��� ������ � ��� �������� ������������:
        1. ����� ��������� � �������������� ������
        2. �������� ���� ��� ����������
        3. ��� ������ �� �����������
        4. �������� ������ � �������� ������������ ��� �������������
        
        ���� ���������� � �������� � ������."""

        try:
            # ������� event loop ��� ����������� ������ ����������� �������
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            analysis_result = loop.run_until_complete(chat_with_ai(analysis_prompt, [], user_id))
            loop.close()
            
            # ��������� ��������� ������� � ������� ��������������
            interaction = Interaction(user_id=user.id, message_type="ai", content=f"������ ������ '{task.title}':\n\n{analysis_result}")
            session.add(interaction)
            session.commit()
            
            result = f"������ ������ '{task.title}':\n\n{analysis_result}"
            
        except Exception as e:
            logger.error(f"Error analyzing task {task_id}: {e}")
            result = f"������ ��� ������� ������ '{task.title}': {str(e)}"
    else:
        result = "������ �� �������."
    
    if close_session:
        session.close()
    return result


def set_reminder(task_id, reminder_time, user_id=None):
    from models import Session, Task
    from datetime import datetime

    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "������������ �� ������."

        try:
            task_id_int = int(task_id)
        except (ValueError, TypeError):
            return f"������������ ID ������: {task_id}"

        task = session.query(Task).filter_by(id=task_id_int, user_id=user.id).first()
        if task:
            try:
                reminder_time_parsed = datetime.strptime(reminder_time, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
                task.reminder_time = reminder_time_parsed
                session.commit()
                result = f"����������� ����������� ��� '{task.title}' �� {reminder_time_parsed}."
            except ValueError:
                result = "�������� ������ �������."
        else:
            result = "������ �� �������."
        return result
    finally:
        session.close()


def update_user_memory(info, user_id=None):
    from models import Session, User

    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if user:
            # ��������� ������������ ������
            existing_decrypted = ""
            if user.memory:
                try:
                    existing_decrypted = decrypt_data(user.memory)
                except Exception:
                    existing_decrypted = ""
            # ��������� ����� ����������
            if existing_decrypted:
                existing_decrypted += "\n" + info
            else:
                existing_decrypted = info
            # ������� �������
            encrypted = encrypt_data(existing_decrypted)
            user.memory = encrypted
            session.commit()
            result = "��������� ����������."
        else:
            result = "������������ �� ������."
        return result
    finally:
        session.close()


def delegate_task(
    title, reminder_time=None, delegated_to_username=None, user_id=None, description="", delegation_details=""
):
    """Create a delegated task that requires acceptance by the recipient"""
    from models import Session, Task, User, UserProfile
    from datetime import datetime
    import pytz

    session = Session()
    try:
        # Validate reminder_time is provided
        if not reminder_time:
            return "��� ������������� ������ ��������� ������ ���� � ����� ��������. ����������, ��������: �� ����� ������ ����� � ���� ��������� �������? (��������: '2026-01-10 15:00' ��� '������ � 14:30')"

        # Validate reminder_time format
        if reminder_time:
            # Try parsing the format first
            try:
                datetime.strptime(reminder_time, "%Y-%m-%d %H:%M")
            except ValueError:
                # If not in YYYY-MM-DD HH:MM format, try to parse as relative time
                logger.info(f"[DELEGATE] Parsing relative time: {reminder_time}")
                parsed_time = parse_time_to_datetime(reminder_time, user_id)
                if parsed_time:
                    reminder_time = parsed_time
                    logger.info(f"[DELEGATE] Parsed to: {reminder_time}")
                else:
                    return f"������������ ������ ������� '{reminder_time}'. ������� ������ ����� � ������� YYYY-MM-DD HH:MM (��������: 2026-01-10 15:00)"

        # Find delegator (creator)
        delegator = session.query(User).filter_by(telegram_id=user_id).first()
        if not delegator:
            return "������: ������������ �� ������."

        # Find recipient by username
        recipient_username = delegated_to_username.replace("@", "").lower()
        recipient = session.query(User).filter(User.username.ilike(recipient_username)).first()

        if not recipient:
            return f"������������ @{recipient_username} �� ������ � �������. ���������, ��� �� ��������������� � ����."

        # If delegating to self, create regular task instead
        if recipient.id == delegator.id:
            # Create regular task for self
            task = Task(user_id=delegator.id, title=title, description=encrypt_data(description), status="pending")
            if reminder_time:
                try:
                    user_tz = pytz.timezone(delegator.timezone) if delegator.timezone else pytz.UTC
                    local_dt = datetime.strptime(reminder_time, "%Y-%m-%d %H:%M")
                    local_dt = user_tz.localize(local_dt)
                    task.reminder_time = local_dt.astimezone(pytz.UTC)
                except ValueError:
                    pass
            session.add(task)
            session.commit()
            task_id = task.id

            # Schedule reminder if set
            if task.reminder_time:
                try:
                    from main import reminder_service

                    if reminder_service:
                        reminder_service.schedule_reminder(
                            task_id=task.id,
                            reminder_time=task.reminder_time,
                            user_id=delegator.telegram_id,
                            task_title=task.title,
                        )
                except Exception as e:
                    import logging

                    logging.error(f"Failed to schedule reminder for self-delegated task {task_id}: {e}")

            # Update profile analytics
            profile = session.query(UserProfile).filter_by(user_id=delegator.id).first()
            if profile:
                profile.total_tasks_created = (profile.total_tasks_created or 0) + 1
                session.commit()

            session.close()
            return f"������ '{title}' ��������� ��� ��� � ������������ �� {reminder_time}."

        # Create task with pending delegation status
        task = Task(
            user_id=delegator.id,
            title=title,
            description=encrypt_data(description),
            delegated_by=None,
            delegated_to_username=recipient_username,
            delegation_status="pending",
            delegation_details=delegation_details,
            status="pending",
        )

        if reminder_time:
            try:
                user_tz = pytz.timezone(recipient.timezone) if recipient.timezone else pytz.UTC
                local_dt = datetime.strptime(reminder_time, "%Y-%m-%d %H:%M")
                local_dt = user_tz.localize(local_dt)
                task.reminder_time = local_dt.astimezone(pytz.UTC)
            except ValueError:
                pass

        session.add(task)
        session.commit()
        task_id = task.id

        # Send notification to recipient via Telegram
        try:
            from main import bot

            if bot:
                message = f"����� ����������� ������ �� @{delegator.username}:\n\n"
                message += f"������: {title}\n"
                if description:
                    message += f"��������: {description}\n"
                if reminder_time:
                    message += f"�������: {reminder_time}\n"
                if delegation_details:
                    message += f"������: {delegation_details}\n"
                message += f"\n�������� ���� '������� ������ {task_id}' ��� ������������� ��� '��������� ������ {task_id}' ��� ������."

                import asyncio

                asyncio.create_task(bot.send_message(recipient.telegram_id, message))
        except Exception as e:
            import logging

            logging.error(f"Failed to send delegation notification: {e}")

        session.close()
        return f"����������� ������ ���������� @{recipient_username}. ��������� �������������."
    except Exception as e:
        session.close()
        return f"������ ��� �������� �������������� ������: {str(e)}"


def suggest_alternatives(task_id, reason="", user_id=None):
    """���������� ������������ ��� ������������� ������ ����� AI"""
    import asyncio

    return asyncio.run(_suggest_alternatives_async(task_id, reason, user_id))


async def _suggest_alternatives_async(task_id, reason="", user_id=None):
    from models import Session, Task

    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "������������ �� ������."

        task = session.query(Task).filter(Task.id == task_id, Task.user_id == user.id).first()
        if not task:
            return "������ �� �������."

        # �������� ������ ������������
        user_memory = ""
        if user.memory:
            try:
                user_memory = f"\n���������� � ������������: {decrypt_data(user.memory)}"
            except:
                user_memory = ""

        # ���������� ������������ ����� AI
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}

        system_prompt = get_active_system_prompt()

        messages = [
            {"role": "system", "content": system_prompt + user_memory},
            {
                "role": "user",
                "content": f"�������� 3-5 �������������� �������� � ������ '{task.title}'. ������� ������������: '{reason}'. ���� ���������� � ����������.",
            },
        ]

        data = {"model": "deepseek-reasoner", "messages": messages, "max_tokens": 500}

        import aiohttp

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    content = result["choices"][0]["message"]["content"]
                    content = clean_technical_details(content)
                    # ��������� ����� ������������ ����������
                    content = enrich_response_with_engagement(content, user_id, task_title)
                    return content
                else:
                    return "�� ������� ������������� ������������."

    except Exception as e:
        return f"������ ��� ��������� �����������: {str(e)}"
    finally:
        session.close()


def create_subscription_payment(user_id=None):
    """������� ������ ��� �������� ��������"""
    from subscription_service import create_subscription_payment as create_sub_payment

    try:
        payment_url = create_sub_payment(user_id)
        return f"������ �� ������ �������� �������� �������: {payment_url}"
    except Exception as e:
        return f"������ �������� �������: {str(e)}"


def check_subscription_status(user_id=None):
    """��������� ������ �������� ������������"""
    from subscription_service import get_subscription_status
    from config import FREE_ACCESS_MODE

    try:
        if FREE_ACCESS_MODE:
            return "����� ����������� ������� �������. �������� �� ���������."

        status = get_subscription_status(user_id)
        if status:
            status_text = f"������ ��������: {status['status']}\n"
            status_text += f"����: {status['plan']}\n"
            if status["start_date"]:
                status_text += f"���� ������: {status['start_date'][:10]}\n"
            if status["end_date"]:
                status_text += f"���� ���������: {status['end_date'][:10]}\n"
            status_text += f"���������� ������: {status['login_count']}"
            return status_text
        else:
            return "�������� �� �������. ��� ������������� ������� ��������� �������� ��������."
    except Exception as e:
        return f"������ �������� ��������: {str(e)}"


def cancel_subscription(user_id=None):
    """�������� �������� ������������"""
    from subscription_service import cancel_subscription as cancel_sub

    try:
        success = cancel_sub(user_id)
        if success:
            return "�������� ������� ��������."
        else:
            return "�������� �� ������� ��� ��� ��������."
    except Exception as e:
        return f"������ ������ ��������: {str(e)}"


def accept_delegated_task(task_id, user_id=None):
    """Accept a delegated task"""
    from models import Session, Task, User

    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "������: ������������ �� ������."

        try:
            task_id_int = int(task_id)
        except (ValueError, TypeError):
            return f"������������ ID ������: {task_id}"

        # ���� ������ �������������� ��� (�� delegated_to_username)
        task = (
            session.query(Task)
            .filter(
                Task.id == task_id_int,
                Task.delegated_to_username.ilike(user.username),
                Task.delegation_status == "pending",
            )
            .first()
        )
        if not task:
            return "������ �� ������� ��� ��� ����������."

        # Update delegation status
        task.delegation_status = "accepted"
        session.commit()

        # Schedule reminder if set
        if task.reminder_time:
            try:
                from main import reminder_service

                if reminder_service:
                    reminder_service.schedule_reminder(
                        task_id=task.id,
                        reminder_time=task.reminder_time,
                        user_id=user.telegram_id,
                        task_title=task.title,
                    )
            except Exception as e:
                import logging

                logging.error(f"Failed to schedule reminder: {e}")

        # Notify delegator (creator)
        try:
            delegator = session.query(User).filter_by(id=task.user_id).first()
            if delegator and delegator.telegram_id != user_id:
                from main import bot

                if bot:
                    message = f"@{user.username} ������ ������: {task.title}"
                    import asyncio

                    asyncio.create_task(bot.send_message(delegator.telegram_id, message))
        except Exception as e:
            import logging

            logging.error(f"Failed to notify delegator: {e}")

        session.close()
        return f"�� ������� ������ '{task.title}'. ��� ��������� � ��� ������ �����."
    except Exception as e:
        session.close()
        return f"������: {str(e)}"


def reject_delegated_task(task_id, user_id=None):
    """Reject a delegated task"""
    from models import Session, Task, User

    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "������: ������������ �� ������."

        try:
            task_id_int = int(task_id)
        except (ValueError, TypeError):
            return f"������������ ID ������: {task_id}"

        # ���� ������ �������������� ��� (�� delegated_to_username)
        task = (
            session.query(Task)
            .filter(
                Task.id == task_id_int,
                Task.delegated_to_username.ilike(user.username),
                Task.delegation_status == "pending",
            )
            .first()
        )
        if not task:
            return "������ �� ������� ��� ��� ����������."

        # Update delegation status
        task.delegation_status = "rejected"
        task.status = "rejected"
        session.commit()

        # Notify delegator (creator)
        try:
            delegator = session.query(User).filter_by(id=task.user_id).first()
            if delegator and delegator.telegram_id != user_id:
                from main import bot

                if bot:
                    message = f"@{user.username} �������� ������: {task.title}"
                    import asyncio

                    asyncio.create_task(bot.send_message(delegator.telegram_id, message))
        except Exception as e:
            import logging

            logging.error(f"Failed to notify delegator: {e}")

        session.close()
        return f"�� ��������� ������ '{task.title}'."
    except Exception as e:
        session.close()
        return f"������: {str(e)}"


def get_delegation_progress(task_id, user_id=None):
    """Get progress report for a delegated task"""
    from models import Session, Task, User

    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "������: ������������ �� ������."

        task = session.query(Task).filter_by(id=int(task_id), user_id=user.id).first()
        if not task or not task.delegated_to_username:
            return "�������������� ������ �� �������."

        recipient = session.query(User).filter(User.username.ilike(task.delegated_to_username)).first()

        if task.delegation_status == "pending":
            status_msg = f"@{task.delegated_to_username} ��� �� ������� �� �����������."
        elif task.delegation_status == "accepted":
            if task.status == "completed":
                status_msg = f"������ ��������� @{task.delegated_to_username}!"
            else:
                status_msg = (
                    f"@{task.delegated_to_username} ������ ������ � �������� ��� ��� (������: {task.status})."
                )
        elif task.delegation_status == "rejected":
            status_msg = f"@{task.delegated_to_username} �������� ��� ������."
        else:
            status_msg = "������ ����������."

        session.close()
        return f"������: {task.title}\n{status_msg}"
    except Exception as e:
        session.close()
        return f"������: {str(e)}"


def edit_task(task_id=None, task_title=None, title=None, description=None, reminder_time=None, user_id=None, session=None):
    from models import Session, Task
    from datetime import datetime, timezone
    import pytz

    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        if close_session:
            session.close()
        return "������������ �� ������."
    
    # ����� ������ �� ID ��� �� ��������
    task = None
    if task_id:
        task = session.query(Task).filter_by(id=int(task_id)).first()
    elif task_title:
        # ���� ������ �� �������� (������ ���������� ��� ��������)
        task = session.query(Task).filter(
            Task.user_id == user.id,
            Task.title.ilike(f"%{task_title}%")
        ).first()
    
    if task:
        # ��������� ����� �������: ������ ������ ������������ ������������ ��� ���� ������������ ���
        has_access = False
        if task.user_id == user.id:
            has_access = True  # ������� ������ ������������ ��� �������������� ��
        elif task.delegated_to_username:
            # ���������, �������� �� ������������ ����������� �������������� ������
            recipient_username = task.delegated_to_username.replace("@", "").lower()
            if user.username and user.username.lower() == recipient_username:
                has_access = True

        if not has_access:
            session.close()
            return "� ��� ��� ���� �� �������������� ���� ������."

        if title:
            task.title = title
        if description:
            task.description = encrypt_data(description)
        if reminder_time:
            try:
                # ���������, �������� �� ����� �������������
                if "�����" in reminder_time.lower():
                    # ������������ parse_relative_time ��� �������������� �������
                    current_time = datetime.now(pytz.UTC)
                    parsed_time = parse_relative_time(reminder_time, current_time)
                    if parsed_time:
                        task.reminder_time = parsed_time
                        logger.info(f"Task {task.id} relative time updated: '{reminder_time}' -> {parsed_time}")
                    else:
                        session.close()
                        return "�� ������� ���������� ������������� �����."
                else:
                    # ������� ��� ���������� �����
                    reminder_time_parsed = datetime.strptime(reminder_time, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
                    task.reminder_time = reminder_time_parsed
                    logger.info(f"Task {task.id} absolute time updated: {reminder_time_parsed}")
                # ��������� ����������� ����� ������ ���������� ������ � �����������
                # ReminderService ������� bot, ������� ���������� ������ ����������
            except ValueError:
                if close_session:
                    session.close()
                return "�������� ������ �������. ����������� YYYY-MM-DD HH:MM ��� '����� X �����'."
        session.commit()
        result = f"��������� ������ '{task.title}'."
    else:
        result = "������ �� �������."
    if close_session:
        session.close()
    return result


# DUPLICATE delete_all_tasks REMOVED - Using version at line 1628

def get_task_details(task_id, user_id=None):
    from models import Session, Task

    session = Session()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        session.close()
        return "������������ �� ������."
    task = session.query(Task).filter_by(id=int(task_id)).first()
    if task:
        # ��������� ����� �������
        has_access = False
        if task.user_id == user.id:
            has_access = True  # ������� ������ ������������
        elif task.delegated_to_username:
            # ���������, �������� �� ������������ ����������� �������������� ������
            recipient_username = task.delegated_to_username.replace("@", "").lower()
            if user.username and user.username.lower() == recipient_username:
                has_access = True

        if not has_access:
            session.close()
            return "� ��� ��� ���� �� �������� ���� ������."

        session.close()
        return f"������: {task.title}, ������ {task.status}, ��������� {task.priority}."
    session.close()
    return "������ �� �������."


def get_partners_list(user_id=None, session=None):
    """���������� ������ ���� ������������� � ��������� (����� ������ ������������ � ���, � ��� ��� ���� �������������)"""
    import logging

    logger = logging.getLogger(__name__)
    logger.info(f"[PARTNERS] get_partners_list called for user_id: {user_id}")

    from models import Session, UserProfile, User, Task

    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False

    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        logger.warning(f"[PARTNERS] User not found for telegram_id: {user_id}")
        if close_session:
            session.close()
        return []

    logger.info(f"[PARTNERS] Found user: {user.id}, username: {user.username}")

    # �������� ������ �������������, � �������� ��� ���� �������������
    delegated_usernames = set()

    # ������, ������� ������������ ���
    if user.username:
        delegated_to_me = (
            session.query(Task)
            .filter(
                Task.delegated_to_username.ilike(user.username), Task.delegation_status.in_(["pending", "accepted"])
            )
            .all()
        )
        for task in delegated_to_me:
            delegated_user = session.query(User).filter_by(id=task.user_id).first()
            if delegated_user:
                delegated_usernames.add(delegated_user.username.lower() if delegated_user.username else "")
    else:
        delegated_to_me = []

    # ������, ������� � �����������
    delegated_by_me = (
        session.query(Task)
        .filter(
            Task.user_id == user.id,
            Task.delegated_to_username.isnot(None),
            Task.delegation_status.in_(["pending", "accepted"]),
        )
        .all()
    )
    for task in delegated_by_me:
        if task.delegated_to_username:
            delegated_usernames.add(task.delegated_to_username.replace("@", "").lower())

    # �������� ��� ������� � ������������ �������, ����� ������ � ���, � ��� ��� ���� �������������
    all_profiles = (
        session.query(UserProfile)
        .join(User, UserProfile.user_id == User.id)
        .filter(
            UserProfile.user_id != user.id,
            # ���� �� ���� ���� ������ ���� ���������
            (UserProfile.interests.isnot(None))
            | (UserProfile.skills.isnot(None))
            | (UserProfile.position.isnot(None))
            | (UserProfile.city.isnot(None))
            | (UserProfile.bio.isnot(None))
            | (UserProfile.languages.isnot(None)),
        )
        .all()
    )

    logger.info(f"[PARTNERS] Found {len(all_profiles)} profiles with data")

    # �������� ������� �������� ������������ ��� ���������
    user_profile = session.query(UserProfile).filter_by(user_id=user.id).first()
    if not user_profile:
        # ������� �� ������ - ���������� ������ ������
        if close_session:
            session.close()
        return []

    logger.info(
        f"[PARTNERS] User profile: interests='{user_profile.interests}', skills='{user_profile.skills}', goals='{user_profile.goals}'"
    )

    # ��������� ������ ���, � ���� ���� ����������
    partners = []
    for profile in all_profiles:
        profile_user = session.query(User).filter_by(id=profile.user_id).first()
        if not profile_user or not profile_user.username:
            continue

        logger.info(
            f"[PARTNERS] Checking profile for {profile_user.username}: interests='{profile.interests}', skills='{profile.skills}'"
        )

        # ��������� ������� ���������� �� ���������, ������� ��� �����
        has_match = False

        # �������� �� �������
        if user_profile.skills and profile.skills:
            user_skills = set(s.strip().lower() for s in user_profile.skills.split(","))
            profile_skills = set(s.strip().lower() for s in profile.skills.split(","))
            if user_skills & profile_skills:
                has_match = True
                logger.info(f"[PARTNERS] Skills match: {user_skills & profile_skills}")

        # �������� �� ���������
        if user_profile.interests and profile.interests:
            user_interests = set(i.strip().lower() for i in user_profile.interests.split(","))
            profile_interests = set(i.strip().lower() for i in profile.interests.split(","))
            if user_interests & profile_interests:
                has_match = True
                logger.info(f"[PARTNERS] Interests match: {user_interests & profile_interests}")

        # �������� �� �����
        if user_profile.goals and profile.goals:
            user_goals = set(g.strip().lower() for g in user_profile.goals.split(","))
            profile_goals = set(g.strip().lower() for g in profile.goals.split(","))
            if user_goals & profile_goals:
                has_match = True
                logger.info(f"[PARTNERS] Goals match: {user_goals & profile_goals}")

        # �������� �� ��������
        if hasattr(user_profile, "company") and hasattr(profile, "company"):
            if user_profile.company and profile.company:
                if user_profile.company.lower() == profile.company.lower():
                    has_match = True
                    logger.info(f"[PARTNERS] Company match: {user_profile.company}")

        # ��������� ������ ���� ���� ����������
        if has_match:
            partners.append(profile)
            logger.info(f"[PARTNERS] Added {profile_user.username} to partners")

    logger.info(f"[PARTNERS] Total partners found: {len(partners)}")

    # ���������: ������� ������������ �� ������ ������, ����� ���������
    user_city = user_profile.city.lower() if user_profile.city else None
    partners_same_city = []
    partners_other_city = []

    for partner in partners:
        partner_city = partner.city.lower() if partner.city else None
        if user_city and partner_city == user_city:
            partners_same_city.append(partner)
        else:
            partners_other_city.append(partner)

    # ��������� ������ ������ �� �������� �������� (�� �������� � ��������)
    partners_same_city.sort(key=lambda p: (p.average_rating or 0), reverse=True)
    partners_other_city.sort(key=lambda p: (p.average_rating or 0), reverse=True)

    # ����������: ������� �� ���� �� ������, ����� ���������
    sorted_partners = partners_same_city + partners_other_city

    if close_session:
        session.close()

    # ���������� �� 20 ������������� (����� ��������� ��� �������������)
    return sorted_partners[:20]


def find_partners(user_id=None, session=None):
    import re
    from models import Session, UserProfile, User, Task

    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        if close_session:
            session.close()
        return "������������ �� ������."

    # �������� ������ �������� ������������ ��� ������� ���������� ����
    user_tasks = session.query(Task).filter_by(user_id=user.id).all()
    user_task_keywords = set()
    for task in user_tasks:
        # ��������� �������� ����� �� �������� � �������� �����
        import re

        words = re.findall(r"\b\w+\b", (task.title + " " + (task.description or "")).lower())
        user_task_keywords.update(words)

    # ��������� ���...
    user_profile = session.query(UserProfile).filter_by(user_id=user.id).first()
    profiles = session.query(UserProfile).filter(UserProfile.user_id != user.id).all()
    # �������� ������ ��� ���������� ���������������
    blocked = []
    hidden_contacts = {}  # username -> expiration_timestamp
    if user.memory:
        try:
            decrypted = decrypt_data(user.memory)
            # ���� �������� ����� "�� ���������� @user" ��� "������������� @user"
            from datetime import datetime, timezone as dt_timezone

            # Permanent blocks
            matches = re.findall(r"�� ���������� @(\w+)|������������� @(\w+)", decrypted, re.IGNORECASE)
            for match in matches:
                blocked.extend([m for m in match if m])

            # Temporary hides: hide_contact:username:timestamp
            hide_matches = re.findall(r"hide_contact:@?(\w+):(\d+)", decrypted, re.IGNORECASE)
            current_time = int(datetime.now(dt_timezone.utc).timestamp())
            for username, expiration_ts in hide_matches:
                exp_ts = int(expiration_ts)
                if exp_ts > current_time:  # Still hidden
                    hidden_contacts[username.lower()] = exp_ts
        except Exception as e:
            pass
    partners = []
    tips = []
    # ���������, ���� �� � ������� �����-�� ������ ��� ������
    has_profile_data = (
        user_profile and (
            (user_profile.interests and user_profile.interests.strip()) or
            (user_profile.skills and user_profile.skills.strip()) or
            (user_profile.goals and user_profile.goals.strip()) or
            (user_profile.city and user_profile.city.strip()) or
            (hasattr(user_profile, 'company') and user_profile.company and user_profile.company.strip()) or
            (hasattr(user_profile, 'position') and user_profile.position and user_profile.position.strip()) or
            (hasattr(user_profile, 'bio') and user_profile.bio and user_profile.bio.strip())
        )
    )
    
    if has_profile_data:
        # ������� ��������� �� ������, ���� ������
        if user_profile.city:
            city_profiles = [p for p in profiles if p.city and p.city.lower() == user_profile.city.lower()]
            if city_profiles:
                profiles = city_profiles  # ���������� ������ ������� �� ���� �� ������

        # ������� ��� �������� �������������: {profile: (score, matched_fields)}
        partner_scores = {}

        for p in profiles:
            # ��������� ��������������� � ����
            if not p.contact_info:
                continue
            contact_username = p.contact_info.replace("@", "").lower()
            if (
                p.contact_info in blocked
                or any("@" + b in p.contact_info for b in blocked)
                or p.contact_info == f"user{user_id}"
            ):
                continue
            # ��������� �������� �������
            if contact_username in hidden_contacts:
                continue

            score = 0
            matched_fields = []

            # ������ ����� ��� ���������� ����
            partner_user = session.query(User).filter_by(id=p.user_id).first()
            if partner_user:
                partner_tasks = session.query(Task).filter_by(user_id=partner_user.id).all()
                partner_task_keywords = set()
                
                for task in partner_tasks:
                    task_text = (task.title + " " + (task.description or "")).lower()
                    words = re.findall(r"\b\w+\b", task_text)
                    partner_task_keywords.update(words)

                # ������� ����������� �������� ���� �����
                common_keywords = user_task_keywords & partner_task_keywords
                if common_keywords:
                    score += len(common_keywords) * 2  # 2 ����� �� ������ ����������
                    matched_fields.append(f"���������� ������: {', '.join(list(common_keywords)[:3])}")

            # �������� ��������� � ����������� ������� ����������
            if user_profile.interests and p.interests:
                user_interests = [i.strip().lower() for i in user_profile.interests.split(",")]
                partner_interests = [i.strip().lower() for i in p.interests.split(",")]

                for user_int in user_interests:
                    for partner_int in partner_interests:
                        # ������ ���������� = 10 ������
                        if user_int == partner_int:
                            score += 10
                            matched_fields.append(f"�������: {user_int}")
                        # ���� �������� ������ = 5 ������
                        elif user_int in partner_int or partner_int in user_int:
                            score += 5
                            matched_fields.append(f"������� �������: {partner_int}")

            # �������� �������
            if user_profile.skills and p.skills:
                user_skills = [s.strip().lower() for s in user_profile.skills.split(",")]
                partner_skills = [s.strip().lower() for s in p.skills.split(",")]

                for user_skill in user_skills:
                    for partner_skill in partner_skills:
                        if user_skill == partner_skill:
                            score += 10
                            matched_fields.append(f"�����: {user_skill}")
                        elif user_skill in partner_skill or partner_skill in user_skill:
                            score += 5
                            matched_fields.append(f"������� �����: {partner_skill}")

            # �������� �����
            if user_profile.goals and p.goals:
                user_goals = [g.strip().lower() for g in user_profile.goals.split(",")]
                partner_goals = [g.strip().lower() for g in p.goals.split(",")]

                for user_goal in user_goals:
                    for partner_goal in partner_goals:
                        if user_goal == partner_goal:
                            score += 10
                            matched_fields.append(f"����: {user_goal}")
                        elif user_goal in partner_goal or partner_goal in user_goal:
                            score += 5
                            matched_fields.append(f"������� ����: {partner_goal}")

            # �������� (������ ����������)
            if hasattr(user_profile, "company") and hasattr(p, "company") and user_profile.company and p.company:
                if user_profile.company.lower() == p.company.lower():
                    score += 15  # ������� - ������� ���������
                    matched_fields.append(f"������� �� {p.company}")

            # ��������� (��������� ����������)
            if hasattr(user_profile, "position") and hasattr(p, "position") and user_profile.position and p.position:
                if (
                    user_profile.position.lower() in p.position.lower()
                    or p.position.lower() in user_profile.position.lower()
                ):
                    score += 8
                    matched_fields.append(f"���������: {p.position}")

            # ���� ���� ���������� - ��������� � ���������
            if score > 0:
                partner_scores[p] = (score, matched_fields)

        # ��������� �� �������� �������������
        sorted_partners = sorted(partner_scores.items(), key=lambda x: x[1][0], reverse=True)
        partners = [item[0] for item in sorted_partners]

        # ��������� ����� �� ������������� ��� ���-3
        for p in partners[:3]:
            if p.current_plans and user_profile.interests:
                for interest in user_profile.interests.split(","):
                    interest_words = interest.strip().lower().split()
                    if any(word in p.current_plans.lower() for word in interest_words):
                        tips.append(
                            f"@{p.contact_info} ������� {p.current_plans.split(',')[0]} - ����� ���� ��������� � ������ ���������� � {interest.strip()}."
                        )
                        break
                        break
    else:
        # ���� ������� ��� ��� �� ������, ������� �������� ��������� ��� ������������
        partners = profiles[:3] if profiles else []

    if close_session:
        session.close()

    response = ""
    if partners:
        response += "����� ���������� �����:\n"
        for idx, p in enumerate(partners[:3], 1):
            info_parts = []

            # ���������� ������� ���������� (������ ���� ������� ��������)
            if has_profile_data and p in partner_scores:
                score, matched = partner_scores[p]
                # ���� ������ ����� ����������� ����������
                match_reason = matched[0] if matched else "����� ��������"
                info_parts.append(f"����������: {match_reason}")

            if p.interests:
                info_parts.append(f"��������: {p.interests}")
            if hasattr(p, "bio") and p.bio:
                bio_short = p.bio[:80] + "..." if len(p.bio) > 80 else p.bio
                info_parts.append(f"����� ������������: {bio_short}")
            if hasattr(p, "position") and p.position:
                info_parts.append(f"{p.position}")
            if hasattr(p, "company") and p.company:
                info_parts.append(f"��������: {p.company}")
            if hasattr(p, "languages") and p.languages:
                info_parts.append(f"�����: {p.languages}")
            if p.city:
                info_parts.append(f"�����: {p.city}")

            info_str = ", ".join(info_parts) if info_parts else "������� � ����������"
            response += f"{idx}. @{p.contact_info}\n   {info_str}\n"

        # ��������� ����������� ���������� ���� �� ������ ����� (������ ���� ������� ��������)
        if has_profile_data:
            joint_ideas = []
            for p in partners[:3]:
                if p in partner_scores:
                    score, matched = partner_scores[p]
                    # ���� ���� ���������� �� �������, ���������� ���������� ����
                    task_matches = [m for m in matched if m.startswith("���������� ������")]
                    if task_matches:
                        partner_user = session.query(User).filter_by(id=p.user_id).first()
                        if partner_user:
                            partner_tasks = session.query(Task).filter_by(user_id=partner_user.id).all()
                            for pt in partner_tasks[:2]:  # ��������� ������ 2 ������
                                for ut in user_tasks[:2]:
                                    common_words = set(
                                        re.findall(r"\b\w+\b", (pt.title + " " + (pt.description or "")).lower())
                                    ) & set(re.findall(r"\b\w+\b", (ut.title + " " + (ut.description or "")).lower()))
                                    if common_words:
                                        joint_ideas.append(
                                            f"@{p.contact_info} ���� �������� ��� '{pt.title}' - ����� ������������ ��� ����������� �������� {', '.join(list(common_words)[:2])}!"
                                        )
                                        break
                                if joint_ideas and len(joint_ideas) >= 2:  # �������� 2 ����
                                    break

            response = response.rstrip("\n")
            if joint_ideas:
                response += "\n\n" + "\n".join(joint_ideas[:2])
    else:
        if has_profile_data:
            # ������� ����, �� �� ����� ���������� ���������
            response = "�� ������ ������� ���� �� ������� ��������� ����������, �� ������� �����������! "
        else:
            # ������� ��� ��� �� ������
            response = "����, ��� � ���� ���� �� �������� ������� ��� ���� ������ ��� ������. �� ��� �������� ����������� ������ ������� �������� ����������! "
        response += "� ���� ����� ��� ���� ������ �� ������ ��� ������ ������ � ���������� ��������, ���������������� �� ��������� ��� ������, ����� ��� ������, ��������� ��� �������� ����� ������� � ���������� �����, � ����� ����� �� ������ ������ ��� �������� ������. "
        response += "�������� ��� � ����� ���������, ������� ��� ����� - � � ����� ����� ���������� �����. ��� ���� �������� ��� ��� ��� ���������?"

    return response


def update_profile(
    skills=None,
    interests=None,
    goals=None,
    city=None,
    current_plans=None,
    timezone=None,
    company=None,
    position=None,
    bio=None,
    languages=None,
    user_id=None,
    session=None,
):
    from models import Session, User, UserProfile

    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        user = User(telegram_id=user_id)
        session.add(user)
        session.commit()
    profile = session.query(UserProfile).filter_by(user_id=user.id).first()
    if not profile:
        profile = UserProfile(user_id=user.id)
        session.add(profile)

    updates_made = []  # ����������� ��� ������ ��������
    needs_confirmation = []  # ��� ������� �������������
    
    def update_list_field(field, value, field_name):
        if value is None:  # ���� None - �� ������� ����
            return field, None, False
        if value == "":  # ���� ������ ������ - ������� ����
            action = f"cleared_{field_name}"
            return None, action, False
        
        current = set((field or "").split(", ")) - {""}  # ��������� �� ", " � ������� ������
        action = None
        requires_confirmation = False
        
        if value.startswith("+"):
            # ����� ����������
            new_item = value[1:].strip()
            if new_item:
                current.add(new_item)
                action = f"added_{field_name}:{new_item}"
        elif value.startswith("-"):
            # ����� ��������
            remove_item = value[1:].strip()
            if remove_item in current:
                current.discard(remove_item)
                action = f"removed_{field_name}:{remove_item}"
        else:
            # �����: ���������� ��� �������� ������� ������������� ��� interests
            new_items_list = [item.strip() for item in value.split(",") if item.strip()]
            added = []
            for item in new_items_list:
                if item not in current:
                    added.append(item)
            
            if added and field_name == "interests":
                # ��� ��������� ������� �������������
                requires_confirmation = True
                action = f"pending_{field_name}:{', '.join(added)}"
            elif added:
                # ��� ������ ����� ��������� �����
                for item in added:
                    current.add(item)
                action = f"added_{field_name}:{', '.join(added)}"
        
        return ", ".join(sorted(current)), action, requires_confirmation

    if skills is not None:  # ��������� �� None ������ ������ if skills
        new_value, action, needs_confirm = update_list_field(profile.skills, skills, "skills")
        if needs_confirm:
            needs_confirmation.append(action)
        else:
            profile.skills = new_value
            if action:
                updates_made.append(action)
    
    if interests is not None:  # ��������� �� None
        new_value, action, needs_confirm = update_list_field(profile.interests, interests, "interests")
        if needs_confirm:
            needs_confirmation.append(action)
        else:
            profile.interests = new_value
            if action:
                updates_made.append(action)
    
    if goals is not None:  # ��������� �� None
        new_value, action, _ = update_list_field(profile.goals, goals, "goals")
        profile.goals = new_value
        if action:
            updates_made.append(action)
    
    if city is not None:  # ��������� ������ ������ ��� �������
        old_city = profile.city
        profile.city = city if city else None
        updates_made.append(f"changed_city:{old_city}->{city if city else 'cleared'}")
    
    if current_plans:
        profile.current_plans = current_plans
        updates_made.append(f"updated_plans")
    
    # ��������� ��������� ����� ���� (����� ������������� � ������ ��)
    if hasattr(profile, "company") and company is not None:  # ��������� ������ ������
        old_company = profile.company
        profile.company = company if company else None
        updates_made.append(f"changed_company:{old_company}->{company if company else 'cleared'}")
    
    if hasattr(profile, "position") and position is not None:  # ��������� ������ ������
        old_position = profile.position
        profile.position = position if position else None
        updates_made.append(f"changed_position:{old_position}->{position if position else 'cleared'}")
    
    if hasattr(profile, "bio") and bio is not None:  # ��������� ������ ������
        old_bio = profile.bio
        profile.bio = bio if bio else None
        updates_made.append(f"changed_bio:{old_bio}->{bio if bio else 'cleared'}")
    
    if hasattr(profile, "languages") and languages is not None:  # ��������� ������ ������
        old_languages = profile.languages
        profile.languages = languages if languages else None
        updates_made.append(f"changed_languages:{old_languages}->{languages if languages else 'cleared'}")
    
    if timezone:
        user.timezone = timezone
        updates_made.append(f"changed_timezone:{timezone}")
    
    profile.contact_info = f"user{user_id}"  # ������� username
    profile.updated_at = datetime.now(pytz.UTC)
    session.commit()
    if close_session:
        session.close()
    
    # ���������� ��������� �����
    if needs_confirmation:
        return f"CONFIRMATION_REQUIRED:{';'.join(needs_confirmation)}"
    elif updates_made:
        return f"������� ��������: {'; '.join(updates_made)}"
    else:
        return "������� �������� (��������� �� ����������)"


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "add_task",
            "description": "�������� ����� ������ � ������������ �������� �����������. ��������: �� �������� description ���� ������������ �� ������ ����� ������! �������� ������. ��������: ��������� ������ ������� ���� �� system prompt ({{current_date}}), �� ��������� ���� �� ����� ������!",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "�������� ������ - ������ ���� ���������� � ���������: ��������, ������, ��������. ������: '�������� �������� �����'. �����: '��������� �����'",
                    },
                    "description": {
                        "type": "string",
                        "description": "�����������! ������ ������ ���� ������������ �� ������ ������. ���� ������ - �������� 50 ��������. �������: '������, ����, ����' ��� '�������� ��������'",
                    },
                    "reminder_time": {"type": "string", "description": "����� ����������� � ������� YYYY-MM-DD HH:MM. ����������� ��������� current_date �� system prompt ��� ���������� ����! ��������, ���� current_date=2026-01-11 � ������������ ������ '����� 5 ����� � 12:30', ��������� '2026-01-11 12:30', � �� ���� �� ��������!"},
                    "due_date": {"type": "string", "description": "������� � ������� YYYY-MM-DD HH:MM, �����������"},
                },
                "required": ["title", "reminder_time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_tasks",
            "description": "�������� ������ �����",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_task",
            "description": "��������� ������������ ������ �� ID ��� ��������. ������� ����� ������������ ������� ��� ��������/������/�������� ������. �� �������� ����� ������, � ������ ������� ������������!",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "ID ������ (����������� ���� ������ task_title)"},
                    "task_title": {
                        "type": "string",
                        "description": "�������� ������ ��� ��� ����� (����������� ���� ������ task_id)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_reminder",
            "description": "���������� ����������� ��� ������",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "ID ������"},
                    "reminder_time": {"type": "string", "description": "����� ����������� � ������� YYYY-MM-DD HH:MM"},
                },
                "required": ["task_id", "reminder_time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_user_memory",
            "description": "��������� ���������� � ������������ � �������������� ������ ��� ��������������",
            "parameters": {
                "type": "object",
                "properties": {
                    "info": {
                        "type": "string",
                        "description": "���������� ��� ����������, �������� ������������, ��������, ����",
                    }
                },
                "required": ["info"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delegate_task",
            "description": "������� ������ ��� ������� ������������. ������� ������ ����� � ��������� ���� @username! ���� ��� @mention - �� ������� ��� �������. reminder_time ����� ��������� � ������������ ������� ��� '������ � 10:00', '�� ����������� 15:00' � �.�.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "�������� ������"},
                    "description": {"type": "string", "description": "��������� �������� ������ (�����������)"},
                    "reminder_time": {
                        "type": "string",
                        "description": "����� �������� � ����� ������� �������: '������ � 10:00', '�� ����������� 15:00', '������� � 18:00' � �.�.",
                    },
                    "delegated_to_username": {
                        "type": "string",
                        "description": "Username ���������� � @ (�������� @username)",
                    },
                    "delegation_details": {
                        "type": "string",
                        "description": "������: �������� ���������, �������� ����������, ��������",
                    },
                },
                "required": ["title", "reminder_time", "delegated_to_username"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "accept_delegated_task",
            "description": "������� �������������� ������",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "integer", "description": "ID ������"}},
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reject_delegated_task",
            "description": "��������� �������������� ������",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "integer", "description": "ID ������"}},
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_delegation_progress",
            "description": "�������� ������ ���������� �������������� ������ ��� ����������",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "integer", "description": "ID ������"}},
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_task",
            "description": "�������� ��������, �������� ��� ����� ����������� ������",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "ID ������"},
                    "title": {"type": "string", "description": "����� ��������, �����������"},
                    "description": {"type": "string", "description": "����� ��������, �����������"},
                    "reminder_time": {
                        "type": "string",
                        "description": "����� ����� ����������� � ������� YYYY-MM-DD HH:MM, �����������",
                    },
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_task",
            "description": "������� ������ �� ID ��� ��������",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "ID ������ (����������� ���� ������ task_title)"},
                    "task_title": {
                        "type": "string",
                        "description": "�������� ������ ��� ��� ����� (����������� ���� ������ task_id)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_priority",
            "description": "���������� ��������� ������",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "ID ������"},
                    "priority": {"type": "string", "description": "���������: high, medium, low"},
                },
                "required": ["task_id", "priority"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_task_details",
            "description": "�������� ������ ���������� � ������",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "integer", "description": "ID ������"}},
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_partners",
            "description": "����� ������������� ����� �� ������ ������� ������������",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_profile",
            "description": "�������� ������� ������������. �����: �� ��������� ��� �������� ����������� � ������������ (�� ��������). ��������� ������� '-' ��� ��������. ��� ������ ������� �������: ������� ������ ������ '' ��� ���� ����� (city='', company='', position='', bio='', languages='', skills='', interests='', goals=''). ��������: interests='���' - ������� � ������������, interests='-������������' - ������ �� ������, interests='' - ��������� ������� ��������",
            "parameters": {
                "type": "object",
                "properties": {
                    "skills": {"type": "string", "description": "������ (����������� � ������������, ����� �������). ��� �������� ��������� '-�����'"},
                    "interests": {"type": "string", "description": "�������� (����������� � ������������, ����� �������). ��� �������� ��������� '-�������'"},
                    "goals": {"type": "string", "description": "���� (����������� � ������������)"},
                    "city": {"type": "string", "description": "����� ������������ (�������� ������ ��������), �����������"},
                    "current_plans": {
                        "type": "string",
                        "description": "������� ����� ��� ������� ������������, �����������",
                    },
                    "current_time": {
                        "type": "string",
                        "description": "������� ����� ������������ � ������� HH:MM, �����������",
                    },
                    "timezone": {
                        "type": "string",
                        "description": "������� ���� ������������, �������� 'Europe/Moscow', �����������",
                    },
                    "company": {
                        "type": "string",
                        "description": "��������, � ������� �������� ������������ (�������� ������ ��������), �����������",
                    },
                    "bio": {
                        "type": "string",
                        "description": "����� ������������ ������������ (����������, ������������, ������� ��������������), �������� ������ ��������, �����������",
                    },
                    "languages": {
                        "type": "string",
                        "description": "����� ������������ (��������: ������� (������), English (C1), Espanol (A2)), �������� ������ ��������, �����������",
                    },
                    "position": {"type": "string", "description": "��������� ������������ (�������� ������ ��������), �����������"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "suggest_alternatives",
            "description": "���������� ������������ ��� ������������� ������: ���������, ������� �� �����, ������������, ����� �������",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "ID ������"},
                    "reason": {"type": "string", "description": "������� ������������ (�����������)"},
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_all_tasks",
            "description": "������� ��� ������ ������������. ��������: ��� ����������� ��������! ����� ������� ����������� ��������� � ������������: '�� ����� ������ ������� ��� ������? ��� �������� ������ ��������.' � ������� ������ �������������.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_subscription_payment",
            "description": "������� ������ ��� ���������� ��� ��������� �������� ��������",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_subscription_status",
            "description": "��������� ������ ������� �������� ������������",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "brainstorm_ideas",
            "description": "������������� ���� ��� ������� �������� ��� ��������� ��������. ��������� ����� ������������ ������ ����, ������ ��� brainstorming.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "���� ��� �������� ��� ��������� ����",
                    },
                    "num_ideas": {
                        "type": "integer",
                        "description": "���������� ���� (�� ��������� 5)",
                        "default": 5,
                    },
                },
                "required": ["topic"],
            },
        },
    },
]


async def chat_with_ai(message, context=None, user_id=None, file_content=None):
    # Force rebuild v3.0 - FIXED clean_content issue
    import re
    import json
    from datetime import datetime, timezone, timedelta
    import pytz

    logger = logging.getLogger(__name__)

    # Ensure context is a list or None
    if context is not None and not isinstance(context, list):
        logger.warning(f"context is not a list: {type(context)}, setting to None")
        context = None

    # ��������� ��������� � ������� � ��������� timezone
    time_message_match = re.search(r"���\s+�������\s+�����:\s*(\d{1,2}:\d{2})", message.lower())
    if time_message_match:
        user_time_str = time_message_match.group(1)
        detected_timezone = determine_timezone_from_time(user_time_str, user_id)
        if detected_timezone:
            logger.info(f"Detected timezone {detected_timezone} from time {user_time_str}")
            update_profile(timezone=detected_timezone, user_id=user_id)

    # ��������� ������������ ��������� �� �������
    original_message = message
    # Extract mentions before cleaning message
    mentions = re.findall(r"@[\w]+", message)
    mentions_str = ", ".join(mentions) if mentions else "���"
    # Clean message from mentions for processing
    clean_message = re.sub(r"@[\w]+", "", message).strip()
    context_len = (
        len(context) if context and not isinstance(context, int) else (context if isinstance(context, int) else 0)
    )
    logger.info(
        f"chat_with_ai called with message: {clean_message[:50]}..., mentions: {mentions_str}, context len: {context_len}, user_id: {user_id}, file: {file_content is not None}"
    )
    logger.info(f"DEEPSEEK_API_KEY present: {bool(DEEPSEEK_API_KEY)}")

    if not DEEPSEEK_API_KEY:
        logger.warning("DEEPSEEK_API_KEY not set")
        return "API ���� DeepSeek �� ��������. ��� ���� �����: ������! � AI-��������� TaskChat. ��� ���� ������?"

    try:
        logger.info("Starting chat_with_ai processing")
        # Get user memory and all tasks for extended context
        user_memory = ""
        user = None
        profile = None
        session = None
        # Initialize time variables with defaults
        base_now = datetime.now(pytz.UTC)
        user_now = base_now
        current_time_str = user_now.strftime("%H:%M")
        user_username = "user"

        if user_id:
            from models import Session, User, Task, UserProfile, Subscription

            db_session = Session()
            user = db_session.query(User).filter_by(telegram_id=user_id).first()

            # ������� ������������ ���� �� ����������
            if not user:
                user = User(telegram_id=user_id)
                db_session.add(user)
                db_session.commit()

            # Check subscription
            from config import FREE_ACCESS_MODE

            if not FREE_ACCESS_MODE:
                subscription = db_session.query(Subscription).filter_by(user_id=user.id, status="active").first()
                if not subscription:
                    db_session.close()
                    return "� ��� ��� �������� ��������. ��� ������������� AI-���������� ����������� �������� � Telegram ���� @asibiont_bot. ����� ��������� �������� � ����� �������� ��� � ����������� ��������!"

            # Get user current time FIRST before using it
            base_now = datetime.now(pytz.UTC)
            logger.info(f"[TIME CHECK] Real UTC now: {base_now}")
            logger.info(f"[TIME CHECK] Formatted: {base_now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
            user_now = base_now  # Default to base_now
            current_time_str = user_now.strftime("%H:%M")
            user_tz = pytz.UTC  # Default
            if user:
                tz_str = user.timezone if user.timezone else "UTC"
                logger.info(f"User timezone: {tz_str}")
                try:
                    user_tz = pytz.timezone(tz_str)
                    user_now = base_now.astimezone(user_tz)
                    current_time_str = user_now.strftime("%H:%M")
                    logger.info(f"[TIME CHECK] User local time ({tz_str}): {user_now}")
                    logger.info(f"[TIME CHECK] Formatted for prompt: {current_time_str}")
                    logger.info(f"[TIME CHECK] Full date for prompt: {user_now.strftime('%Y-%m-%d')}")
                except Exception as e:
                    logger.error(f"Error setting user timezone: {e}")
                    user_tz = pytz.UTC
                    user_now = base_now
                    current_time_str = user_now.strftime("%H:%M")

            if user and user.memory:
                try:
                    decrypted = decrypt_data(user.memory)
                    user_memory = f"\n���������� � ������������: {decrypted}"
                except (Exception,):
                    user_memory = ""  # If decryption fails, skip

            # ��������� ���������� �� ������� (��������, ��������� � �.�.)
            profile = db_session.query(UserProfile).filter_by(user_id=user.id).first()
            profile_filled = False
            if profile:
                profile_info = []
                if profile.city:
                    profile_info.append(f"�����: {profile.city}")
                if profile.company:
                    profile_info.append(f"��������: {profile.company}")
                if profile.position:
                    profile_info.append(f"���������: {profile.position}")
                if hasattr(profile, 'bio') and profile.bio:
                    profile_info.append(f"����� ������������: {profile.bio}")
                if hasattr(profile, 'languages') and profile.languages:
                    profile_info.append(f"�����: {profile.languages}")
                if profile.skills:
                    profile_info.append(f"������: {profile.skills}")
                if profile.interests:
                    profile_info.append(f"��������: {profile.interests}")
                if profile.goals:
                    profile_info.append(f"����: {profile.goals}")
                
                # ���������� ������������� ����
                empty_fields = []
                if not profile.city:
                    empty_fields.append("�����")
                if not profile.company:
                    empty_fields.append("��������")
                if not profile.position:
                    empty_fields.append("���������")
                if not profile.skills:
                    empty_fields.append("������")
                if not profile.interests:
                    empty_fields.append("��������")
                if not profile.goals:
                    empty_fields.append("����")
                if not (hasattr(profile, 'languages') and profile.languages):
                    empty_fields.append("�����")
                if not (hasattr(profile, 'bio') and profile.bio):
                    empty_fields.append("����� ������������")
                
                if profile_info:
                    user_memory += f"\n�������: {', '.join(profile_info)}"
                
                # ����������� ���������� ��� ������������� �����
                if empty_fields:
                    fields_list = ', '.join(empty_fields[:3])  # ����� ������ 3 �������������
                    user_memory += f"\n?? ������������� ����: {fields_list}. ������ 5-7 ��������� ���������� ��������� �� ����� �� ��� (����������� � ��������� �������, �� ���������)!"
                
                profile_filled = len(profile_info) >= 3  # ������� ��������� ����������� ���� ���� ���� �� 3 ����
                # ���� ������� ������ ������ - ������ ������ � ������ ���������
                if not profile_filled and (len(context) if context else 0 < 2):
                    user_memory += "\n�������� �����: ������� ����� ����! � ������ ������ ���������� ������ � ������, �������� ��� ��������� ��� ������ ������!"
            else:
                user_memory += f"\n������� �� �������� - ����� ������ ��� ���������� ������� (������ �� �������: �����, ��������, ���������, ������, ��������, ����)"

            # �� ��������� ������ � user_memory! ����� ������ ��� ������� list_tasks()
            # ��� �������� ��� �������������� ����������� �����

            # �� ��������� ������� ������ ��� ���������
            tasks_summary = db_session.query(Task).filter_by(user_id=user.id, status="pending").count()
            overdue_tasks = (
                db_session.query(Task)
                .filter(Task.user_id == user.id, Task.reminder_time < user_now, Task.status == "pending")
                .limit(5)
                .all()
            )

            if tasks_summary > 0:
                user_memory += f"\n������: ����� �������� ����� {tasks_summary}"

            if overdue_tasks:
                overdue_titles = [f"{t.title}" for t in overdue_tasks]
                user_memory += f"\n������������ ������: {', '.join(overdue_titles)} - �������� ������!"

            # Add delegated tasks info
            if user.username:
                delegated_tasks = (
                    db_session.query(Task)
                    .filter(Task.delegated_to_username.ilike(user.username), Task.delegation_status == "pending")
                    .all()
                )
                if delegated_tasks:
                    delegated_info = [
                        f"������ '{t.title}' (ID: {t.id}) �� @{creator.username if (creator := db_session.query(User).filter_by(id=t.user_id).first()) else 'unknown'}"
                        for t in delegated_tasks[:3]
                    ]
                    user_memory += f"\n�������������� ������ ��� ��������: {', '.join(delegated_info)}"

            # Add info about tasks delegated BY user
            my_delegated_tasks = (
                db_session.query(Task)
                .filter(
                    Task.user_id == user.id,
                    Task.delegated_to_username.isnot(None),
                    Task.delegation_status.in_(["pending", "accepted"]),
                )
                .all()
            )
            if my_delegated_tasks:
                my_delegated_info = [
                    f"������ '{t.title}' �������� @{t.delegated_to_username} (������: {t.delegation_status})"
                    for t in my_delegated_tasks[:3]
                ]
                user_memory += f"\n������ ���������� ������: {', '.join(my_delegated_info)}"

            # Add partners/contacts info
            try:
                partners = get_partners_list(user_id=user_id, session=db_session)
                if partners:
                    # partners - ��� ������ �������� UserProfile
                    partners_usernames = []
                    for p in partners[:5]:
                        partner_user = db_session.query(User).filter_by(id=p.user_id).first()
                        if partner_user and partner_user.username:
                            partners_usernames.append(f"@{partner_user.username}")
                    if partners_usernames:
                        user_memory += f"\n��������� ��������: {', '.join(partners_usernames)}"
            except Exception as e:
                logger.error(f"Error getting partners: {e}")

            # Add file content if provided
            if file_content:
                user_memory += f"\n���������� �������������� �����: {file_content[:2000]}"  # Limit to 2000 chars

            # ��������� pending_action
            if user and user.pending_action:
                try:
                    pending_data = json.loads(user.pending_action)
                    action_type = pending_data.get("type")

                    # �������� �� ������� (24 ����)
                    timestamp = pending_data.get("timestamp")
                    if timestamp:
                        created_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                        if datetime.now(timezone.utc) - created_at > timedelta(hours=24):
                            logger.info(f"Pending action timed out for user {user_id}, clearing")
                            user.pending_action = None
                            db_session.commit()
                            # ���������� � ������� ����������
                            pass
                        else:
                            # ���������� ��������� pending_action
                            pass

                    if action_type == "result_check_response":
                        task_id = pending_data.get("task_id")
                        task_title = pending_data.get("task_title")
                        # ��������� ����� ������������ ��� completion_notes
                        task = db_session.query(Task).filter(Task.id == task_id, Task.user_id == user.id).first()
                        if task:
                            task.completion_notes = original_message  # ��������� ������ ����� ������������
                            db_session.commit()
                        # �������� pending_action
                        user.pending_action = None
                        db_session.commit()
                        # ������� ����������� ����� ��� ��������� ����������
                        return f"������� �� ���������� � ������ '{task_title}'! ��������� ������� ��� �������."

                    elif action_type == "task_skip_confirmation":
                        task_id = pending_data.get("task_id")
                        task_title = pending_data.get("task_title")
                        # ���������� ����� ������������ � �������� ������
                        task = db_session.query(Task).filter(Task.id == task_id, Task.user_id == user.id).first()
                        if task:
                            if "��" in original_message.lower() or "����������" in original_message.lower():
                                skip_response = f"������ '{task_title}' �������� ��� �����������. ���� ���������� ������������ ��� ������� ����� ������."
                                return skip_response
                            else:
                                keep_response = f"������, ��������� ������ '{task_title}' ��������. ��� ���� ������?"
                                return keep_response
                        user.pending_action = None
                        db_session.commit()
                except (json.JSONDecodeError, KeyError) as e:
                    logger.error(f"Error processing pending_action: {e}")
                    user.pending_action = None
                    db_session.commit()

        db_session.close()

        # Classify user intent (use improved version)
        if PROMPTS_V2_AVAILABLE:
            intent = improved_classify_intent(clean_message, mentions_str)
            logger.info(f"[PROMPTS V2] User intent: {intent['type']} (confidence: {intent['confidence']})")
        else:
            # Fallback to basic intent if improved_prompts_final.py not available
            intent = {"type": "conversation", "confidence": 0.5, "params": {}}
            logger.warning("[FALLBACK] improved_prompts_final.py not available, using basic intent")

        # ������ ����������� ��������� ����������� - ��� ����� AI ������

        # �������� ������ ��������� ��� ������������������� �������
        context_analysis = analyze_user_context_for_advice(user_id, clean_message, context)
        if "error" not in context_analysis:
            # ��������� ������ � user_memory ��� ������������� � �������
            user_memory += f"\n\n������ ���������:\n"
            user_memory += f"������� �������� �� {context_analysis['profile'].get('filled_fields', 0)}/6 �����\n"
            user_memory += f"������: {context_analysis['tasks']['pending']} ��������, {context_analysis['tasks']['completed']} ���������\n"
            user_memory += f"�������� ����: {', '.join([f'{theme}: {count}' for theme, count in context_analysis['patterns']['main_themes']])}\n"
            user_memory += f"������������� ���������: {context_analysis['context_insights']['emotional_state']}\n"
            user_memory += f"������� ���������: {context_analysis['context_insights']['urgency_level']}\n"
            if context_analysis['recommendations']:
                user_memory += f"������������ ������������: {', '.join(context_analysis['recommendations'])}\n"

        # Construct system prompt with replaced placeholders
        # ��������� system prompt ��� ������ � ������������� ��������
        user_username = f"@{user.username}" if user and user.username else "@unknown"
        
        # ��������� ��������� 2 ������ ������ ��� �������������� ��������
        last_responses = []
        if context and isinstance(context, list):
            for item in context[-3:]:  # ��������� 3 ���������
                if "agent" in item:
                    # ���� ������ 40 ��������
                    response_text = item["agent"][:40].strip()
                    if response_text and response_text not in last_responses:
                        last_responses.append(response_text)
        
        # ������������ �� 2 ���������
        last_responses = last_responses[-2:]
        
        if PROMPTS_V2_AVAILABLE:
            system_prompt = get_optimized_prompt_final(
                user_now, current_time_str, user_username, mentions_str, user_memory, last_responses
            )
            logger.info("[PROMPTS V2] Using optimized prompt system")
        else:
            system_prompt = get_extended_system_prompt(user_now, current_time_str, user_username, mentions_str, user_memory)
            logger.info("[LEGACY] Using extended prompt system")

        # ��������� �������� ��������� ��������� ������ ��� edit_task
        last_task_context = ""
        if redis_client and user_id:
            try:
                last_task_data = await redis_client.get(f"last_task_id:{user_id}")
                if last_task_data:
                    task_info = json.loads(last_task_data.decode("utf-8"))
                    last_task_context = f"\n\n�������� ��������� ������: ID={task_info['id']}, ��������='{task_info['title']}', �����='{task_info.get('reminder_time', '')}'. ���� ������������ ��� ��������� (� ������, �� ������ � �������, �������� ����� � �.�.), ����������� ��������� edit_task(task_id={task_info['id']}, ...)!"
                    logger.info(f"[LAST_TASK_CONTEXT] Loaded for user {user_id}: {task_info}")
            except Exception as e:
                logger.error(f"Error loading last_task_id from Redis: {e}")

        messages = [{"role": "system", "content": system_prompt}]
        if context and isinstance(context, list):
            for item in context:
                if "user" in item:
                    messages.append({"role": "user", "content": item["user"]})
                if "agent" in item:
                    messages.append({"role": "assistant", "content": item["agent"]})
        # ��������� ������� ��������� � ���������� ��������� ������
        user_message_with_context = message + last_task_context
        messages.append({"role": "user", "content": user_message_with_context})

        # ���������� intent classification ������ hardcoded ��������
        is_advice_question = intent.get('type') in ['conversation', 'unknown'] and any(word in clean_message.lower() for word in [
            "��� ������", "���", "�����", "������", "��� �����������", "��� ����", 
            "��� �����������", "����� ����", "��� ������ �", "��� ������",
            "�� ���� � ���� ������", "� ���� ������", "��� ������", "��� ������ ������",
            "��� ������ ����", "��� �����", "��� �����������", "����� �����",
            "����� �����", "���������", "��� ���������", "��� ������ � ��������",
            "��� ��������������", "��� ��������", "��� �������������", "��� ������",
            "� ���� ������", "��� ����������", "��� ����� �������", "��� ������ ��������"
        ])

        # ����������, �������� �� ��������� �������� �� ���������� �������� �� ������ intent
        is_task_request = intent.get('type') in [
            'add_task', 'complete_task', 'list_tasks', 'edit_task', 'delete_task', 
            'delegate_task', 'find_partners', 'update_profile'
        ]

        # ����� ������ ������ ������������ �� ������ intent classification
        intent_type = intent.get('type', 'unknown')
        
        if intent_type in ['conversation', 'unknown'] and is_advice_question:
            # ������� � ������ - �� ���������� �����������, �������� �������
            tool_choice = "none"
        elif intent_type in ['add_task', 'complete_task', 'list_tasks', 'edit_task', 'delete_task', 'delegate_task']:
            # ����� ������� �� ���������� �������� - ���������� �����������
            tool_choice = "auto"
        elif intent_type == 'find_partners':
            # ����� ��������� - ���������� �����������
            tool_choice = "auto"
        elif intent_type == 'update_profile':
            # ���������� ������� - ���������� �����������
            tool_choice = "auto"
        else:
            # �� ��������� - ���������������
            tool_choice = "auto"

        # ������������ ����������� � ����������� �� ���� ���������
        temperature = 0.7  # Default
        top_p = 1.0  # Default
        
        if intent_type == 'greeting':
            # ��� ����������� ����� ������������ �������������
            temperature = 1.0
            top_p = 0.95  # Nucleus sampling ��� ������������
        elif intent_type in ['conversation', 'unknown'] and is_advice_question:
            # ��� ������� ����� ������������
            temperature = 0.85
            top_p = 0.95
        elif intent_type in ['add_task', 'complete_task', 'list_tasks']:
            # ��� ����� ����� ��������
            temperature = 0.6
            top_p = 1.0
        else:
            # �� ���������
            temperature = 0.7
            top_p = 1.0
        
        logger.info(f"Using temperature {temperature}, top_p {top_p} for intent type '{intent_type}'")
        
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
        data = {
            "model": "deepseek-chat",
            "messages": messages,
            "tools": TOOLS,
            "tool_choice": tool_choice,
            "temperature": temperature,
            "top_p": top_p,
        }
        logger.info(f"Sending request to DeepSeek API with {len(messages)} messages")
        # Retry loop for API call
        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=60)
                    ) as response:
                        logger.info(f"DeepSeek API response status: {response.status} (attempt {attempt + 1})")
                        if response.status == 200:
                            # �������� ����� - ������������
                            tool_calls = []
                            try:
                                result = await response.json()
                                message_response = result["choices"][0]["message"]
                                content = message_response.get("content", "")
                                # ����������� ����� tool calls
                                content = re.sub(r"<\|.*?\|>", "", content).strip()
                                content = re.sub(
                                    r"<|DSML|function_calls>.*?</|DSML|function_calls>",
                                    "",
                                    content,
                                    flags=re.DOTALL,
                                ).strip()
                                # ������� JSON ����� � tool_calls ���� ��� ������ � �����
                                content = re.sub(
                                    r'```json\s*\{.*?"tool_calls".*?\}\s*```', "", content, flags=re.DOTALL
                                ).strip()
                                content = re.sub(r'\{.*?"tool_calls".*?\}', "", content, flags=re.DOTALL).strip()
                                content = re.sub(
                                    r'\{.*?"name":\s*"".*?"arguments".*?\}', "", content, flags=re.DOTALL
                                ).strip()

                                # ��������� tool_calls � API response
                                tool_calls = message_response.get("tool_calls")
                            except Exception as e:
                                logger.error(f"Error parsing API response: {e}")
                                if attempt < max_retries:
                                    logger.info(f"Retrying API call due to parse error (attempt {attempt + 1})")
                                    await asyncio.sleep(1)
                                    continue
                                content = "��������, ��������� ������ ��� ��������� ������ �� ��. ���������� ��� ���."

                            # ��������� tool calls � �.�.
                            tool_results = []  # �������������� �������

                            # ���������, �� ������� �� AI JSON � ����� ������ tool_calls
                            json_in_text = re.search(r'\{.*?"name":\s*"(.*?)"\s*,\s*"arguments":\s*(\{.*?\})\s*\}', content, re.DOTALL)
                            if json_in_text and not tool_calls:
                                try:
                                    func_name = json_in_text.group(1)
                                    func_args = json.loads(json_in_text.group(2))
                                    tool_calls = [{
                                        'function': {
                                            'name': func_name,
                                            'arguments': json.dumps(func_args, ensure_ascii=False)
                                        }
                                    }]
                                    # ������� JSON �� ������
                                    content = re.sub(r'\{.*?"name":\s*".*?"\s*,\s*"arguments":\s*\{.*?\}\s*\}', '', content, flags=re.DOTALL).strip()
                                except Exception as e:
                                    pass

                            if tool_calls:

                                # ����-����������: ������������ tool calls �� ������ intent
                                corrected_tool_calls = post_process_tool_calls(intent, tool_calls, message)
                                if corrected_tool_calls:
                                    tool_calls = corrected_tool_calls

                                # ���� ��� ������ � ������, ���������� tool_calls � ������������ ��� ������� �����
                                if is_advice_question:
                                    tool_calls = None
                                else:
                                    # ��������� tool calls
                                    tool_results = []
                                    for tool_call in tool_calls:
                                        try:
                                            func_name = tool_call["function"]["name"]
                                            args = json.loads(tool_call["function"]["arguments"])
                                            logger.info(f"[TOOL CALL] Executing {func_name} with args: {args}")

                                            if func_name == "add_task":
                                                logger.info(f"[AI TOOL CALL] add_task called with reminder_time: {args.get('reminder_time')}, current user_now: {user_now}")
                                                result = add_task(
                                                    title=args.get("title", args.get("task_title", "������")),
                                                    description=args.get("description", ""),
                                                    reminder_time=args.get("reminder_time"),
                                                    user_id=user_id,
                                                    session=None,
                                                )
                                                tool_results.append({"function": func_name, "result": result})

                                            elif func_name == "complete_task":
                                                result = complete_task(
                                                    task_id=args.get("task_id"),
                                                    task_title=args.get("task_title"),
                                                    user_id=user_id,
                                                    session=None,
                                                )
                                                tool_results.append({"function": func_name, "result": result})

                                            elif func_name == "list_tasks":
                                                result = list_tasks(user_id=user_id, session=None)
                                                tool_results.append({"function": func_name, "result": result})

                                            elif func_name == "find_partners":
                                                result = find_partners(user_id=user_id, session=None)
                                                tool_results.append({"function": func_name, "result": result})

                                            elif func_name == "update_profile":
                                                result = update_profile(
                                                    city=args.get("city"),
                                                    company=args.get("company"),
                                                    position=args.get("position"),
                                                    interests=args.get("interests"),
                                                    user_id=user_id,
                                                    session=None,
                                                )
                                                tool_results.append({"function": func_name, "result": result})

                                            elif func_name == "delegate_task":
                                                result = delegate_task(
                                                    title=args.get("title"),
                                                    delegated_to_username=args.get("delegated_to_username"),
                                                    reminder_time=args.get("reminder_time"),
                                                    user_id=user_id,
                                                )
                                                tool_results.append({"function": func_name, "result": result})

                                            elif func_name == "delete_all_tasks":
                                                result = delete_all_tasks(user_id=user_id, session=None)
                                                tool_results.append({"function": func_name, "result": result})

                                            elif func_name == "delete_task":
                                                result = delete_task(
                                                    task_id=args.get("task_id"),
                                                    task_title=args.get("task_title"),
                                                    user_id=user_id,
                                                    session=None,
                                                )
                                                tool_results.append({"function": func_name, "result": result})

                                            elif func_name == "edit_task":
                                                result = edit_task(
                                                    task_id=args.get("task_id"),
                                                    title=args.get("title"),
                                                    description=args.get("description"),
                                                    reminder_time=args.get("reminder_time"),
                                                    user_id=user_id,
                                                    session=None,
                                                )
                                                tool_results.append({"function": func_name, "result": result})

                                            elif func_name == "check_subscription_status":
                                                result = check_subscription_status(user_id=user_id)
                                                tool_results.append({"function": func_name, "result": result})

                                            elif func_name == "create_subscription_payment":
                                                result = create_subscription_payment(user_id=user_id)
                                                tool_results.append({"function": func_name, "result": result})

                                            elif func_name == "brainstorm_ideas":
                                                result = brainstorm_ideas(
                                                    topic=args.get("topic"),
                                                    num_ideas=args.get("num_ideas", 5),
                                                    user_id=user_id
                                                )
                                                tool_results.append({"function": func_name, "result": result})

                                            else:
                                                logger.warning(f"[TOOL CALL] Unknown function: {func_name}")
                                                tool_results.append(
                                                    {"function": func_name, "result": f"����������� �������: {func_name}"}
                                                )

                                        except Exception as e:
                                            logger.error(f"[TOOL CALL] Error executing {func_name}: {e}")
                                            tool_results.append(
                                                {"function": func_name, "result": f"������ ����������: {str(e)}"}
                                            )

                                # ���������� ������������ ����� �� ������ ����������� tool calls
                                if tool_results:
                                    natural_responses = []
                                    has_list_tasks = False
                                    list_tasks_result = None

                                    for action in tool_results:
                                        result_text = action["result"]
                                        func_name = action["function"]

                                        # ���������, ���� �� list_tasks � �����������
                                        if func_name == "list_tasks":
                                            has_list_tasks = True
                                            list_tasks_result = result_text

                                        if "��������� ������" in result_text:
                                            match = re.search(r"��������� ������ '([^']+)' \(ID: (\d+)\)", result_text)
                                            if match:
                                                title = match.group(1)
                                                task_id = int(match.group(2))
                                                
                                                # �������� ������������ �� ���� ������
                                                from models import Session, Task
                                                session_db = Session()
                                                try:
                                                    task = session_db.query(Task).filter_by(id=task_id).first()
                                                    recommendations = []
                                                    if task and task.recommendations:
                                                        import json
                                                        try:
                                                            recommendations = json.loads(task.recommendations)
                                                        except:
                                                            pass
                                                    
                                                    # ��������� ������� �����
                                                    if recommendations:
                                                        rec_text = " ������������: " + ", ".join(recommendations[:3])
                                                        natural = f'������ "{title}" ��������� � �������������.{rec_text}'
                                                    else:
                                                        natural = f'������ "{title}" ��������� � �������������.'
                                                    
                                                    natural_responses.append(natural)
                                                finally:
                                                    session_db.close()
                                            else:
                                                natural_responses.append(result_text)

                                        elif "��������� ������" in result_text:
                                            match = re.search(r"��������� ������ '([^']+)'", result_text)
                                            if match:
                                                title = match.group(1)
                                                natural = f'�������, ������� ������ "{title}" ��� �����������! ��� ������ ��� ������. ������ ����� ����������������, ��� ���� ������� ���������, � �������� � ��������� �������. ���� �� �����, ������� ����� ������� �� ���������� ���� ������? ����� ����, ����� �������� ���������� ��� ������������� ���-�� �����?'
                                                natural_responses.append(natural)
                                            else:
                                                natural_responses.append(result_text)

                                        elif "������:" in result_text:
                                            # ��� list_tasks ��������� ����� ������ ������ �������� ������
                                            natural = enrich_task_list_with_insights(result_text, user_id)
                                            natural_responses.append(natural)

                                        elif (
                                            "������� ��������:" in result_text
                                            or "�������� �������" in result_text.lower()
                                        ):
                                            natural_responses.append(result_text)

                                        elif "������� ��������" in result_text:
                                            # ������ ������ ����������
                                            if "added_interests:" in result_text:
                                                match = re.search(r"added_interests:([^;]+)", result_text)
                                                if match:
                                                    items = match.group(1).strip()
                                                    natural = f"�������! ������� � ���� ��������: {items}. ������ � ����� �������� ��� ���� ����� � �������� ����������� � ���������� ����������� ����������."
                                                    natural_responses.append(natural)
                                                else:
                                                    natural_responses.append("������� ��������! ������� ����� ��������.")
                                            
                                            elif "removed_interests:" in result_text:
                                                match = re.search(r"removed_interests:([^;]+)", result_text)
                                                if match:
                                                    items = match.group(1).strip()
                                                    natural = f"�����, ����� �� ���������: {items}. ������� ���� �������."
                                                    natural_responses.append(natural)
                                                else:
                                                    natural_responses.append("������� ��������! ����� ��������.")
                                            
                                            elif "changed_city:" in result_text:
                                                match = re.search(r"changed_city:([^->]+)->([^;]+)", result_text)
                                                if match:
                                                    old_city = match.group(1).strip()
                                                    new_city = match.group(2).strip()
                                                    natural = f"������� ����� � {old_city} �� {new_city}! ������ ���� ������ ��� ���� ����� � ������� � {new_city}."
                                                    natural_responses.append(natural)
                                                else:
                                                    natural_responses.append("������� ��������! ������� �����.")
                                            
                                            elif "changed_company:" in result_text:
                                                match = re.search(r"changed_company:([^->]+)->([^;]+)", result_text)
                                                if match:
                                                    new_company = match.group(2).strip()
                                                    natural = f"������� ����� ����� ������: {new_company}. ������� ��������!"
                                                    natural_responses.append(natural)
                                                else:
                                                    natural_responses.append("������� ��������! ������� ��������.")
                                            
                                            elif "added_skills:" in result_text:
                                                match = re.search(r"added_skills:([^;]+)", result_text)
                                                if match:
                                                    items = match.group(1).strip()
                                                    natural = f"�������! ������� � ������: {items}. ��� ������� ����� ������� � �����, ������� ����� ����� �����������."
                                                    natural_responses.append(natural)
                                                else:
                                                    natural_responses.append("������� ��������! ������� ������.")
                                            
                                            elif "added_goals:" in result_text:
                                                match = re.search(r"added_goals:([^;]+)", result_text)
                                                if match:
                                                    items = match.group(1).strip()
                                                    natural = f"������� ����� ����: {items}. ���� �������� ���� ��������� � ���!"
                                                    natural_responses.append(natural)
                                                else:
                                                    natural_responses.append("������� ��������! ������� ����.")
                                            
                                            else:
                                                # ����� ������ ���� �� ������� ����������
                                                natural_responses.append("������� ��������! �������� ���������.")

                                        elif "������" in result_text and "������������" in result_text:
                                            natural = "�������, ������ ������������! � �������� ����������."
                                            natural_responses.append(natural)

                                        elif "������� ��� ������" in result_text:
                                            natural = "������ ��� ���� ������. ������ ������ ���� - ����� �������� � ������� �����!"
                                            natural_responses.append(natural)

                                        elif "������" in result_text and "�������" in result_text:
                                            match = re.search(r"������ '([^']+)' �������", result_text)
                                            if match:
                                                title = match.group(1)
                                                natural = f'������ ������ "{title}". ��� ������?'
                                                natural_responses.append(natural)
                                            else:
                                                natural_responses.append(result_text)

                                        elif "���� ��� ����" in result_text:
                                            natural = f"{result_text}\n\n�������, ��� ���� �������! ���� ����� �������� �����-�� ��� ������������� ������ ��������� - ��� �����."
                                            natural_responses.append(natural)

                                        else:
                                            natural_responses.append(result_text)

                                    # ��� list_tasks ������ ��� �������� ����

                                    final_content = "\n".join(natural_responses)
                                    # ��������� ����� ������������ ����������
                                    final_content = enrich_response_with_engagement(
                                        final_content, user_id, original_message
                                    )

                                    # Enforcement �������� - AI ������ �������� �����������
                                    # intent_type = "list_tasks" if has_list_tasks else None
                                    # final_content = await enforce_prompt_compliance(
                                    #     final_content, intent_type, user_id, context,
                                    #     system_prompt, messages, url, headers
                                    # )

                                    logger.info(
                                        f"[TOOL CALLS] Processed {len(tool_results)} tool calls, returning natural response"
                                    )
                                    return final_content
                            else:
                                # tool_calls ���� ��������������� ��� ������� ������, ��������� � ������� ���������
                                pass

                    # ��� ������� ������������ AI, ��� �������������� ���������
                    logger.info("[AI ONLY] All requests handled by AI without forced triggers")

                    # SMART FALLBACK: ���������, ����� �� ��������� ����� fallback (use improved version if available)
                    try:
                        if PROMPTS_V2_AVAILABLE:
                            fallback_result = improved_fallback(
                                intent, tool_calls if 'tool_calls' in locals() else None, 
                                content, original_message, user_id
                            )
                            logger.info(f"[PROMPTS V2] Fallback actions: {len(fallback_result)}")
                        else:
                            fallback_result = smart_fallback_handler(original_message, mentions_str, user_id, content)
                            logger.info(f"[LEGACY] Fallback actions: {len(fallback_result)}")
                        print(
                            f"[DEBUG] Fallback result: {len(fallback_result) if fallback_result else 0} actions"
                        )  # DEBUG
                        if fallback_result:
                            logger.info(
                                f"[SMART FALLBACK] Applied {len(fallback_result)} fallback actions for user {user_id}"
                            )

                            # ������������ ���������� fallback ���������� tool calls
                            natural_responses = []
                            for action in fallback_result:
                                result_text = action["result"]
                                func_name = action["function"]

                                if "��������� ������" in result_text:
                                    match = re.search(
                                        r"��������� ������ '([^']+)' \(ID: \d+\) � ������������ �� ([^)]+)", result_text
                                    )
                                    if match:
                                        title = match.group(1)
                                        time_str = match.group(2)
                                        natural = f'�������, ������� ������ "{title}" � ������������ �� {time_str}.'
                                        natural_responses.append(natural)
                                    else:
                                        natural_responses.append(result_text)

                                elif "��������� ������" in result_text:
                                    match = re.search(r"��������� ������ '([^']+)'", result_text)
                                    if match:
                                        title = match.group(1)
                                        natural = f'�������, ������� ������ "{title}" ��� �����������! ??'
                                        natural_responses.append(natural)
                                    else:
                                        natural_responses.append(result_text)

                                elif "������:" in result_text:
                                    # �� ��������� �����, ������ ����� �������� ��������
                                    pass

                                elif "������� ��� ������" in result_text:
                                    natural = (
                                        "������ ��� ���� ������. ������ ������ ���� - ����� �������� � ������� �����!"
                                    )
                                    natural_responses.append(natural)

                                elif "������" in result_text and "������������" in result_text:
                                    natural = "�������, ������ ������������! � �������� ����������."
                                    natural_responses.append(natural)

                                else:
                                    natural_responses.append(result_text)

                            # ���������, ���� �� list_tasks � ����������� fallback
                            has_list_tasks = any(action["function"] == "list_tasks" for action in fallback_result)
                            list_tasks_result = None
                            if has_list_tasks:
                                for action in fallback_result:
                                    if action["function"] == "list_tasks":
                                        list_tasks_result = action["result"]
                                        break

                            # ��� list_tasks ������ ��������� ��������� - ������� ������ ��� �������� ��� �������
                            if has_list_tasks and list_tasks_result:
                                natural_responses.append(list_tasks_result)
                            
                            # ��������� ��������� �������
                            final_content = "\n".join(natural_responses)
                            
                            # Enforcement �������� - AI ������ �������� �����������
                            # intent_type = "list_tasks" if has_list_tasks else None
                            # final_content = await enforce_prompt_compliance(
                            #     final_content, intent_type, user_id, context,
                            #     system_prompt, messages, url, headers
                            # )
                            
                            return final_content
                    except Exception as e:
                        logger.error(f"[SMART FALLBACK] Error in fallback handler: {e}")

                    # ���� forced calls �� ���������, ������������ ������� ����� AI
                    # ������������ ������� ����� AI ��� tool calls
                    logger.info("[TOOL CALLS] Tool calls completed, 0 results. Generating natural response...")
                    original_content = message_response.get("content", "")
                    content = original_content

                    # ��� ������� ������� ������ �������� ������������, ��� �������������� �������
                    content = replace_placeholders(content, user_now, current_time_str)

                    # ����������� ��������: ���� content ������ ��� ������� ��������
                    if not content or len(content.strip()) < 3:
                        print(
                            f"[DEBUG] Content is empty or too short: '{content}', len={len(content.strip())}"
                        )  # DEBUG
                        logger.warning(f"[EMPTY RESPONSE] Original: '{original_content[:100]}...', returning original")
                        content = original_content.strip()
                        if not content:
                            logger.warning("[RETRY] Response empty, retrying with explicit instruction")
                            retry_system = (
                                system_prompt
                                + "\n\n���������� �����:\n1. �� ��������� JSON, code blocks ��� ����������� ����\n2. ������� ������ ������� �������\n3. ���� ������ ������ - ����� �� ���� � �������� ����� �������\n4. ������� 20 ���� � ������\n5. ���� ����������� � ����������!"
                            )

                            retry_messages = [{"role": "system", "content": retry_system}]
                            if context:
                                for item in context:
                                    if "user" in item:
                                        retry_messages.append({"role": "user", "content": item["user"]})
                                    if "assistant" in item:
                                        retry_messages.append({"role": "assistant", "content": item["assistant"]})
                            retry_messages.append({"role": "user", "content": original_message})

                            async with aiohttp.ClientSession() as retry_session:
                                async with retry_session.post(
                                    url,
                                    headers=headers,
                                    json={
                                        "model": "deepseek-reasoner",
                                        "messages": retry_messages,
                                        "temperature": 0.3,
                                    },
                                    timeout=aiohttp.ClientTimeout(total=120),
                                ) as retry_response:
                                    retry_result = await retry_response.json()
                                    retry_content = retry_result["choices"][0]["message"]["content"]
                                    retry_content = replace_placeholders(retry_content, user_now, current_time_str)
                                    content = retry_content.strip()
                                    logger.info(f"[RETRY] Got retry content: '{content[:100]}...'")
                                    if retry_content and len(retry_content.strip()) >= 3:
                                        content = retry_content
                                    else:
                                        content = "������, ��������� ������!"
                        else:
                            logger.info(f"[RECOVERED] Using original content: '{content[:100]}...'")

                    # ���� ��� ��� ������ ����� retry
                    if not content:
                        content = "������, ��������� ������!"

                    # ��������� ����� ������������ ����������
                    content = enrich_response_with_engagement(content, user_id, original_message)

                    # Enforcement �������� - AI ������ �������� ����������� ��� �������������� API �������

                    # ������� �� ����������� ������� ����� ���������
                    # �� ��������� clean_technical_details ��� ������� ������� AI!
                    
                    # ������� �������� ������
                    response_quality = {
                        'length': len(content),
                        'has_questions': '?' in content,
                        'has_tools': bool(tool_calls),
                        'intent_type': intent.get('type', 'unknown'),
                        'user_id': user_id
                    }
                    logger.info(f"[RESPONSE QUALITY] {response_quality}")
                    
                    # ��������� ������: ���� ����� ������� �������� ��� ������, ���� fallback
                    if not content or len(content.strip()) < 10:
                        logger.warning(f"[FALLBACK] Empty or too short response, using fallback")
                        content = improved_fallback(intent, tool_calls, content, message, user_id)
                    
                    # �������������� ������� ��������� ������ ��� ������������
                    # ������� ������, ������������, ���������� - ������ ������ ����� AI
                    
                    return content

            except Exception as e:
                import traceback

                logger.error(f"Error in chat_with_ai: {e}")
                logger.error(f"Error type: {type(e).__name__}")
                logger.error(f"Traceback:\n{traceback.format_exc()}")
                # ��������� ����� ������ ��� �������
                tb = traceback.extract_tb(e.__traceback__)
                if tb:
                    last_frame = tb[-1]
                    logger.error(f"Error location: {last_frame.filename}:{last_frame.lineno} in {last_frame.name}")
                return f"������: {str(e)} [v2]"

    except Exception as e:
        import traceback

        logger.error(f"Error in chat_with_ai: {e}")
        logger.error(f"Error type: {type(e).__name__}")
        logger.error(f"Traceback:\n{traceback.format_exc()}")
        # ��������� ����� ������ ��� �������
        tb = traceback.extract_tb(e.__traceback__)
        if tb:
            last_frame = tb[-1]
            logger.error(f"Error location: {last_frame.filename}:{last_frame.lineno} in {last_frame.name}")
        return f"������: {str(e)} [v2]"


async def generate_reminder(user_id, task_title):
    """���������� ����� ����������� � ������"""
    try:
        # �������� ������ ������������
        user_memory = ""
        if user_id:
            from models import Session, User

            session = Session()
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if user and user.memory:
                try:
                    decrypted = decrypt_data(user.memory)
                    user_memory = f"\n���������� � ������������: {decrypted}"
                except (Exception,):
                    user_memory = ""
            session.close()

        # ���������� ������ ��������������� ������ ��� ���� AI-���������
        from datetime import datetime
        import pytz
        user_now = datetime.now(pytz.UTC)
        current_time_str = user_now.strftime("%H:%M")
        user_username = "������������"  # ����� �������� �� ���� ���� �����
        mentions_str = ""

        base_prompt = get_optimized_prompt_final(user_now, current_time_str, user_username, mentions_str, user_memory)

        # ��������������� ������� ��� ���� AI-���������:
        system_prompt = f"{base_prompt}\n\n��������������� ������� ��� ���� AI-���������:\n"
        system_prompt += "������ ���������� �������� ��� ����������� �������\n"
        system_prompt += "���������� �������� � ����� ���������� ������������\n"
        system_prompt += "���� �������������������, ��������� ���������� � ������������\n"
        system_prompt += "������������ ��������: ��������� ��� ��������� �����, �������������� ��������\n"
        system_prompt += "2-4 �����������, ����� ������� ��� � ������\n"
        system_prompt += "���� ���� ����������� ���������� �� ������ ������������, ��������� �\n"

        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"������� � ������: {task_title}"},
        ]

        data = {"model": "deepseek-reasoner", "messages": messages}
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=60)
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    content = result["choices"][0]["message"]["content"]
                    # �������� ������������ �� �������� ��������
                    content = replace_placeholders(
                        content, datetime.now(pytz.UTC), datetime.now(pytz.UTC).strftime("%H:%M")
                    )
                    content = clean_technical_details(content)
                    # ��������� ����� ������������ ����������
                    content = enrich_response_with_engagement(content, user_id, task_title)
                    
                    # ��������� � ���������� ���������� �������
                    is_compliant, issues = validate_response_compliance(content, "reminder")
                    if not is_compliant:
                        logger.warning(f"[COMPLIANCE] Reminder response not compliant: {issues}")
                        # ���������� �����������
                        content = await enforce_prompt_compliance(
                            content, "reminder", user_id, None, system_prompt, messages, url, headers
                        )
                    
                    return content
                else:
                    return "������ ��������� �����������."
    except Exception as e:
        print(f"Error in generate_reminder: {e}")
        return f"����������� � '{task_title}'."


async def generate_result_check(user_id, task_title):
    """���������� ������ � ���������� ���������� ������"""
    try:
        # �������� ������ ������������
        user_memory = ""
        if user_id:
            from models import Session, User

            session = Session()
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if user and user.memory:
                try:
                    decrypted = decrypt_data(user.memory)
                    user_memory = f"\n���������� � ������������: {decrypted}"
                except (Exception,):
                    user_memory = ""
            session.close()

        # ���������� ������ ��������������� ������ ��� ���� AI-���������
        from datetime import datetime
        import pytz
        user_now = datetime.now(pytz.UTC)
        current_time_str = user_now.strftime("%H:%M")
        user_username = "������������"
        mentions_str = ""

        base_prompt = get_extended_system_prompt(user_now, current_time_str, user_username, mentions_str, user_memory)

        # ��������������� ������� ��� ���� AI-���������:
        system_prompt = f"{base_prompt}\n\n��������������� ������� ��� ���� AI-���������:\n"
        system_prompt += "������ ���������� �������� ��� ����������� �������\n"
        system_prompt += "���������� �������� � ����� ���������� ������������\n"
        system_prompt += "���� �������������������, ��������� ���������� � ������������\n"
        system_prompt += "������������ ��������: ��������� ��� ��������� �����, �������������� ��������\n"
        system_prompt += "2-4 �����������, ����� ������� ��� � ������\n"
        system_prompt += "���� ���� ����������� ���������� �� ������ ������������, ��������� �\n"

        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}

        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": f"������ � ���������� ���������� ������ '{task_title}'. ����� � �������, ����������, ����������.",
            },
        ]

        data = {"model": "deepseek-reasoner", "messages": messages}
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=60)
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    content = result["choices"][0]["message"]["content"]
                    # �������� ������������ �� �������� ��������
                    content = replace_placeholders(
                        content, datetime.now(pytz.UTC), datetime.now(pytz.UTC).strftime("%H:%M")
                    )
                    content = clean_technical_details(content)
                    # ��������� ����� ������������ ����������
                    content = enrich_response_with_engagement(content, user_id, task_title)
                    
                    # ��������� � ���������� ���������� �������
                    is_compliant, issues = validate_response_compliance(content, "result_check")
                    if not is_compliant:
                        logger.warning(f"[COMPLIANCE] Result check response not compliant: {issues}")
                        # ���������� �����������
                        content = await enforce_prompt_compliance(
                            content, "result_check", user_id, None, system_prompt, messages, url, headers
                        )
                    
                    return content
                else:
                    return "������ ��������� �������."
    except Exception as e:
        print(f"Error in generate_result_check: {e}")
        return f"��������� ������ '{task_title}'?"


async def generate_proactive_message(user_id):
    """���������� ����������� ���������, ���� ��� ����� �� ��������� ���"""
    try:
        # �������� ������ ������������, ����� ������ � ������� ������
        user_memory = ""
        plans_info = ""
        tasks_info = ""
        if user_id:
            from models import Session, User, UserProfile, Task

            session = Session()
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if user is None:
                return "�������� ������."
            if user and user.memory:
                try:
                    decrypted = decrypt_data(user.memory)
                    user_memory = f"\n���������� � ������������: {decrypted}"
                except (Exception,):
                    user_memory = ""
            # �������� ������� ������������
            user_profile = session.query(UserProfile).filter_by(user_id=user.id).first()
            if user_profile and user_profile.interests:
                # ����� ����� ������ �������������, ����������� � ����������
                profiles = session.query(UserProfile).filter(UserProfile.user_id != user.id).all()
                tips = []
                for p in profiles:
                    if p.current_plans and p.contact_info != f"user{user_id}":
                        for interest in user_profile.interests.split(","):
                            interest_words = interest.strip().lower().split()
                            if any(word in p.current_plans.lower() for word in interest_words):
                                tips.append(
                                    f"@{p.contact_info} ������� {p.current_plans.split(',')[0]} - ����� ���� ��������� � ������ ���������� � {interest.strip()}."
                                )
                                break
                if tips:
                    plans_info = "\n����� �����: " + " ".join(tips[:2])
            # �������� ������� ������
            tasks = session.query(Task).filter_by(user_id=user.id).all()
            pending_tasks = [t.title for t in tasks if t.status in ["pending", "in_progress"]]
            if pending_tasks:
                tasks_info = f"\n������� ������������� ������: {', '.join(pending_tasks[:3])}"
            session.close()

        # ���������� ������ ��������������� ������ ��� ���� AI-���������
        from datetime import datetime
        import pytz
        user_now = datetime.now(pytz.UTC)
        current_time_str = user_now.strftime("%H:%M")
        user_username = "������������"
        mentions_str = ""

        base_prompt = get_optimized_prompt_final(user_now, current_time_str, user_username, mentions_str, user_memory + plans_info + tasks_info)

        # ��������������� ������� ��� ���� AI-���������:
        system_prompt = f"{base_prompt}\n\n��������������� ������� ��� ���� AI-���������:\n"
        system_prompt += "������ ���������� �������� ��� ����������� �������\n"
        system_prompt += "���������� �������� � ����� ���������� ������������\n"
        system_prompt += "���� �������������������, ��������� ���������� � ������������\n"
        system_prompt += "������������ ��������: ��������� ��� ��������� �����, �������������� ��������\n"
        system_prompt += "2-4 �����������, ����� ������� ��� � ������\n"
        system_prompt += "���� ���� ����������� ���������� �� ������ ������������, ��������� �\n"

        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}

        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": "� ������������ ��� ����� �� ��������� ���. ������ ���������� ����������� ���������.",
            },
        ]

        data = {"model": "deepseek-reasoner", "messages": messages}
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=60)
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    content = result["choices"][0]["message"]["content"]
                    # �������� ������������ �� �������� ��������
                    content = replace_placeholders(
                        content, datetime.now(pytz.UTC), datetime.now(pytz.UTC).strftime("%H:%M")
                    )
                    content = clean_technical_details(content)
                    # ����������� ��������� ��� �����������, �� ����� �������
                    content = enrich_response_with_engagement(content, user_id, "")
                    
                    # ��������� � ���������� ���������� �������
                    is_compliant, issues = validate_response_compliance(content, "proactive")
                    if not is_compliant:
                        logger.warning(f"[COMPLIANCE] Proactive message response not compliant: {issues}")
                        # ���������� �����������
                        content = await enforce_prompt_compliance(
                            content, "proactive", user_id, None, system_prompt, messages, url, headers
                        )
                    
                    return content
                else:
                    return "������ ��������� ���������."
    except Exception as e:
        print(f"Error in generate_proactive_message: {e}")
        return "�������� ������."


async def generate_daily_report(user_id):
    """���������� ���������� ����� � �������"""
    try:
        # �������� ������ ������������
        from models import Session, Task

        session = Session()
        tasks = session.query(Task).filter_by(user_id=user_id).all()
        session.close()

        completed = [t for t in tasks if t.status == "completed"]
        pending = [t for t in tasks if t.status in ["pending", "in_progress"]]

        # �������� ������ ������������
        user_memory = ""
        if user_id:
            from models import Session, User

            session = Session()
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if user and user.memory:
                try:
                    decrypted = decrypt_data(user.memory)
                    user_memory = f"\n���������� � ������������: {decrypted}"
                except (Exception,):
                    user_memory = ""
            session.close()

        # ���������� ������ ��������������� ������ ��� ���� AI-���������
        from datetime import datetime
        import pytz
        user_now = datetime.now(pytz.UTC)
        current_time_str = user_now.strftime("%H:%M")
        user_username = "������������"
        mentions_str = ""

        base_prompt = get_optimized_prompt_final(user_now, current_time_str, user_username, mentions_str, user_memory)

        # ��������������� ������� ��� ���� AI-���������:
        system_prompt = f"{base_prompt}\n\n��������������� ������� ��� ���� AI-���������:\n"
        system_prompt += "������ ���������� �������� ��� ����������� �������\n"
        system_prompt += "���������� �������� � ����� ���������� ������������\n"
        system_prompt += "���� �������������������, ��������� ���������� � ������������\n"
        system_prompt += "������������ ��������: ��������� ��� ��������� �����, �������������� ��������\n"
        system_prompt += "2-4 �����������, ����� ������� ��� � ������\n"
        system_prompt += "���� ���� ����������� ���������� �� ������ ������������, ��������� �\n"

        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"������ �����: ��������� {len(completed)}, ������� {len(pending)}"},
        ]

        data = {"model": "deepseek-reasoner", "messages": messages}
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=60)
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    content = result["choices"][0]["message"]["content"]
                    # �������� ������������ �� �������� ��������
                    content = replace_placeholders(
                        content, datetime.now(pytz.UTC), datetime.now(pytz.UTC).strftime("%H:%M")
                    )
                    content = clean_technical_details(content)
                    
                    # ��������� � ���������� ���������� �������
                    is_compliant, issues = validate_response_compliance(content, "daily_report")
                    if not is_compliant:
                        logger.warning(f"[COMPLIANCE] Daily report response not compliant: {issues}")
                        # ���������� �����������
                        content = await enforce_prompt_compliance(
                            content, "daily_report", user_id, None, system_prompt, messages, url, headers
                        )
                    
                    return content
                else:
                    return "������ ��������� ������."
    except Exception as e:
        print(f"Error in generate_daily_report: {e}")
        return "����� � �������."


async def generate_overdue_reminder(user_id, overdue_tasks, escalation_level=1):
    """���������� ����������� � ������������ �������"""
    try:
        # ��������� ��� �������� Task, ��� � ��������
        if overdue_tasks and isinstance(overdue_tasks[0], dict):
            task_titles = [t.get('title', '������') for t in overdue_tasks]
        else:
            task_titles = [t.title for t in overdue_tasks]
        # �������� ������ ������������
        user_memory = ""
        if user_id:
            from models import Session, User

            session = Session()
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if user and user.memory:
                try:
                    decrypted = decrypt_data(user.memory)
                    user_memory = f"\n���������� � ������������: {decrypted}"
                except (Exception,):
                    user_memory = ""
            session.close()

        # ���������� ������ ��������������� ������ ��� ���� AI-���������
        from datetime import datetime
        import pytz
        user_now = datetime.now(pytz.UTC)
        current_time_str = user_now.strftime("%H:%M")
        user_username = "������������"
        mentions_str = ""

        base_prompt = get_optimized_prompt_final(user_now, current_time_str, user_username, mentions_str, user_memory)

        # ��������������� ������� ��� ���� AI-���������:
        system_prompt = f"{base_prompt}\n\n��������������� ������� ��� ���� AI-���������:\n"
        system_prompt += "������ ���������� �������� ��� ����������� �������\n"
        system_prompt += "���������� �������� � ����� ���������� ������������\n"
        system_prompt += "���� �������������������, ��������� ���������� � ������������\n"
        system_prompt += "������������ ��������: ��������� ��� ��������� �����, �������������� ��������\n"
        system_prompt += "2-4 �����������, ����� ������� ��� � ������\n"
        system_prompt += "���� ���� ����������� ���������� �� ������ ������������, ��������� �\n"

        # ���������� ��� � ����������� �� ������ ���������
        if escalation_level == 1:
            tone_instruction = "���� �����������, �� �����������. ������� � �������� ���������� �����."
        elif escalation_level == 2:
            tone_instruction = "���� ����� �������. ��������� ���������� ����������� ������������."
        else:  # 3+
            tone_instruction = "���� ����� ������� � ������������. �������� ���������� ������������ � ������."

        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}

        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": f"������� � ������������ �������: {', '.join(task_titles)}. {tone_instruction} �������� �������� �������.",
            },
        ]

        data = {"model": "deepseek-reasoner", "messages": messages}
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=60)
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    content = result["choices"][0]["message"]["content"]
                    # �������� ������������ �� �������� ��������
                    content = replace_placeholders(
                        content, datetime.now(pytz.UTC), datetime.now(pytz.UTC).strftime("%H:%M")
                    )
                    content = clean_technical_details(content)
                    
                    # ��������� � ���������� ���������� �������
                    is_compliant, issues = validate_response_compliance(content, "overdue")
                    if not is_compliant:
                        logger.warning(f"[COMPLIANCE] Overdue reminder response not compliant: {issues}")
                        # ���������� �����������
                        content = await enforce_prompt_compliance(
                            content, "overdue", user_id, None, system_prompt, messages, url, headers
                        )
                    
                    return content
                else:
                    return "������ ��������� �����������."
    except Exception as e:
        print(f"Error in generate_overdue_reminder: {e}")
        return "������������ ������."


# ������� ��� ������ � ��������
def list_tasks(user_id=None, session=None):
    """���������� ������ ����� ������������ � ������ ��������� �������"""
    from models import Task, User
    from sqlalchemy import or_

    if session is None:
        from models import Session

        session = Session()
        close_session = True
    else:
        close_session = False

    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "������������ �� ������"

        # �������� ������ ������������ ��� �������������� ���
        query = session.query(Task).filter(Task.user_id == user.id)
        if user.username and user.username.strip():
            query = query.union(
                session.query(Task).filter(Task.delegated_to_username.ilike(user.username))
            )
        tasks = query.all()

        if not tasks:
            return "� ��� ��� �������� �����. �������� ������ ������ - ������ �������� ��� ����� �������!"

        # ��������� ��������� ������ ��� ������ � ��������������
        active_tasks = [t for t in tasks if t.status != "completed"]
        completed_tasks = [t for t in tasks if t.status == "completed"]
        user_username_lower = user.username.lower() if user.username else ""
        delegated_to_me = [
            t
            for t in active_tasks
            if t.delegated_to_username and user_username_lower and t.delegated_to_username.lower() == user_username_lower
        ]
        delegated_by_me = [
            t
            for t in active_tasks
            if t.delegated_to_username and user_username_lower and t.delegated_to_username.lower() != user_username_lower
        ]
        my_tasks = [t for t in active_tasks if not t.delegated_to_username]

        from datetime import datetime
        import pytz

        # ���������� timezone ������������
        user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
        now = datetime.now(user_tz)

        # ������� ������������ �����
        overdue_count = 0
        for task in active_tasks:
            if task.reminder_time:
                try:
                    reminder_dt = task.reminder_time.replace(tzinfo=pytz.UTC).astimezone(user_tz)
                    if reminder_dt < now:
                        overdue_count += 1
                except:
                    pass

        # ��������� ������� �����
        if not active_tasks:
            return "��� �������� �����. ��� ����������?"

        result = f"� ��� {len(active_tasks)} {'������' if len(active_tasks) == 1 else '�����'}\n\n"

        # ���������� ������ ������ 3 ������
        tasks_to_show = my_tasks[:3]
        if tasks_to_show:
            result += "���� ������:\n"
            for task in tasks_to_show:
                reminder_info = ""
                if task.reminder_time:
                    try:
                        reminder_dt = task.reminder_time.replace(tzinfo=pytz.UTC).astimezone(user_tz)
                        if reminder_dt < now:
                            delta = now - reminder_dt
                            days = delta.days
                            hours = (delta.seconds // 3600)
                            if days > 0:
                                reminder_info = f" - ���������� �� {days} � {hours} �" if hours else f" - ���������� �� {days} �"
                            else:
                                reminder_info = f" - ���������� �� {hours} �"
                        else:
                            reminder_info = f" - {reminder_dt.strftime('%d.%m %H:%M')}"
                    except:
                        pass
                result += f"- {task.title}{reminder_info}\n"
            
            if len(my_tasks) > 3:
                result += f"...� ��� {len(my_tasks) - 3}\n"

        # ������� ������������
        if overdue_count > 0:
            result += f"\n\n{overdue_count} ������������ - ����� �����������"
        elif len(active_tasks) == 1:
            result += "\n\n���� ������ - �������� �����"
        elif len(active_tasks) > 5:
            result += "\n\n����� ����� - �������������"

        return result.strip()
    except Exception as e:
        print(f"Error listing tasks: {e}")
        return "������ ��������� ������ �����"
    finally:
        if close_session:
            session.close()


def enrich_task_list_with_insights(task_list_text, user_id):
    """��������� ������ ����� ����������� insights � ��������"""
    from models import Session, User, Task
    from datetime import datetime
    import pytz
    
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return task_list_text
            
        # �������� ������ ��� �������
        tasks = session.query(Task).filter(
            Task.user_id == user.id,
            Task.status != "completed"
        ).all()
        
        # ����������� ��������
        insights = []
        
        # 1. ������ �������������
        task_count = len(tasks)
        if task_count == 0:
            insights.append("�������� ������ - ��� ������ ���������! ������ �� ��� ������ ����������, ��� ����� �������, ������ ��� ��� ���������.")
        elif task_count == 1:
            insights.append("���� ������ - �������� ��� ������. ������ �� ��� �������� � ������� �������, ������ ��������� ����.")
        elif task_count > 5:
            insights.append(f"{task_count} ����� - ����� ����������������. � ������ ������������, ����� �� ������ ����� �� ����.")
        
        # 2. ������ ������������ �����
        overdue_count = 0
        user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
        now = datetime.now(user_tz)
        
        for task in tasks:
            if task.reminder_time:
                try:
                    reminder_dt = task.reminder_time.replace(tzinfo=pytz.UTC).astimezone(user_tz)
                    if reminder_dt < now:
                        overdue_count += 1
                except:
                    pass
        
        if overdue_count > 0:
            insights.append(f"{overdue_count} ������������ �����. ������ ��� ����� ������� ������ � ������ ������� - ������ ����� �������� ��������.")
        
        # 3. ������ �������������
        delegated_count = sum(1 for t in tasks if t.delegated_to_username)
        if delegated_count > 0:
            insights.append(f"�� ����������� {delegated_count} ����� - ����� ������! ������ ��� ����������� ������ ������, ������ ������� ��������.")
        
        # 4. ����������� �� �����������
        tasks_without_time = sum(1 for t in tasks if not t.reminder_time)
        if tasks_without_time > 0:
            insights.append(f"{tasks_without_time} ����� ��� ������� - ������� �����, ����� �������� ������ � ��������� ������.")
        
        # ��������� ��������� �����
        result = task_list_text
        if insights:
            result += "\n\n������ ��������: " + ", ".join(insights[:3])
            result += "\n\n��� ��������������? ��� ����� ������ ��������� ��� ���������� ������ ��� �������� ��������?"
        
        # ��������� ���������� ����������� �� ������ �������
        if user_profile and (user_profile.interests or user_profile.skills):
            social_suggestions = []
            
            if user_profile.interests:
                interests_list = [i.strip() for i in user_profile.interests.split(',')]
                if any(i.lower() in ['���', '�����', '������', '����'] for i in interests_list):
                    social_suggestions.append("���� ������� � ������ - ���� ����� ��������� ��� ���������� ����������")
                if any(i.lower() in ['����������������', 'it', '����������'] for i in interests_list):
                    social_suggestions.append("����������� IT - ������ ������ ��� ������ ������ ��� ���������� ��������")
                if any(i.lower() in ['�����������', '����', '�����', '������'] for i in interests_list):
                    social_suggestions.append("������ ���������� ����������� - ������� �������� ��� ������� � ���� ��� �����")
            
            if social_suggestions:
                result += "\n\n���������� �����������: " + ", ".join(social_suggestions[:2])
                result += "\n\n������ ����� ���������������� ����� ������?"
        
        return result
        
    except Exception as e:
        print(f"Error enriching task list: {e}")
        return task_list_text
    finally:
        session.close()


# DUPLICATE cancel_subscription REMOVED - Using version at line 2149 (calls subscription_service)

def brainstorm_ideas(topic, num_ideas=5, user_id=None):
    """���������� ���� ��� ������� �������� ��� ��������� ��������"""
    import requests
    from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL

    prompt = f"""
    ���������� {num_ideas} ���������� ���� ��� ����: "{topic}"
    
    ���� ������ ����:
    - ����������� � ������������
    - ��������������
    - ��������� ������������ �������
    
    ������ ������: ��������������� ������ ����, ������ � ������� ��������� ������ ��� ������.
    """

    try:
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        data = {
            "model": DEEPSEEK_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 1000,
            "temperature": 0.7
        }
        response = requests.post(url, headers=headers, json=data, timeout=30)
        response.raise_for_status()
        result = response.json()
        ideas = result["choices"][0]["message"]["content"].strip()
        return f"���� ��� ���� '{topic}':\n\n{ideas}"
    except Exception as e:
        return f"������ ��������� ����: {str(e)}"
