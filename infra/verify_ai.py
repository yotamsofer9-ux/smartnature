"""Check the real structured model interfaces without writing visitor data."""
import csv,io,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
from ai_service import AIService
from domain import eligible_reserves

model=AIService()
image=io.BytesIO();Image.new('RGB',(32,32),'green').save(image,format='JPEG')
if '--recommend-only' not in sys.argv:
    analysis,ok=model.hazard('ענף גדול חוסם חלק משביל ההליכה; נדרשת בדיקת אדם',image.getvalue())
    assert ok,'Structured hazard call failed'
with open('assets/reserves_demo.csv') as stream: rows=list(csv.DictReader(stream))
for row in rows: row['id']=int(row['id'])
choices,ok=model.recommend('הצע מסלול לרכב פרטי',rows,'Private Car','Beginner',[])
assert ok and choices,'Structured recommendations failed'
allowed={r['id'] for r in eligible_reserves(rows,'Private Car')}
assert all(c['id'] in allowed for c in choices)
print(json.dumps({'hazard_valid':True,'recommendations_valid':True,'count':len(choices)}))
