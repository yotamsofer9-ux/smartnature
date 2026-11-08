"""Live acceptance test: two isolated visitors, storage, quotas, structured AI."""
import csv
import io
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
from domain import session_identity, demo_hazard, validate_image, eligible_reserves
from repository import DemoRepository, LimitReached, StoreUnavailable
from ai_service import AIService


def main():
    repo=DemoRepository()
    token_a,a=session_identity();token_b,b=session_identity()
    assert session_identity(token_a)[1]==a
    repo.reserve_action(a)
    repo.save_exchange(a,'בדיקת קבלה אוטומטית','תשובה בביקור הראשון בלבד')
    assert len(repo.chat(a))==2 and repo.chat(b)==[]
    # New process/object resumes data without relying on Streamlit memory.
    assert len(DemoRepository().chat(session_identity(token_a)[1]))==2
    print('Chat isolation and persisted resume verified.',flush=True)
    repo.reserve_action(b)
    stream=io.BytesIO();Image.new('RGB',(32,32),'green').save(stream,format='PNG')
    image,_=validate_image(stream.getvalue())
    rid=repo.submit_report(b,21,'בדיקת קבלה: תמונת הדגמה',demo_hazard(),image)
    assert repo.reports(a)==[] and repo.reports(b)[0]['review_status']=='pending'
    assert repo.report_image(b,rid)==image
    try: repo.report_image(a,rid)
    except StoreUnavailable: pass
    else: raise AssertionError('Another visitor read the image')
    repo.simulate_review(a,rid)
    assert repo.reports(b)[0]['review_status']=='pending'
    repo.simulate_review(b,rid)
    assert repo.reports(b)[0]['review_status']=='demo_approved'
    print('Private image, queued report, cross-visitor review protection verified.',flush=True)
    # Trigger deterministic quota enforcement without 20 billable model calls.
    repo._query(f"UPDATE `{repo.dataset}.usage` SET calls=20 WHERE scope=@sid AND day=CURRENT_DATE('UTC')",
                [repo.param('sid',a)])
    try: repo.reserve_action(a)
    except LimitReached: pass
    else: raise AssertionError('Session quota bypassed')
    print('Durable quota enforcement verified.',flush=True)
    with open('assets/reserves_demo.csv') as f: rows=list(csv.DictReader(f))
    for r in rows: r['id']=int(r['id'])
    model=AIService()
    analysis,ok=model.hazard('ענף גדול חוסם חלק משביל ההליכה; יש לעבור לבדיקה',image)
    assert ok, 'Live hazard analysis failed'
    choices,ok=model.recommend('הצע מסלול לרכב פרטי',rows,'Private Car','Beginner',[])
    assert ok and choices, 'Live structured route recommendation failed'
    allowed={r['id'] for r in eligible_reserves(rows,'Private Car')}
    assert all(r['id'] in allowed for r in choices)
    print(json.dumps({'live_hazard':analysis['severity'],'validated_recommendations':len(choices),
                      'all_checks_passed':True}),flush=True)


if __name__=='__main__': main()
