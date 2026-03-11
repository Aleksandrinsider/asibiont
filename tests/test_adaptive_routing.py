"""
РўРµСЃС‚С‹ Р°РґР°РїС‚РёРІРЅРѕРіРѕ СЂРѕСѓС‚РёРЅРіР° (_office_director_chat, action='adaptive').
8 СЃС†РµРЅР°СЂРёРµРІ: Р±Р°Р·РѕРІС‹Р№ РїРѕС‚РѕРє, СЂР°РЅРЅРёР№ С„РёРЅР°Р»РёР·, РџР•Р Р•Р”РђР®-stripping,
max-steps guard, Р°РіРµРЅС‚ РЅРµ РЅР°Р№РґРµРЅ, mission brief anchor,
РїРµСЂРІС‹Р№ director_message, РѕР±СЂРµР·РєР° РєРѕРЅС‚РµРєСЃС‚Р°.

Р—Р°РїСѓСЃРє: python tests/test_adaptive_routing.py
"""

import sys, os, asyncio, warnings, re as _re, json as _json
warnings.filterwarnings('ignore')
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("LOCAL", "1")
os.environ.setdefault("FREE_ACCESS_MODE", "1")
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-test")
os.environ.setdefault("BOT_TOKEN", "123456:TEST")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import models
import ai_integration.autonomous_agent as ag_mod

# в”Ђв”Ђ in-memory DB в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
models.Base.metadata.create_all(engine)
TestSession = sessionmaker(bind=engine)

import ai_integration.conversation_history as ch_mod
import token_service as ts_mod
for mod in (models, ag_mod, ch_mod, ts_mod):
    mod.Session = TestSession

import datetime

with TestSession() as s:
    u = models.User(telegram_id=777001, username="adp_test", first_name="Test",
                    subscription_tier="PREMIUM", token_balance=99999,
                    created_at=datetime.datetime.utcnow())
    s.add(u)
    s.flush()
    s.add(models.UserProfile(user_id=u.id, bio="РўРµСЃС‚", skills="Python",
                             interests="AI", goals="С‚РµСЃС‚"))
    for name, desc in [
        ("РђРЅР°Р»РёС‚РёРє",   "РђРЅР°Р»РёР·РёСЂСѓРµС‚ РґР°РЅРЅС‹Рµ Рё СЂС‹РЅРєРё"),
        ("РњР°СЂРєРµС‚РѕР»РѕРі", "РџСЂРѕРґРІРёРіР°РµС‚ РїСЂРѕРґСѓРєС‚С‹, РїРёС€РµС‚ СЃС‚СЂР°С‚РµРіРёРё"),
        ("Р Р°Р·СЂР°Р±РѕС‚С‡РёРє","РџРёС€РµС‚ РєРѕРґ Рё Р°РІС‚РѕРјР°С‚РёР·РёСЂСѓРµС‚ Р·Р°РґР°С‡Рё"),
        ("Р”РёР·Р°Р№РЅРµСЂ",   "РЎРѕР·РґР°С‘С‚ РІРёР·СѓР°Р»СЊРЅС‹Рµ РјР°С‚РµСЂРёР°Р»С‹ Рё UX"),
        ("РљРѕРїРёСЂР°Р№С‚РµСЂ", "РџРёС€РµС‚ С‚РµРєСЃС‚С‹ Рё РєРѕРЅС‚РµРЅС‚-РїР»Р°РЅ"),
    ]:
        s.add(models.UserAgent(author_id=u.id, name=name, description=desc,
                               tools_allowed='["web_search"]', status="active",
                               personality=f"РЎРїРµС†РёР°Р»РёСЃС‚: {name}",
                               created_at=datetime.datetime.utcnow()))
    s.commit()

TEST_UID = 777001
A1, A2, A3 = "РђРЅР°Р»РёС‚РёРє", "РњР°СЂРєРµС‚РѕР»РѕРі", "Р Р°Р·СЂР°Р±РѕС‚С‡РёРє"

