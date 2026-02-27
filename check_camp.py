import os
os.environ['DATABASE_URL'] = 'postgresql://postgres:upZTbJrZvoxnoSPdUDaOwnLuOvnNSbML@nozomi.proxy.rlwy.net:52451/railway'
from models import *
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

engine = create_engine(os.environ['DATABASE_URL'])
Session = sessionmaker(bind=engine)
s = Session()

camps = s.query(EmailCampaign).order_by(EmailCampaign.id.desc()).limit(5).all()
for c in camps:
    print(f"Campaign #{c.id}: {c.name}")
    print(f"  status={c.status} | goal={c.goal}")
    print(f"  daily_limit={c.daily_limit} | max_emails={c.max_emails} | sent={c.emails_sent}")
    print()

print("--- OUTREACH ---")
outreach = s.query(EmailOutreach).order_by(EmailOutreach.id.desc()).limit(30).all()
for o in outreach:
    subj = (o.subject or "")[:80]
    body = (o.body or "")[:150]
    print(f"id={o.id} camp={o.campaign_id} | to={o.recipient_email}")
    print(f"  name={o.recipient_name} | status={o.status} | sent={o.sent_at}")
    print(f"  subj: {subj}")
    print(f"  body: {body}")
    print()

s.close()
