from sqlalchemy import create_engine, text
import os
from dotenv import load_dotenv
load_dotenv()

url = os.getenv('DATABASE_URL', '').replace('postgres://', 'postgresql://')
eng = create_engine(url, connect_args={'connect_timeout': 15, 'sslmode': 'require'})
try:
    with eng.connect() as c:
        # Latest anchors (with exact timestamps for service_degraded/weather_extreme)
        rows = c.execute(text(
            'SELECT id, anchor_type, source, priority, created_at, delivered_at '
            'FROM anchors ORDER BY id DESC LIMIT 20'
        )).fetchall()
        print('=== Latest anchors ===')
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        for r in rows:
            age = int((now - r[4].replace(tzinfo=timezone.utc)).total_seconds() // 60) if r[4] else '?'
            del_str = f'del={int((now - r[5].replace(tzinfo=timezone.utc)).total_seconds()//60)}min ago' if r[5] else 'PENDING'
            exact = r[4].strftime('%H:%M:%S') if r[4] else '?'
            print(f'#{r[0]} {r[1]} src={r[2]} pri={r[3]} @{exact}(age={age}m) {del_str}')

        # service_degraded exact timestamps
        sd_rows = c.execute(text(
            "SELECT id, source, created_at, delivered_at, expires_at FROM anchors "
            "WHERE anchor_type='service_degraded' ORDER BY id DESC LIMIT 20"
        )).fetchall()
        print('\n=== service_degraded history (last 20) ===')
        for r in sd_rows:
            exact = r[2].strftime('%H:%M:%S') if r[2] else '?'
            exp = r[4].strftime('%H:%M') if r[4] else 'no-exp'
            del_str = r[3].strftime('%H:%M:%S') if r[3] else 'PENDING'
            print(f'  #{r[0]} src={r[1]} created={exact} exp={exp} delivered={del_str}')

        # Pending count by type (user_id=1)
        pend = c.execute(text(
            "SELECT anchor_type, COUNT(*) as cnt FROM anchors "
            "WHERE delivered_at IS NULL AND user_id=1 GROUP BY anchor_type ORDER BY cnt DESC"
        )).fetchall()
        print('\n=== Pending by type (user_id=1) ===')
        for r in pend:
            print(f'  {r[0]}: {r[1]} pending')

        # dialog_count today (raw, for reference only — engine uses its own calc)
        today = now.date()
        dlg_raw = c.execute(text(
            "SELECT COUNT(*) FROM anchor_delivery_log WHERE user_id=1 AND created_at::date = :d"
        ), {'d': today}).fetchone()
        print(f'\n=== Raw delivery_log today: {dlg_raw[0]} entries ===')

        # Engine dialog_count (matching engine logic)
        _SILENT_TYPES = "('email_outreach_send','email_follow_up','email_need_leads','content_campaign_publish','delegation_campaign_send','delegation_campaign_follow_up','agent_delegation')"
        dlg_rows = c.execute(text(
            "SELECT anchor_types FROM anchor_delivery_log WHERE user_id=1 AND created_at::date = :d"
        ), {'d': today}).fetchall()
        import json as _json
        d_cnt = p_cnt = ch_cnt = disc_cnt = silent_cnt = 0
        for (at,) in dlg_rows:
            try: types = _json.loads(at) if at else []
            except: types = []
            if 'channel_post' in types: ch_cnt += 1
            elif 'discord_post' in types: disc_cnt += 1
            elif 'post_opportunity' in types: p_cnt += 1
            elif types and all(t in {'email_outreach_send','email_follow_up','email_need_leads','content_campaign_publish','delegation_campaign_send','delegation_campaign_follow_up','agent_delegation'} for t in types): silent_cnt += 1
            else: d_cnt += 1
        print(f'\n=== Engine dialog_count: {d_cnt} (posts={p_cnt} channel={ch_cnt} discord={disc_cnt} silent={silent_cnt}) ===')

        # Expired-but-undelivered anchors (garbage)
        expired_cnt = c.execute(text(
            "SELECT COUNT(*) FROM anchors WHERE delivered_at IS NULL AND expires_at IS NOT NULL AND expires_at < NOW()"
        )).fetchone()
        print(f'=== Expired-but-undelivered anchors (garbage): {expired_cnt[0]} ===')

        # Last successful delivery
        last_del = c.execute(text(
            "SELECT MAX(created_at) FROM anchor_delivery_log WHERE user_id=1"
        )).fetchone()
        if last_del[0]:
            age_del = int((now - last_del[0].replace(tzinfo=timezone.utc)).total_seconds() // 60)
            print(f'=== Last delivery: {age_del}min ago ({last_del[0].strftime("%H:%M:%S")}) ===')

        # Last agent activity
        ag = c.execute(text(
            "SELECT activity_type, title, status, created_at FROM agent_activity_log "
            "WHERE user_id=1 ORDER BY created_at DESC LIMIT 5"
        )).fetchall()
        print('\n=== Recent agent activity ===')
        for r in ag:
            age_a = int((now - r[3].replace(tzinfo=timezone.utc)).total_seconds() // 60) if r[3] else '?'
            print(f'  {r[0]} | {r[1][:60]} | {r[2]} | {age_a}min ago')

except Exception as e:
    print(f'ERROR: {e}')