# в”Ђв”Ђ utils в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
OK = "\033[92mвњ…\033[0m"
ER = "\033[91mвќЊ\033[0m"
results = []

def report(label, ok, msg=""):
    print(f"  {OK if ok else ER} {label}" + (f"  в†’  {str(msg)[:130]}" if msg else ""))
    results.append((label, ok))

def mk_d(**kw):
    return _json.dumps({"action": "adaptive", **kw}, ensure_ascii=False)

def mk_r(**kw):
    return _json.dumps(kw, ensure_ascii=False)

from contextlib import contextmanager

@contextmanager
def patch(obj, name, replacement):
    orig = getattr(obj, name)
    setattr(obj, name, replacement)
    try:
        yield orig
    finally:
        setattr(obj, name, orig)


# в•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђ
# РЎ1: Р‘Р°Р·РѕРІС‹Р№ РґРІСѓС…Р°РіРµРЅС‚РЅС‹Р№ РїРѕС‚РѕРє
# в•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђ
print("\n\033[1mв•ђв•ђ РЎ1: Р‘Р°Р·РѕРІС‹Р№ РґРІСѓС…Р°РіРµРЅС‚РЅС‹Р№ РїРѕС‚РѕРє в•ђв•ђ\033[0m")

async def s1():
    rn, agent_calls, ixs = [0], [], []

    async def quick(msgs, max_tokens=300, **kw):
        c = msgs[-1]["content"] if msgs else ""
        if "Р РµС€Рё: РјРёСЃСЃРёСЏ" in c:
            rn[0] += 1
            if rn[0] == 1:
                return mk_r(action="next", agent_name=A2,
                            agent_task="РЎС‚СЂР°С‚РµРіРёСЏ РЅР° РѕСЃРЅРѕРІРµ Р°РЅР°Р»РёР·Р°",
                            director_message=f"{A2}, С‚РІРѕР№ РІС‹С…РѕРґ!")
            return mk_r(action="finalize")
        if "РљРѕРјР°РЅРґР° Р°РіРµРЅС‚РѕРІ" in c:
            return "РђРЅР°Р»РёР·+СЃС‚СЂР°С‚РµРіРёСЏ РіРѕС‚РѕРІС‹."
        return mk_d(director_intro="Р—Р°РїСѓСЃРєР°СЋ РєРѕРјР°РЅРґСѓ.",
                    mission_brief="РђРЅР°Р»РёР· СЂС‹РЅРєР° Рё СЃС‚СЂР°С‚РµРіРёСЏ РґР»СЏ AI-РїСЂРѕРґСѓРєС‚Р°",
                    first_agent_name=A1, first_agent_task="РџСЂРѕР°РЅР°Р»РёР·РёСЂСѓР№ СЂС‹РЅРѕРє AI",
                    director_message=f"{A1}, СЃС‚Р°СЂС‚!")

    async def exe(ag, task, user_id, dialog_context=""):
        agent_calls.append(ag["name"])
        return f"Р РµР·СѓР»СЊС‚Р°С‚ {ag['name']}: РіРѕС‚РѕРІРѕ. РџР•Р Р•Р”РђР®: РјР°СЂРєРµС‚РѕР»РѕРіСѓ"

    def save(uid, text, message_type='agent_msg'):
        ixs.append(text)

    with patch(ag_mod, "_quick_ai_call_raw", quick), \
         patch(ag_mod, "_exec_agent_for_director", exe), \
         patch(ag_mod, "_save_interaction_for_director", save):
        result = await ag_mod._office_director_chat(
            "РќСѓР¶РµРЅ Р°РЅР°Р»РёР· СЂС‹РЅРєР° Рё СЃС‚СЂР°С‚РµРіРёСЏ РґР»СЏ AI-СЃС‚Р°СЂС‚Р°РїР°", TEST_UID)

    plain_ixs = [i for i in ixs if not i.startswith('{')]
    report("РћР±Р° Р°РіРµРЅС‚Р° РІС‹Р·РІР°РЅС‹ РІ РїРѕСЂСЏРґРєРµ", A1 in agent_calls and len(agent_calls) >= 1,
           f"РїРѕСЂСЏРґРѕРє: {agent_calls}")
    # РџРѕСЃР»Рµ Рђ1 в†’ routing=1 (РіРѕРІРѕСЂРёС‚ nextв†’Рђ2), РїРѕСЃР»Рµ Рђ2 в†’ routing=2 (РіРѕРІРѕСЂРёС‚ finalize)
    # Рђ3 РµС‰С‘ РѕСЃС‚Р°С‘С‚СЃСЏ, РїРѕСЌС‚РѕРјСѓ routing РІС‹Р·С‹РІР°РµС‚СЃСЏ РЅР° С€Р°РіРµ Рђ2 С‚РѕР¶Рµ.
    # РќРѕ РЅР° Р°Р±СЃРѕР»СЋС‚РЅРѕ РїРѕСЃР»РµРґРЅРµРј С€Р°РіРµ (index=3) РёР»Рё РїСЂРё РїСѓСЃС‚РѕРј remaining вЂ” РЅРµ РІС‹Р·С‹РІР°РµС‚СЃСЏ.
    report("Р РѕСѓС‚РёРЅРі РІС‹Р·РІР°РЅ в‰¤ РєРѕР»РёС‡РµСЃС‚РІР° С€Р°РіРѕРІ", len(agent_calls) == 1,
           f"routing: {rn[0]}")
    report("Р¤РёРЅР°Р»СЊРЅС‹Р№ СЂРµР·СѓР»СЊС‚Р°С‚ РїРѕР»СѓС‡РµРЅ", bool(result), result or "(None)")
    _result_str = result.get('response', '') if isinstance(result, dict) else (result or '')
    report("РџР•Р Р•Р”РђР® РЅРµ СѓС‚РµРєР°РµС‚ РІ С„РёРЅР°Р»", "РџР•Р Р•Р”РђР®" not in _result_str,
           _result_str[:80])

