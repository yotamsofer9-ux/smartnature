"""Session-scoped demo persistence. The historical dataset is read-only here."""
import datetime as dt
import json
import logging
import uuid

from google.cloud import bigquery, storage

from auth_manager import get_bq_client, get_config_value, get_credentials

logger = logging.getLogger(__name__)


class StoreUnavailable(RuntimeError):
    pass


class LimitReached(StoreUnavailable):
    pass


class DemoRepository:
    def __init__(self):
        self.project = get_config_value("GCP_PROJECT_ID")
        self.client = get_bq_client(self.project)
        self.dataset = f"{self.project}.smart_nature_demo"
        self.location = "us-central1"

    def _query(self, sql, parameters=()):
        try:
            job = self.client.query(sql, location=self.location,
                job_config=bigquery.QueryJobConfig(query_parameters=list(parameters),
                                                   maximum_bytes_billed=100_000_000),
                timeout=15)
            return [dict(r) for r in job.result(timeout=25)]
        except Exception as exc:
            if any(marker in str(exc) for marker in ("session limit", "global limit", "cooldown")):
                logger.warning("demo_rate_limit_reached")
            else:
                logger.exception("demo_database_operation_failed")
            raise StoreUnavailable("השמירה אינה זמינה כרגע; נסו שוב בעוד רגע") from exc

    @staticmethod
    def param(name, value, kind="STRING"):
        return bigquery.ScalarQueryParameter(name, kind, value)

    def reserve_action(self, session_id):
        # One transaction enforces both shared and per-session limits across instances.
        # An aborted transaction fails closed: no model call or submission follows.
        sql = f"""
        BEGIN TRANSACTION;
        MERGE `{self.dataset}.usage` T
        USING (SELECT @sid AS scope) S
        ON T.scope=S.scope AND T.day=CURRENT_DATE('UTC')
        WHEN NOT MATCHED THEN INSERT (scope,day,calls,last_call)
        VALUES (S.scope,CURRENT_DATE('UTC'),0,TIMESTAMP '2000-01-01');
        ASSERT (SELECT COUNT(*)=1 FROM `{self.dataset}.usage`
                WHERE day=CURRENT_DATE('UTC') AND scope=@sid)
                AS 'usage counter not unique';
        ASSERT (SELECT MAX(calls)<20 FROM `{self.dataset}.usage`
                WHERE day=CURRENT_DATE('UTC') AND scope=@sid) AS 'session limit';
        ASSERT (SELECT COUNT(*)=1 FROM `{self.dataset}.budget` WHERE id='global') AS 'global counter missing';
        ASSERT (SELECT IF(day=CURRENT_DATE('UTC'),calls,0)<300
                FROM `{self.dataset}.budget` WHERE id='global') AS 'global limit';
        UPDATE `{self.dataset}.budget`
        SET calls=IF(day=CURRENT_DATE('UTC'),calls+1,1),day=CURRENT_DATE('UTC')
        WHERE id='global';
        ASSERT (SELECT MAX(last_call)<TIMESTAMP_SUB(CURRENT_TIMESTAMP(),INTERVAL 5 SECOND)
                FROM `{self.dataset}.usage` WHERE day=CURRENT_DATE('UTC') AND scope=@sid)
                AS 'cooldown';
        UPDATE `{self.dataset}.usage` SET calls=calls+1,last_call=CURRENT_TIMESTAMP()
        WHERE day=CURRENT_DATE('UTC') AND scope=@sid;
        COMMIT TRANSACTION;
        """
        try:
            self._query(sql, [self.param("sid", session_id)])
        except StoreUnavailable as exc:
            raise LimitReached("הפעולה לא אושרה. המתינו כמה שניות; ייתכן שהושגה מכסת ההדגמה היומית") from exc

    def chat(self, session_id):
        # Fetch latest messages first, then return chronological order.
        rows = self._query(f"""SELECT message_id, role, content, created_at
            FROM `{self.dataset}.messages` WHERE session_id=@sid
            AND created_at>TIMESTAMP_SUB(CURRENT_TIMESTAMP(),INTERVAL 7 DAY)
            ORDER BY created_at DESC, message_id DESC LIMIT 20""", [self.param("sid", session_id)])
        return list(reversed(rows))

    def save_exchange(self, session_id, user_text, answer):
        now = dt.datetime.now(dt.timezone.utc)
        self._query(f"""INSERT INTO `{self.dataset}.messages`
            (message_id,session_id,created_at,role,content)
            VALUES (@uid,@sid,@now,'user',@question),
            (@aid,@sid,TIMESTAMP_ADD(@now,INTERVAL 1 MICROSECOND),'assistant',@answer)""", [
                self.param("uid", uuid.uuid4().hex), self.param("aid", uuid.uuid4().hex),
                self.param("sid", session_id), self.param("now", now, "TIMESTAMP"),
                self.param("question", user_text), self.param("answer", answer)])

    def reports(self, session_id):
        return self._query(f"""SELECT report_id,reserve_id,created_at,user_text,
            analysis,review_status,image_object FROM `{self.dataset}.reports`
            WHERE session_id=@sid AND created_at>TIMESTAMP_SUB(CURRENT_TIMESTAMP(),INTERVAL 7 DAY)
            ORDER BY created_at DESC LIMIT 20""", [self.param("sid", session_id)])

    def submit_report(self, session_id, reserve_id, text, analysis, image=None):
        report_id = uuid.uuid4().hex
        object_name = None
        if image:
            object_name = f"reports/{session_id}/{report_id}.jpg"
            try:
                credentials, _ = get_credentials()
                client = storage.Client(project=self.project, credentials=credentials)
                bucket = client.bucket(get_config_value("REPORTS_BUCKET",
                                                       f"{self.project}-smartnature-reports"))
                bucket.blob(object_name).upload_from_string(
                    image, content_type="image/jpeg", if_generation_match=0, timeout=20)
            except Exception as exc:
                logger.exception("private_image_upload_failed")
                raise StoreUnavailable("התמונה לא נשמרה; הדיווח לא נשלח") from exc
        # If DB submission fails, an unattached object expires by the bucket lifecycle.
        self._query(f"""INSERT INTO `{self.dataset}.reports`
            (report_id,session_id,reserve_id,created_at,user_text,analysis,review_status,image_object)
            VALUES (@rid,@sid,@reserve,CURRENT_TIMESTAMP(),@text,PARSE_JSON(@analysis),'pending',@image)""", [
                self.param("rid", report_id), self.param("sid", session_id),
                self.param("reserve", int(reserve_id), "INT64"), self.param("text", text),
                self.param("analysis", json.dumps(analysis, ensure_ascii=False)),
                self.param("image", object_name)])
        return report_id

    def simulate_review(self, session_id, report_id):
        self._query(f"""UPDATE `{self.dataset}.reports` SET review_status='demo_approved'
            WHERE session_id=@sid AND report_id=@rid AND review_status='pending'
            AND created_at>TIMESTAMP_SUB(CURRENT_TIMESTAMP(),INTERVAL 7 DAY)""", [
                self.param("sid", session_id), self.param("rid", report_id)])

    def report_image(self, session_id, report_id):
        matches = [r for r in self.reports(session_id) if r["report_id"] == report_id]
        if not matches or not matches[0]["image_object"]:
            raise StoreUnavailable("התמונה לא נמצאה בביקור שלך")
        try:
            credentials, _ = get_credentials()
            client = storage.Client(project=self.project, credentials=credentials)
            bucket = client.bucket(get_config_value("REPORTS_BUCKET",
                                                   f"{self.project}-smartnature-reports"))
            return bucket.blob(matches[0]["image_object"]).download_as_bytes(timeout=20)
        except Exception as exc:
            logger.exception("private_image_download_failed")
            raise StoreUnavailable("לא ניתן להציג את התמונה כרגע") from exc
