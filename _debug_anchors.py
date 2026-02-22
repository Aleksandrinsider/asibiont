import psycopg2
from datetime import datetime, timezone, timedelta

conn = psycopg2.connect('postgresql://postgres:upZTbJrZvoxnoSPdUDaOwnLuOvnNSbML@nozomi.proxy.rlwy.net:52451/railway')
cur = conn.cursor()

now_utc = datetime.now(timezone.utc)

# 1. ALL anchor deliveries for user 1
print("=== USER 1 ANCHOR DELIVERIES (all time) ===")
cur.execute("""SELECT id, anchor_types, created_at 
               FROM anchor_delivery_log WHERE user_id = 1 
               ORDER BY created_at DESC""")
for r in cur.fetchall():
    print(f"  #{r[0]} types={r[1]} at={r[2]}")

# 2. ALL anchors for user 1 today
print("\n=== USER 1 ANCHORS TODAY ===")
cur.execute("""SELECT id, anchor_type, triggered_at, delivered_at, expires_at, priority
               FROM anchors WHERE user_id = 1 
               AND triggered_at >= '2026-02-21 00:00:00'
               ORDER BY triggered_at DESC""")
for r in cur.fetchall():
    dlv = f'delivered={r[3]}' if r[3] else 'NOT delivered'
    expired = 'EXPIRED' if r[4] and r[4].replace(tzinfo=timezone.utc) < now_utc else 'active'
    print(f"  #{r[0]} type={r[1]} prio={r[5]} triggered={r[2]} {dlv} {expired}")

# 3. Ignore rate for user 1
print("\n=== USER 1 IGNORE RATE ===")
cur.execute("""SELECT COUNT(*) FROM anchor_delivery_log WHERE user_id = 1""")
total_sent = cur.fetchone()[0]
cur.execute("""SELECT COUNT(*) FROM anchor_delivery_log 
               WHERE user_id = 1 
               AND created_at >= NOW() - INTERVAL '7 days'""")
recent_sent = cur.fetchone()[0]
print(f"  Total deliveries: {total_sent}")
print(f"  Recent 7-day deliveries: {recent_sent}")

# Check interactions (responses) after proactive messages
cur.execute("""SELECT COUNT(*) FROM anchor_delivery_log WHERE user_id = 1""")
total_deliveries = cur.fetchone()[0]
print(f"  Total deliveries: {total_deliveries}")

# 4. Task reminders - check task_reminder anchors
print("\n=== TASK REMINDER ANCHORS (all users, all time) ===")
cur.execute("""SELECT id, user_id, anchor_type, triggered_at, delivered_at 
               FROM anchors WHERE anchor_type = 'task_reminder'
               ORDER BY triggered_at DESC LIMIT 10""")
rows = cur.fetchall()
if rows:
    for r in rows:
        dlv = f'delivered={r[4]}' if r[4] else 'NOT delivered'
        print(f"  #{r[0]} user={r[1]} triggered={r[3]} {dlv}")
else:
    print("  NO task_reminder anchors found!")

# 5. Check task_overdue anchors delivery
print("\n=== TASK_OVERDUE ANCHORS (all) ===")
cur.execute("""SELECT id, user_id, anchor_type, triggered_at, delivered_at 
               FROM anchors WHERE anchor_type = 'task_overdue'
               ORDER BY triggered_at DESC LIMIT 10""")
for r in cur.fetchall():
    dlv = f'delivered={r[4]}' if r[4] else 'NOT delivered'
    print(f"  #{r[0]} user={r[1]} triggered={r[3]} {dlv}")

# 6. Check delivery log for task-related
print("\n=== TASK-RELATED DELIVERIES ===")
cur.execute("""SELECT id, user_id, anchor_types, created_at
               FROM anchor_delivery_log 
               WHERE anchor_types LIKE '%task%'
               ORDER BY created_at DESC LIMIT 10""")
for r in cur.fetchall():
    print(f"  #{r[0]} user={r[1]} types={r[2]} at={r[3]}")

# 7. Active pending tasks with upcoming reminders
print("\n=== PENDING TASKS WITH REMINDERS ===")
cur.execute("""SELECT t.id, t.user_id, u.username, t.title, t.reminder_time, t.reminder_sent, t.status
               FROM tasks t JOIN users u ON t.user_id = u.id
               WHERE t.status IN ('pending', 'in_progress', 'active')
               ORDER BY t.reminder_time""")
for r in cur.fetchall():
    rt = r[4]
    if rt:
        diff = now_utc - rt.replace(tzinfo=timezone.utc)
        if diff.total_seconds() > 0:
            time_str = f"{diff.total_seconds()/3600:.1f}h ago"
        else:
            time_str = f"in {abs(diff.total_seconds())/3600:.1f}h"
    else:
        time_str = "no time"
    print(f"  #{r[0]} user={r[1]}(@{r[2]}) '{r[3][:40]}' remind={r[4]} sent={r[5]} status={r[6]} ({time_str})")

# 8. Check the _deliver flow - daily limits
print("\n=== DAILY DELIVERY COUNTS (today) ===")
cur.execute("""SELECT user_id, COUNT(*) 
               FROM anchor_delivery_log 
               WHERE created_at >= '2026-02-21 00:00:00'
               GROUP BY user_id ORDER BY user_id""")
for r in cur.fetchall():
    print(f"  user={r[0]}: {r[1]} deliveries today")

conn.close()
print("\n=== DONE ===")