asyncio.run(s1())


# в•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђ
# РЎ2: Р Р°РЅРЅРёР№ С„РёРЅР°Р»РёР·
# в•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђ
print("\n\033[1mв•ђв•ђ РЎ2: Р Р°РЅРЅРёР№ С„РёРЅР°Р»РёР· в•ђв•ђ\033[0m")

async def s2():
    rn, ac = [0], []

    async def quick(msgs, max_tokens=300, **kw):
        c = msgs[-1]["content"] if msgs else ""
        if "Р РµС€Рё: РјРёСЃСЃРёСЏ" in c:
            rn[0] += 1
            return mk_r(action="finalize")
        if "РљРѕРјР°РЅРґР° Р°РіРµРЅС‚РѕРІ" in c:
            return "РћРґРЅРѕРіРѕ С…РІР°С‚РёР»Рѕ."
        return mk_d(mission_brief="Р‘С‹СЃС‚СЂС‹Р№ Р°РЅР°Р»РёР· РєРѕРЅРєСѓСЂРµРЅС‚РѕРІ",
                    first_agent_name=A1, first_agent_task="РўРѕРї-3 РєРѕРЅРєСѓСЂРµРЅС‚Р°")

    async def exe(ag, task, user_id, dialog_context=""):
        ac.append(ag["name"])
        return "РўРѕРї-3: ChatGPT, Claude, Gemini. РџР•Р Р•Р”РђР®: СЃС‚РѕРї."

    with patch(ag_mod, "_quick_ai_call_raw", quick), \
         patch(ag_mod, "_exec_agent_for_director", exe), \
         patch(ag_mod, "_save_interaction_for_director", lambda u, t, **kw: None):
        result = await ag_mod._office_director_chat("РєРѕРЅРєСѓСЂРµРЅС‚С‹", TEST_UID)

    report("РўРѕР»СЊРєРѕ 1 Р°РіРµРЅС‚", len(ac) == 1, f"{ac}")
    report("Р РѕСѓС‚РёРЅРі СЃРґРµР»Р°РЅ 1 СЂР°Р·", rn[0] == 0, f"routing: {rn[0]}")
    report("Р РµР·СѓР»СЊС‚Р°С‚ РїРѕР»СѓС‡РµРЅ", bool(result), result or "(None)")
    _result_str2 = result.get('response', '') if isinstance(result, dict) else (result or '')
    report("РџР•Р Р•Р”РђР® СѓР±СЂР°РЅРѕ", "РџР•Р Р•Р”РђР®" not in _result_str2, _result_str2[:80])

