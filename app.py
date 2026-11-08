"""Smart Nature: an isolated, explicitly historical public demonstration."""
import copy
import csv
import datetime as dt
import html
import json
import logging
import os
from pathlib import Path

import folium
import requests
import streamlit as st
from streamlit_folium import st_folium

from domain import (MAX_TEXT, demo_hazard, eligible_reserves, session_identity,
                    unknown_hazard, validate_image, apply_demo_reviews as apply_overlays, requested_exclusions)
from repository import DemoRepository, StoreUnavailable
from assets.display_names import DISPLAY

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)
st.set_page_config(page_title="Smart Nature | מטיילים עם הקשר", page_icon="🌿", layout="wide")
st.markdown('''<style>
@import url('https://fonts.googleapis.com/css2?family=Heebo:wght@400;500;600;700;800&display=swap');
.stApp {background:#f5f7f0;color:#173e33;font-family:Heebo,Arial,sans-serif}
[data-testid="stSidebar"] {background:#e9efe3;border-left:1px solid #d3dfcc}
h1,h2,h3 {color:#173e33!important;letter-spacing:-.03em}
[data-testid="stMetric"] {background:white;border:1px solid #dce5d4;border-radius:14px;padding:18px}
.stButton>button {border-radius:10px;min-height:44px}
.hero {background:#173e33;border-radius:22px;padding:36px;color:#f5f7f0;margin-bottom:24px}
.hero h1 {color:#f5f7f0!important;font-size:3rem;line-height:1.2;margin:10px 0}
.hero p {max-width:720px;font-size:1.1rem;line-height:1.8;color:#d8e4d1}
.eyebrow {font-size:.85rem;letter-spacing:.12em;color:#c9de90}
[data-testid="stMainBlockContainer"], .stMarkdown, .stRadio, .stSelectbox,
.stTextArea, .stChatInput, .stButton, [data-testid="stCaptionContainer"],
[data-testid="stChatMessage"], [data-testid="stMetric"] {direction:rtl;text-align:right}
@media(max-width:700px) {.hero{padding:24px}.hero h1{font-size:2.2rem}}
[data-testid="stCaptionContainer"] {color:#50604e}
a {color:#27634a}
</style>''', unsafe_allow_html=True)

STATUS = {"Green": "🟢 ירוק בדוגמה", "Yellow": "🟡 נדרשת תשומת לב בדוגמה",
          "Red": "🔴 אדום בדוגמה", "Closed": "⛔ סגור בדוגמה", "Unknown": "⚪ לא ידוע"}
VEHICLES = {"רכב פרטי": "Private Car", "רכב שטח 4×4": "4x4", "טיול רגלי": "Hiking"}
SKILLS = {"מתחילים": "Beginner", "מנוסים": "Advanced", "מתקדמים": "Expert"}
ACCESS = {"Accessible": "גישה לפי נתוני הדמו: רגילה; אינה אישור נגישות לנכים",
          "Private Car": "גישה לרכב פרטי לפי נתוני הדמו", "4x4 Only": "גישה לרכב שטח בלבד לפי נתוני הדמו",
          "Hiking Only": "מסלול הליכה לפי נתוני הדמו", "Closed": "סגור בנתוני הדמו"}


@st.cache_data(show_spinner=False)
def load_seed():
    with open(Path(__file__).parent / "assets/reserves_demo.csv", newline="") as stream:
        rows = list(csv.DictReader(stream))
    for row in rows:
        row.update(id=int(row["id"]), lat=float(row["lat"]), lon=float(row["lon"]))
        row["source_name"], row["source_description"] = row["name"], row["description"]
        name, description = DISPLAY.get(row["id"], (row["name"], None))
        row["name"], row["description"] = name, description or row["description"]
    return rows


@st.cache_resource(show_spinner=False)
def repository():
    return DemoRepository()


@st.cache_resource(show_spinner=False)
def ai():
    from ai_service import AIService
    return AIService()


