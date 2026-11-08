"""Read-only comparison of source/destination data and initial UI smoke check."""
from collections import Counter
import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import os
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from google.cloud import bigquery
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials


def canonical(value):
    if isinstance(value, datetime.datetime):
        return value.astimezone(datetime.timezone.utc).isoformat()
    if isinstance(value, (datetime.date, datetime.time)):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def contents(client, table):
    return Counter(json.dumps(dict(row), sort_keys=True, ensure_ascii=False,
                              default=canonical)
                   for row in client.list_rows(table, timeout=30))


def main():
    source = bigquery.Client(project="yotamsofer", credentials=
        service_account.Credentials.from_service_account_file(
            "yotamsofer-a74cc3540a49.json"))
    token = subprocess.check_output(["gcloud", "auth", "print-access-token"],
                                    text=True).strip()
    target = bigquery.Client(project="yotamsofer-513010", credentials=Credentials(token))
    results = []
    for name in ("users", "reserves", "reports", "chat_history"):
        left = source.get_table(f"yotamsofer.smart_nature_DB.{name}", timeout=30)
        right = target.get_table(f"yotamsofer-513010.smart_nature_DB.{name}", timeout=30)
        a, b = contents(source, left), contents(target, right)
        schema_matches = ([f.to_api_repr() for f in left.schema] ==
                          [f.to_api_repr() for f in right.schema])
        result = dict(table=name, source_rows=sum(a.values()),
                      target_rows=sum(b.values()), content_matches=a == b,
                      schema_matches=schema_matches)
        results.append(result)
        print(json.dumps(result), flush=True)
    assert all(r["content_matches"] and r["schema_matches"] for r in results)
    hashes = {name: hashlib.sha256(Path(name).read_bytes()).hexdigest()
              for name in ("app.py", "auth_manager.py", "requirements.txt",
                           "assets/knowledge_base.py", "ai_service.py", "domain.py", "repository.py",
                           "assets/reserves_demo.csv", "assets/display_names.py")}
    # Exercise initial Streamlit execution with actual target DB/AI, but make
    # weather unavailable immediately to avoid 50 unrelated external requests.
    from streamlit.testing.v1 import AppTest
    with patch.dict(os.environ, {"SMARTNATURE_OFFLINE": "1"}):
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=30).run()
    print(json.dumps({"initial_ui_exceptions": len(app.exception),
                      "ui_error_count": len(app.error),
                      "navigation": list(app.sidebar.radio[0].options)}), flush=True)
    report = dict(tables=results, file_sha256=hashes,
                  initial_ui_exceptions=len(app.exception),
                  ui_error_count=len(app.error),
                  weather_mocked=True, interactive_flows_tested=False)
    Path("infra/restoration_audit.json").write_text(
        json.dumps(report, indent=2) + "\n")
    assert not app.exception, "Initial UI execution failed"
    assert not app.error, "UI reported errors"


if __name__ == "__main__":
    main()