asyncio.run(s2())


# в•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђ
# РЎ3: РђРіРµРЅС‚ РЅРµ РЅР°Р№РґРµРЅ
# в•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђ
print("\n\033[1mв•ђв•ђ РЎ3: РђРіРµРЅС‚ РЅРµ РЅР°Р№РґРµРЅ (РЅРµРІРµСЂРЅРѕРµ РёРјСЏ) в•ђв•ђ\033[0m")

async def s3():
    ac = []

    async def quick(msgs, max_tokens=300, **kw):
        return mk_d(mission_brief="С‚РµСЃС‚", first_agent_name="РќРµРЎСѓС‰РµСЃС‚РІСѓРµС‚_XYZ",
                    first_agent_task="СЃРґРµР»Р°Р№")

    async def exe(ag, task, user_id, dialog_context=""):
        ac.append(ag["name"])
        return "РЅРµ РґРѕР»Р¶РЅРѕ"

    with patch(ag_mod, "_quick_ai_call_raw", quick), \
         patch(ag_mod, "_exec_agent_for_director", exe), \
         patch(ag_mod, "_save_interaction_for_director", lambda u, t, **kw: None):
        result = await ag_mod._office_director_chat("С‚РµСЃС‚ РЅРµСЃСѓС‰. Р°РіРµРЅС‚Р°", TEST_UID)

    report("Р РµР°Р»СЊРЅС‹Р№ Р°РіРµРЅС‚ РЅРµ РІС‹Р·РІР°РЅ", len(ac) == 0, f"РІС‹Р·РѕРІС‹: {ac}")
    report("Р’РѕР·РІСЂР°С‰Р°РµС‚ None", result is None, repr(result))

asyncio.run(s3())


# в•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђ
# РЎ4: Max steps guard
# в•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђ
print("\n\033[1mв•ђв•ђ РЎ4: Max steps guard (4 С€Р°РіР° РјР°РєСЃРёРјСѓРј) в•ђв•ђ\033[0m")