@st.cache_data(ttl=900, show_spinner=False, max_entries=100)
def weather(lat, lon):
    if os.environ.get("SMARTNATURE_OFFLINE") == "1":
        return None
    try:
        response = requests.get("https://api.open-meteo.com/v1/forecast",
            params={"latitude": lat, "longitude": lon,
                    "current": "temperature_2m,weather_code"}, timeout=(2, 3))
        response.raise_for_status()
        current = response.json()["current"]
        return {"temperature": current["temperature_2m"], "time": current["time"]}
    except (requests.RequestException, ValueError, KeyError):
        logger.warning("weather_unavailable")
        return None


def reserve_card(row, reason=None):
    with st.container(border=True):
        st.subheader(row["name"])
        st.write(STATUS.get(row["status"], STATUS["Unknown"]))
        if reason:
            st.write(reason)
        st.write(row["description"])
        st.caption(ACCESS.get(row["accessibility"], "סוג הגישה לא אומת"))
        st.caption(f"מקור: נתוני הפרויקט ההיסטוריים · עודכן במקור: {row['last_update']}")
        st.caption("רמת הקושי והנגישות בפועל לא אומתו. יש לבדוק מקורות רשמיים לפני יציאה.")




raw_token, session_id = session_identity(st.query_params.get("visit"))
if st.query_params.get("visit") != raw_token:
    st.query_params["visit"] = raw_token
if st.session_state.get("visitor") != session_id:
    st.session_state.clear()
    st.session_state["visitor"] = session_id

if "next_page" in st.session_state:
    st.session_state["page"] = st.session_state.pop("next_page")

with st.sidebar:
    st.image("SmartNatureLogo.jpeg", width=110)
    st.title("Smart Nature")
    st.caption("להבין את המסלול. לראות את ההקשר.")
    page = st.radio("לאן ממשיכים?", ["מבט ראשון", "מפת האתרים", "דיווח חכם", "סוכן הטיולים"], key="page")
    st.divider()
    vehicle_label = st.selectbox("איך מטיילים?", list(VEHICLES))
    skill_label = st.selectbox("ניסיון בטיולים", list(SKILLS))
    mode = st.radio("אופן ההדגמה", ["דמו מובנה", "AI מחובר"])
    st.caption("דמו מובנה משתמש בתוצאה קבועה ומסומנת. AI מחובר מנתח באמצעות המודל.")
    if st.button("התחלת ביקור חדש", use_container_width=True):
        st.query_params["visit"] = session_identity()[0]
        st.rerun()
    st.caption("הקישור האישי מאפשר חזרה לשיחה עד 7 ימים. אל תשתפו אותו אם הזנתם מידע אישי.")
    st.caption("להצגה לאחרים שתפו את כתובת האתר ללא הפרמטר visit.")

vehicle, skill = VEHICLES[vehicle_label], SKILLS[skill_label]
rows = copy.deepcopy(load_seed())
repo, backend_error = None, None
if os.environ.get("SMARTNATURE_OFFLINE") != "1":
    try:
        repo = repository()
    except Exception:
        logger.exception("repository_initialization_failed")
        backend_error = "השמירה אינה זמינה. אפשר לעיין בדמו; שליחה ו־AI מושבתים כרגע."
else:
    backend_error = "מצב בדיקה מקומי: נתוני הדוגמה זמינים; שליחה ו־AI מושבתים."

st.caption("סביבת הדגמה · 50 אתרים מתוך עותק היסטורי · הסטטוסים אינם מידע בטיחותי בזמן אמת")
if backend_error:
    st.info(backend_error)

