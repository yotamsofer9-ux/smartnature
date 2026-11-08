import datetime as dt
import os
import unittest
from pathlib import Path
from unittest.mock import patch
from streamlit.testing.v1 import AppTest
from domain import session_identity


class FakeRepository:
    chats={}
    report_rows={}
    def reserve_action(self,sid): pass
    def chat(self,sid): return list(self.chats.get(sid,[]))
    def reports(self,sid): return list(self.report_rows.get(sid,[]))
    def save_exchange(self,sid,text,answer):
        self.chats.setdefault(sid,[]).extend([{'role':'user','content':text},{'role':'assistant','content':answer}])
    def submit_report(self,sid,rid,text,analysis,image=None):
        self.report_rows.setdefault(sid,[]).append(dict(report_id='r1',reserve_id=rid,
            created_at=dt.datetime.now(dt.timezone.utc),user_text=text,analysis=analysis,
            review_status='pending',image_object=None))
    def simulate_review(self,sid,rid):
        for r in self.report_rows[sid]:
            if r['report_id']==rid: r['review_status']='demo_approved'


class UITests(unittest.TestCase):
    def setUp(self):
        FakeRepository.chats={};FakeRepository.report_rows={}
        self.patcher=patch('repository.DemoRepository',FakeRepository)
        self.patcher.start()
        self.env=patch.dict(os.environ,{'SMARTNATURE_OFFLINE':'0'})
        self.env.start()
        import streamlit as st
        st.cache_resource.clear()
        self.app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=20)
        self.token,self.sid=session_identity()
        self.app.query_params['visit']=self.token
        self.app.run()
    def tearDown(self):
        self.patcher.stop();self.env.stop()
    def test_intro_navigation_and_single_chat_answer(self):
        self.assertFalse(self.app.exception)
        next(b for b in self.app.button if b.label=='🌿 שיחה עם הסוכן').click().run()
        self.assertEqual(self.app.sidebar.radio[0].value,'סוכן הטיולים')
        self.app.chat_input[0].set_value('הצע מסלול').run()
        self.assertFalse(self.app.exception)
        self.assertEqual(len(self.app.chat_message),2)
        self.app.run()
        self.assertEqual(len(self.app.chat_message),2)
        other=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=20)
        other.query_params['visit']=session_identity()[0]
        other.run()
        other.sidebar.radio[0].set_value('סוכן הטיולים').run()
        self.assertEqual(len(other.chat_message),0)
    def test_all_pages_remain_usable_offline(self):
        with patch.dict(os.environ,{'SMARTNATURE_OFFLINE':'1'}):
            for page in ['מבט ראשון','מפת האתרים','דיווח חכם','סוכן הטיולים']:
                self.app.sidebar.radio[0].set_value(page).run()
                self.assertFalse(self.app.exception)
            self.assertTrue(self.app.chat_input[0].disabled)

    def test_ai_initialization_failure_keeps_report_unknown(self):
        with patch('ai_service.AIService',side_effect=RuntimeError('provider unavailable')):
            import streamlit as st
            st.cache_resource.clear()
            self.app.sidebar.radio[1].set_value('AI מחובר').run()
            self.app.sidebar.radio[0].set_value('דיווח חכם').run()
            self.app.text_area[0].set_value('תיאור לדוגמה')
            next(b for b in self.app.button if b.label=='ניתוח ושליחה לתור ההדגמה').click().run()
            self.assertFalse(self.app.exception)
            self.assertEqual(FakeRepository.report_rows[self.sid][0]['analysis']['severity'],'Unknown')

    def test_report_queues_and_demo_review_is_session_scoped(self):
        self.app.sidebar.radio[0].set_value('דיווח חכם').run()
        self.app.text_area[0].set_value('ענף חוסם את השביל')
        next(b for b in self.app.button if b.label=='ניתוח ושליחה לתור ההדגמה').click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(FakeRepository.report_rows[self.sid][0]['review_status'],'pending')
        next(b for b in self.app.button if b.label=='הדמיית אישור בדמו שלי בלבד').click().run()
        self.assertEqual(FakeRepository.report_rows[self.sid][0]['review_status'],'demo_approved')
        self.assertEqual(FakeRepository().reports(session_identity()[1]),[])


if __name__=='__main__': unittest.main()