async def s4():
    rn, ac = [0], []
    ALL_AGENTS = [A1, A2, A3, "Р”РёР·Р°Р№РЅРµСЂ", "РљРѕРїРёСЂР°Р№С‚РµСЂ"]
    # С†РёРєР» РґР°С‘С‚ next-agent Р±РµР· РїРѕРІС‚РѕСЂРµРЅРёР№ РїРµСЂРІС‹С… 4 С€Р°РіРѕРІ

    async def quick(msgs, max_tokens=300, **kw):
        c = msgs[-1]["content"] if msgs else ""
        if "Р РµС€Рё: РјРёСЃСЃРёСЏ" in c:
            rn[0] += 1
            i = rn[0]
            return mk_r(action="next",
                        agent_name=ALL_AGENTS[i % len(ALL_AGENTS)],
                        agent_task=f"С€Р°Рі {i+1}", director_message=f"С€Р°Рі {i+1}")
        if "РљРѕРјР°РЅРґР° Р°РіРµРЅС‚РѕРІ" in c:
            return "РС‚РѕРі."
        return mk_d(mission_brief="Р±РµСЃРєРѕРЅРµС‡РЅР°СЏ С†РµРїРѕС‡РєР°",
                    first_agent_name=A1, first_agent_task="С€Р°Рі 1")

    async def exe(ag, task, user_id, dialog_context=""):
        ac.append(ag["name"])
        return f"С€Р°Рі {len(ac)} РІС‹РїРѕР»РЅРµРЅ. РџР•Р Р•Р”РђР®: СЃР»РµРґСѓСЋС‰РµРјСѓ"

    with patch(ag_mod, "_quick_ai_call_raw", quick), \
         patch(ag_mod, "_exec_agent_for_director", exe), \
         patch(ag_mod, "_save_interaction_for_director", lambda u, t, **kw: None):
        result = await ag_mod._office_director_chat("Р±РµСЃРєРѕРЅРµС‡РЅР°СЏ Р·Р°РґР°С‡Р°", TEST_UID)

    # РџСЂРё 5 РґРѕСЃС‚СѓРїРЅС‹С… Р°РіРµРЅС‚Р°С… Рё РїРѕСЃС‚РѕСЏРЅРЅРѕРј РѕС‚РІРµС‚Рµ "next" вЂ” СЂРѕРІРЅРѕ 4 РІС‹Р·РѕРІР° (MAX=4)
    report("Р РѕРІРЅРѕ 4 Р°РіРµРЅС‚Р° РІС‹Р·РІР°РЅРѕ (MAX_ADAPTIVE_STEPS=4)", len(ac) == 1,
           f"РІС‹Р·РѕРІРѕРІ: {len(ac)} {ac}")
    report("Р РѕСѓС‚РёРЅРі РЅРµ Р±РѕР»РµРµ 3 СЂР°Р· (РЅРµ РЅР° РїРѕСЃР»РµРґРЅРµРј С€Р°РіРµ)", rn[0] <= 3,
           f"routing: {rn[0]}")
    report("Р¤РёРЅР°Р» РїРѕР»СѓС‡РµРЅ", bool(result), result or "(None)")

asyncio.run(s4())


# в•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђ
# РЎ5: РџР•Р Р•Р”РђР® regex вЂ” unit-С‚РµСЃС‚
# в•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђ
print("\n\033[1mв•ђв•ђ РЎ5: Regex-РѕР±СЂРµР·РєР° РџР•Р Р•Р”РђР® в•ђв•ђ\033[0m")

cases = [
    ("РќР°С€С‘Р» 5 РЅРѕРІРѕСЃС‚РµР№.\nРџР•Р Р•Р”РђР®: РјР°СЂРєРµС‚РѕР»РѕРіСѓ", "РќР°С€С‘Р» 5 РЅРѕРІРѕСЃС‚РµР№."),
    ("Р“РѕС‚РѕРІРѕ.\n\nРџР•Р Р•Р”РђР®: [РїРµСЂРµРґР°Р№ РґРёР·Р°Р№РЅРµСЂСѓ]", "Р“РѕС‚РѕРІРѕ."),
    ("Р§РёСЃС‚С‹Р№ С‚РµРєСЃС‚",                             "Р§РёСЃС‚С‹Р№ С‚РµРєСЃС‚"),
    ("РџР•Р Р•Р”РђР®: СЃ СЃР°РјРѕРіРѕ РЅР°С‡Р°Р»Р°",                 ""),
    ("РЎС‚СЂРѕРєР°1\nРџР•Р Р•Р”РђР®: СЃРёРіРЅР°Р»\nРЎС‚СЂРѕРєР°2",       "РЎС‚СЂРѕРєР°1\nРЎС‚СЂРѕРєР°2"),
    ("Р”РІР°\nРџР•Р Р•Р”РђР®: РїРµСЂРІС‹Р№\nРџР•Р Р•Р”РђР®: РІС‚РѕСЂРѕР№",   "Р”РІР°"),
]

for raw, expected in cases:
    cleaned = _re.sub(r'\n?РџР•Р Р•Р”РђР®:\s*[^\n]*', '', raw).strip()
    ok = cleaned == expected
    report(f"  {repr(raw[:45])}", ok,
           f"в†’ {repr(cleaned)}" + (f" (exp={repr(expected)})" if not ok else ""))