if page == "מבט ראשון":
    st.markdown('''<div class="hero"><div class="eyebrow">SMART NATURE / FIELD INTELLIGENCE</div>
    <h1>הטיול הבא מתחיל<br>בתמונה רחבה יותר.</h1>
    <p>מפה, דיווחי שטח ועוזר טיולים במקום אחד. בחרו פרופיל, בחנו מסלול,
    ודמו כיצד דיווח מהשטח הופך לתובנה — בלי לשנות מידע של מבקרים אחרים.</p></div>''', unsafe_allow_html=True)
    c1,c2,c3 = st.columns(3)
    c1.metric("אתרי דוגמה", len(rows))
    c2.metric("שלבי התנסות", "3")
    c3.metric("הרשאות אישיות", "0")
    st.subheader("שלוש דקות בתוך המוצר")
    st.write("1. בחרו פרופיל ומסלול במפה.  2. שלחו דיווח לדוגמה.  3. בחנו המלצה חלופית בסוכן.")
    a,b,c = st.columns(3)
    for col, title, target in [(a,"🗺️ בחירת מסלול","מפת האתרים"),
                              (b,"📷 ניתוח דיווח","דיווח חכם"),(c,"🌿 שיחה עם הסוכן","סוכן הטיולים")]:
        if col.button(title, use_container_width=True):
            st.session_state["next_page"] = target
            st.rerun()
    st.info("הדיווחים כאן נכנסים לתור הדגמה בלבד. אין חיבור לגורם חירום או מוקד שמאשר פתיחת מסלולים.")
    with st.expander("מקורות המידע, פרטיות ומגבלות"):
        st.write("האתרים והסטטוסים הם עותק היסטורי ממסד הפרויקט שהועתק ואומת ב־8 בנובמבר 2026. מועד העדכון המקורי מוצג לכל אתר. פרטי המידע לא אומתו מול רשות מוסמכת.")
        st.write("מזג האוויר, כשמוצג, מתקבל מ־Open-Meteo ונשמר במטמון עד 15 דקות. הוא אינו התראת שיטפונות או אישור בטיחות.")
        st.write("שיחות ודיווחים נפרדים לכל קישור ביקור ונשמרים עד 7 ימים במסגרת מחזור מחיקה. תמונות נשמרות באחסון פרטי, מוקטנות ומוסרות מהן מטא־נתונים כגון GPS. במצב AI תוכן הבקשה נשלח לשירות המודל של Google.")
        st.write("אין להזין פרטים מזהים או מידע רגיש. זהו אבטיפוס להצגת יכולות; התאמת מסלול, קושי ונגישות דורשים אימות נוסף.")

elif page == "מפת האתרים":
    st.title("מפת האתרים")
    reports = []
    if repo:
        try:
            reports = repo.reports(session_id)
            rows = apply_overlays(rows, reports)
        except StoreUnavailable as exc:
            st.info(str(exc))
    compatible_only = st.checkbox("הצגת מועמדים התואמים לפרופיל בלבד", value=True)
    visible = eligible_reserves(rows, vehicle) if compatible_only else rows
    if not visible:
        st.info("לא נמצאו אתרים התואמים לפרופיל בנתוני הדוגמה.")
    else:
        selected_name = st.selectbox("בחרו אתר לפרטים", [r["name"] for r in visible])
        selected = next(r for r in visible if r["name"] == selected_name)
        map_col, details_col = st.columns([1.5, 1])
        with map_col:
            m = folium.Map(location=[31.6,35.0], zoom_start=7, tiles="OpenStreetMap")
            colors = {"Green":"green","Yellow":"orange","Red":"red","Closed":"gray"}
            for r in visible:
                folium.CircleMarker([r["lat"],r["lon"]],radius=7 if r["id"]==selected["id"] else 4,
                    color=colors.get(r["status"],"gray"),fill=True,
                    tooltip=html.escape(r["name"])).add_to(m)
            st_folium(m, use_container_width=True, height=480, returned_objects=[])
        with details_col:
            reserve_card(selected)
            with st.spinner("בודקים מזג אוויר לאתר הנבחר…"):
                current = weather(selected["lat"], selected["lon"])
            if current:
                st.metric("טמפרטורה לפי Open-Meteo", f"{current['temperature']}°C")
                st.caption(f"שעת הנתון: {current['time']} UTC · מטמון עד 15 דקות")
            else:
                st.caption("מזג האוויר אינו זמין כרגע; יתר ההדגמה ממשיך לפעול.")
    if any(r["review_status"]=="demo_approved" for r in reports):
        st.warning("במפה מוצגים גם שינויים שהדמית בביקור שלך בלבד. נתוני המקור לא השתנו.")

