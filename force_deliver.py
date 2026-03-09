"""
Forced direct delivery of stuck HIGH anchors #1313 and #1315
to Telegram via bot token (bypasses AI SKIP).
Also fixes expired-but-undelivered anchors that block new ones.
"""
import os, json, asyncio
import aiohttp
from sqlalchemy import create_engine, text

DB = os.environ.get('DATABASE_URL',
    'postgresql://postgres:upZTbJrZvoxnoSPdUDaOwnLuOvnNSbML@nozomi.proxy.rlwy.net:52451/railway')
BOT_TOKEN = os.environ.get('TELEGRAM_TOKEN', '')
chat_id = 146333757

eng = create_engine(DB)


async def send_tg_message(token: str, chat: int, text_msg: str):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    async with aiohttp.ClientSession() as sess:
        async with sess.post(url, json={
            "chat_id": chat,
            "text": text_msg,
            "parse_mode": "HTML"
        }, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            data = await resp.json()
            return data


async def main():
    with eng.connect() as c:
        now = c.execute(text("SELECT NOW() AT TIME ZONE 'UTC'")).scalar()
        print(f"DB UTC: {now}")

        # Fetch anchor data for #1313 and #1315
        anchors = c.execute(text("""
            SELECT id, anchor_type, priority, source, topic, data, expires_at, delivered_at
            FROM anchors WHERE id IN (1313, 1315)
        """)).fetchall()

        print(f"\nAnchors to deliver: {len(anchors)}")

        if not BOT_TOKEN:
            # Try .env
            token = None
            if os.path.exists('.env'):
                with open('.env') as f:
                    for line in f:
                        if line.startswith('TELEGRAM_TOKEN='):
                            token = line.split('=', 1)[1].strip().strip('"').strip("'")
                            print(f"Found TELEGRAM_TOKEN in .env: {token[:10]}...")
                            break
            if not token:
                print("ERROR: could not find TELEGRAM_TOKEN")
                return
        else:
            token = BOT_TOKEN

        messages_to_send = []
        for a in anchors:
            if a.delivered_at:
                print(f"  #{a.id} already delivered, skip")
                continue
            exp_min = int((a.expires_at - now).total_seconds() / 60) if a.expires_at else None
            print(f"  #{a.id} {a.anchor_type} [{a.priority}] expires_in={exp_min}min")

            try:
                data = json.loads(a.data) if a.data else {}
            except Exception:
                data = {}

            agent_name = data.get('agent_name', 'Агент')
            result = data.get('result', '')
            task = data.get('task', '')

            msg = f"<b>📊 Отчёт агента {agent_name}</b>\n\n"
            if result:
                msg += result[:900]
            elif task:
                msg += f"Задача: {task[:300]}"
            else:
                msg += f"{a.topic or a.anchor_type}"

            messages_to_send.append((a.id, msg))

        if not messages_to_send:
            print("Nothing to deliver.")
        else:
            for anchor_id, msg in messages_to_send:
                print(f"\nSending #{anchor_id} ({len(msg)} chars)...")
                try:
                    resp = await send_tg_message(token, chat_id, msg)
                    if resp.get('ok'):
                        # Mark as delivered
                        c.execute(text(
                            "UPDATE anchors SET delivered_at=NOW() WHERE id=:aid"
                        ), {"aid": anchor_id})
                        c.execute(text("COMMIT"))
                        print(f"  ✅ Sent and marked delivered: #{anchor_id}")
                    else:
                        print(f"  ❌ TG error: {resp}")
                except Exception as e:
                    print(f"  ❌ Exception: {e}")

        # Fix expired-but-undelivered anchors blocking new ones
        print("\n--- Fixing expired undelivered anchors ---")
        expired = c.execute(text("""
            SELECT id, anchor_type, source, expires_at
            FROM anchors
            WHERE user_id=1 AND delivered_at IS NULL
              AND expires_at IS NOT NULL AND expires_at <= NOW()
        """)).fetchall()
        print(f"Expired undelivered: {len(expired)}")
        for a in expired:
            exp_min = int((now - a.expires_at).total_seconds() / 60)
            print(f"  #{a.id} {a.anchor_type} src={a.source} expired {exp_min}min ago → marking delivered")
        if expired:
            ids = [a.id for a in expired]
            c.execute(text(
                f"UPDATE anchors SET delivered_at=NOW() WHERE id=ANY(ARRAY{ids}::integer[])"
            ))
            c.execute(text("COMMIT"))
            print(f"  ✅ Marked {len(expired)} expired anchors as delivered")


asyncio.run(main())