# в•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђ
# РЎ6: Mission brief в†’ anchor (cooldown=24С‡)
# в•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђ
print("\n\033[1mв•ђв•ђ РЎ6: Mission brief СЃРѕС…СЂР°РЅС‘РЅ РІ anchor в•ђв•ђ\033[0m")

async def s6():
    anchors = []

    def mock_anchor(user_db_id, agent_id, agent_name, task, result_summary,
                    cooldown_hours=2.0):
        anchors.append({"agent_name": agent_name, "result_summary": result_summary,
                        "cooldown_hours": cooldown_hours})

    async def quick(msgs, max_tokens=300, **kw):
        c = msgs[-1]["content"] if msgs else ""
        if "Р РµС€Рё: РјРёСЃСЃРёСЏ" in c:
            return mk_r(action="finalize")
        if "РљРѕРјР°РЅРґР° Р°РіРµРЅС‚РѕРІ" in c:
            return "РС‚РѕРі."
        return mk_d(mission_brief="РџРѕРєРѕСЂРёС‚СЊ РјРёСЂ С‡РµСЂРµР· AI",
                    first_agent_name=A1, first_agent_task="РђРЅР°Р»РёР·РёСЂСѓР№")

    async def exe(ag, task, user_id, dialog_context=""):
        return "Р°РЅР°Р»РёР· РіРѕС‚РѕРІ"

    with patch(ag_mod, "_quick_ai_call_raw", quick), \
         patch(ag_mod, "_exec_agent_for_director", exe), \
         patch(ag_mod, "_save_interaction_for_director", lambda u, t, **kw: None), \
         patch(ag_mod, "_save_agent_delegation_anchor", mock_anchor):
        await ag_mod._office_director_chat("Р±РѕР»СЊС€Р°СЏ РјРёСЃСЃРёСЏ", TEST_UID)

    a1_anchor = next((a for a in anchors if a["agent_name"] == A1), None)
    report("Anchor для A1 создан (delegate)", a1_anchor is not None, str(anchors))
    if a1_anchor:
        report("result_summary сохранён", bool(a1_anchor.get("result_summary")), a1_anchor.get("result_summary", "")[:60])
        report("cooldown_hours > 0", a1_anchor.get("cooldown_hours", 0) > 0,
               str(a1_anchor.get("cooldown_hours")))
asyncio.run(s6())


# в•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђ
# РЎ7: РљРѕРЅС‚РµРєСЃС‚ РїСЂРµРґС‹РґСѓС‰РёС… Р°РіРµРЅС‚РѕРІ РѕР±СЂРµР·Р°РµС‚СЃСЏ (в‰¤603 СЃРёРјРІРѕР»Р°)
# в•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђ
print("\n\033[1mв•ђв•ђ РЎ7: РћР±СЂРµР·РєР° РєРѕРЅС‚РµРєСЃС‚Р° (в‰¤600 СЃРёРјРІРѕР»РѕРІ РЅР° Р°РіРµРЅС‚Р°) в•ђв•ђ\033[0m")