elif page == "דיווח חכם":
    st.title("מה ראיתם בשטח?")
    st.write("מדווחים, מנתחים ומעבירים לבדיקה. הערכת AI אינה משנה סטטוס מאושר.")
    st.info("להדגמה: בחרו אתר וכתבו ‘ענף גדול חוסם חלק מהשביל’. אפשר לצרף תמונה.")
    with st.form("report", clear_on_submit=True):
        reserve_name = st.selectbox("באיזה אתר?", [r["name"] for r in rows])
        text = st.text_area("תיאור המצב", max_chars=MAX_TEXT, placeholder="מה קרה, והיכן בתוך המסלול?")
        uploaded = st.file_uploader("תמונה לדיווח (JPEG / PNG, עד 5MB)", type=["jpg","jpeg","png"])
        submitted = st.form_submit_button("ניתוח ושליחה לתור ההדגמה", disabled=repo is None)
    if submitted and repo:
        try:
            if not text.strip() and uploaded is None:
                raise ValueError("יש להוסיף תיאור או תמונה")
            image = validate_image(uploaded.getvalue())[0] if uploaded else None
            repo.reserve_action(session_id)
            with st.spinner("מנתחים ושומרים את הדיווח…"):
                if mode=="דמו מובנה":
                    analysis, live_ok = demo_hazard(), True
                else:
                    try:
                        analysis, live_ok = ai().hazard(text, image)
                    except Exception:
                        logger.exception("ai_initialization_failed")
                        analysis, live_ok = unknown_hazard(), False
                selected = next(r for r in rows if r["name"]==reserve_name)
                repo.submit_report(session_id, selected["id"], text, analysis, image)
            st.success("הדיווח נשמר בתור ההדגמה האישי. לא השתנה סטטוס מאושר.")
            if not live_ok:
                st.warning("המודל לא הצליח להעריך את הדיווח. נשמר סטטוס ‘לא ידוע’ לבדיקה.")
            st.write(analysis["short_description"])
        except (ValueError, StoreUnavailable) as exc:
            st.warning(str(exc))
        except Exception:
            logger.exception("report_submission_failed")
            st.warning("לא הצלחנו להשלים את הפעולה. נסו שוב בעוד רגע; ייתכן שהדיווח כבר נשמר.")
    st.subheader("תור הבדיקה של הביקור שלי")
    if repo:
        try:
            reports = repo.reports(session_id)
            if not reports:
                st.caption("הדיווח הראשון שלכם יופיע כאן.")
            for report in reports:
                analysis = report["analysis"]
                if isinstance(analysis,str): analysis=json.loads(analysis)
                with st.container(border=True):
                    name = next((r["name"] for r in rows if r["id"]==report["reserve_id"]),"אתר")
                    st.write(f"**{name}** · {report['created_at'].strftime('%d/%m/%Y %H:%M')} UTC")
                    st.write(report["user_text"])
                    st.write(analysis["short_description"])
                    st.caption("מקור: הערכת דיווח בהדגמה · " + STATUS.get(analysis["severity"], STATUS["Unknown"]))
                    st.caption("תמונה נשמרה באחסון פרטי" if report["image_object"] else "ללא תמונה")
                    if report["image_object"] and st.button("הצגת התמונה שלי", key="image_"+report["report_id"]):
                        st.image(repo.report_image(session_id, report["report_id"]), caption="תמונה פרטית מהדיווח שלך")
                    if report["review_status"]=="pending":
                        st.caption("ממתין לבדיקה — תור הדגמה ללא מוקד תפעולי")
                        if st.button("הדמיית אישור בדמו שלי בלבד",key=report["report_id"]):
                            repo.reserve_action(session_id)
                            repo.simulate_review(session_id,report["report_id"])
                            st.rerun()
                    else:
                        st.caption("האישור הודמה בביקור שלך בלבד; ניתן לבחון את השפעתו במפה ובסוכן.")
        except StoreUnavailable as exc:
            st.info(str(exc))

