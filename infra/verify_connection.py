"""Verify the app identity, table reads, and AI access (historical tables stay read-only)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from auth_manager import get_bq_client, get_config_value, configure_gcp_auth


def main():
    project = get_config_value("GCP_PROJECT_ID")
    assert project == "yotamsofer-513010", "Unexpected destination"
    client = get_bq_client(project)
    for name in ("users", "reserves", "reports", "chat_history"):
        query = f"SELECT COUNT(*) AS n FROM `{project}.smart_nature_DB.{name}`"
        result = list(client.query(query, location="us-central1").result(timeout=60))
        print(json.dumps({"table": name, "rows": result[0].n}), flush=True)
    # The historical dataset is intentionally read-only for the app identity.
    # Use acceptance_check.py for rolled-forward demo writes and isolation tests.
    model, diagnostics = configure_gcp_auth(system_instruction="Reply briefly.")
    print(json.dumps({"ai_connected": model is not None, **diagnostics}), flush=True)
    if model is None:
        raise RuntimeError("AI connection verification failed")


if __name__ == "__main__":
    main()