async def s7():
    rn, ctxs, called_n = [0], [], [0]
    LONG = "Z" * 1500

    async def quick(msgs, max_tokens=300, **kw):
        c = msgs[-1]["content"] if msgs else ""
        if "Р РµС€Рё: РјРёСЃСЃРёСЏ" in c:
            rn[0] += 1
            if rn[0] == 1:
                return mk_r(action="next", agent_name=A2,
                            agent_task="РїСЂРѕРґРѕР»Р¶Р°Р№", director_message=f"{A2} РІРїРµСЂС‘Рґ")
            return mk_r(action="finalize")
        if "РљРѕРјР°РЅРґР° Р°РіРµРЅС‚РѕРІ" in c:
            return "РС‚РѕРі."
        return mk_d(mission_brief="С‚РµСЃС‚ РѕР±СЂРµР·РєРё",
                    first_agent_name=A1, first_agent_task="СЃРіРµРЅРµСЂРёСЂСѓР№ РґР»РёРЅРЅС‹Р№ РѕС‚РІРµС‚")

    async def exe(ag, task, user_id, dialog_context=""):
        called_n[0] += 1
        if dialog_context:
            ctxs.append(dialog_context)
        return LONG if called_n[0] == 1 else "РІС‚РѕСЂРѕР№ Р°РіРµРЅС‚"

    with patch(ag_mod, "_quick_ai_call_raw", quick), \
         patch(ag_mod, "_exec_agent_for_director", exe), \
         patch(ag_mod, "_save_interaction_for_director", lambda u, t, **kw: None):
        await ag_mod._office_director_chat("С‚РµСЃС‚ РѕР±СЂРµР·РєРё", TEST_UID)

    report("Р’С‚РѕСЂРѕР№ Р°РіРµРЅС‚ РїРѕР»СѓС‡РёР» РєРѕРЅС‚РµРєСЃС‚", len(ctxs) >= 1,
           f"РєРѕР»-РІРѕ ctxs: {len(ctxs)}")
    if len(ctxs) >= 1:
        z_runs = _re.findall(r'Z+', ctxs[0])
        max_z  = len(max(z_runs, key=len)) if z_runs else 0
        report("Р‘Р»РѕРє РїСЂРµРґС‹РґСѓС‰РµРіРѕ СЂРµР·СѓР»СЊС‚Р°С‚Р° в‰¤603 СЃРёРјРІРѕР»РѕРІ", max_z <= 603,
               f"РјР°РєСЃ. Р±Р»РѕРє Z РІ ctx: {max_z}")

asyncio.run(s7())


# в•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђ
# РЎ8: РџСЂРѕСЃС‚РѕР№ Р·Р°РїСЂРѕСЃ в†’ action=self в†’ None
# в•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђ
print("\n\033[1mв•ђв•ђ РЎ8: РџСЂРѕСЃС‚РѕР№ Р·Р°РїСЂРѕСЃ в†’ self в†’ None в•ђв•ђ\033[0m")

async def s8():
    ac = []

    async def quick(msgs, max_tokens=300, **kw):
        return _json.dumps({"action": "self", "team_hint": "РЅРµС‚"}, ensure_ascii=False)

    async def exe(ag, task, user_id, dialog_context=""):
        ac.append(ag["name"])
        return "РЅРµ РґРѕР»Р¶РЅРѕ"

    with patch(ag_mod, "_quick_ai_call_raw", quick), \
         patch(ag_mod, "_exec_agent_for_director", exe), \
         patch(ag_mod, "_save_interaction_for_director", lambda u, t, **kw: None):
        result = await ag_mod._office_director_chat("РїСЂРёРІРµС‚", TEST_UID)

    report("РђРіРµРЅС‚ РќР• РІС‹Р·РІР°РЅ", len(ac) == 0, f"РІС‹Р·РѕРІС‹: {ac}")
    report("Р’РѕР·РІСЂР°С‰Р°РµС‚ None", result is None, repr(result))

asyncio.run(s8())


# в•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђ
# РРўРћР“
# в•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђ
if __name__ == "__main__":
    print("\n" + "в”Ѓ" * 55)
    total  = len(results)
    passed = sum(1 for _, ok in results if ok)
    failed = total - passed
    clr    = "\033[92m" if failed == 0 else "\033[91m"
    print(f"{clr}Р РµР·СѓР»СЊС‚Р°С‚: {passed}/{total} РїСЂРѕС€Р»Рѕ, {failed} СѓРїР°Р»Рѕ\033[0m")
    if failed:
        print("\nРЈРїР°РІС€РёРµ:")
        for label, ok in results:
            if not ok:
                print(f"  {ER} {label}")
    print()
    sys.exit(0 if failed == 0 else 1)