elif page == "סוכן הטיולים":
    st.title("לאן מתאים להמשיך?")
    st.write("המלצות מתוך אתרי הדוגמה, לאחר סינון לפי רכב וסטטוס. אין כאן אישור בטיחות או דירוג קושי מאומת.")
    history, reports = [], []
    if repo:
        try:
            history, reports = repo.chat(session_id), repo.reports(session_id)
            rows = apply_overlays(rows,reports)
        except StoreUnavailable as exc:
            st.info(str(exc))
    for message in history:
        with st.chat_message(message["role"]):
            st.write(message["content"])
    example = None
    c1,c2 = st.columns(2)
    if c1.button("מסלול שמתאים לרכב שלי",disabled=repo is None):
        example="הצע מסלול מתוך נתוני הדוגמה שמתאים לפרופיל שלי והסבר את ההתאמה"
    if c2.button("חלופה לאתר שסגור בדוגמה",disabled=repo is None):
        example="הצע חלופה לאתר שסגור בנתוני הדוגמה"
    question = st.chat_input("מה תרצו לחוות בטיול?",max_chars=MAX_TEXT,disabled=repo is None) or example
    if question and repo:
        try:
            repo.reserve_action(session_id)
            candidates = eligible_reserves(rows,vehicle)
            excluded = requested_exclusions(question, rows, vehicle)
            with st.spinner("בוחרים מתוך האתרים התואמים…"):
                if mode=="דמו מובנה":
                    choices=[{"id":r["id"],"reason":"בחירה קבועה לדמו: סוג הגישה תואם לפרופיל והאתר אינו סגור בנתונים ההיסטוריים."}
                             for r in candidates[:3]]
                    live_ok=True
                else:
                    choices,live_ok=ai().recommend(question,rows,vehicle,skill,
                        [{"role":m["role"],"content":m["content"]} for m in history])
            answer="\n\n".join(next(r["name"] for r in rows if r["id"]==x["id"])+": "+x["reason"] for x in choices)
            if not answer:
                answer="לא ניתן להציע מסלול כרגע." if not live_ok else "לא נמצאה התאמה בנתוני הדוגמה לפרופיל הנבחר."
            if excluded:
                answer = "\n".join(excluded) + "\n\nחלופות מתוך נתוני הדוגמה:\n" + answer
            answer += "\n\nמידע היסטורי להדגמה בלבד. יש לבדוק פתיחה, תנאים, קושי ונגישות במקור רשמי לפני יציאה."
            if mode=="דמו מובנה": answer="דמו מובנה — אינו תשובת AI.\n\n"+answer
            repo.save_exchange(session_id,question,answer)
            with st.chat_message("user"): st.write(question)
            with st.chat_message("assistant"): st.write(answer)
            for choice in choices:
                reserve_card(next(r for r in rows if r["id"]==choice["id"]))
        except StoreUnavailable as exc:
            st.warning(str(exc))
        except Exception:
            logger.exception("chat_failed")
            st.warning("הסוכן אינו זמין כרגע. אפשר להמשיך לעיין באתרים במפה.")

st.divider()
st.caption("SMART NATURE · אבטיפוס להצגת יכולות · לא שירות חירום ולא מקור רשמי למצב מסלולים")
