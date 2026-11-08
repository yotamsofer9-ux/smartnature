"""Create isolated demo tables, a private upload bucket and limited runtime access."""
import subprocess
from google.cloud import bigquery, storage
from google.api_core.exceptions import NotFound
from google.oauth2.credentials import Credentials

PROJECT = "yotamsofer-513010"
DATASET = f"{PROJECT}.smart_nature_demo"
BUCKET = f"{PROJECT}-smartnature-reports"
SCHEMAS = {
    "budget": [("id","STRING"),("day","DATE"),("calls","INTEGER")],
    "messages": [("message_id","STRING"),("session_id","STRING"),("created_at","TIMESTAMP"),
                 ("role","STRING"),("content","STRING")],
    "reports": [("report_id","STRING"),("session_id","STRING"),("reserve_id","INTEGER"),
                ("created_at","TIMESTAMP"),("user_text","STRING"),("analysis","JSON"),
                ("review_status","STRING"),("image_object","STRING")],
    "usage": [("scope","STRING"),("day","DATE"),("calls","INTEGER"),("last_call","TIMESTAMP")],
}


def main():
    token = subprocess.check_output(["gcloud","auth","print-access-token"],text=True).strip()
    credentials = Credentials(token)
    bq = bigquery.Client(project=PROJECT, credentials=credentials)
    dataset = bigquery.Dataset(DATASET)
    dataset.location = "us-central1"
    dataset.description = "Isolated guest demo. Historical data is never mutated. Seven-day retention."
    bq.create_dataset(dataset, exists_ok=True, timeout=30)
    for name, fields in SCHEMAS.items():
        table = bigquery.Table(f"{DATASET}.{name}", schema=[bigquery.SchemaField(n,t) for n,t in fields])
        if name != "budget":
            table.time_partitioning = bigquery.TimePartitioning(field="day" if name=="usage" else "created_at",
                                                           expiration_ms=7*86400*1000)
        if name not in {"usage","budget"}:
            table.clustering_fields = ["session_id"]
        bq.create_table(table, exists_ok=True, timeout=30)
        actual = bq.get_table(table.reference,timeout=30)
        assert [(f.name,f.field_type) for f in actual.schema] == fields
        if name != "budget":
            assert actual.time_partitioning.expiration_ms == 7*86400*1000
        print(f"Verified demo table: {name}", flush=True)
    bq.query(f"""MERGE `{DATASET}.budget` T USING (SELECT 'global' AS id) S
        ON T.id=S.id WHEN NOT MATCHED THEN INSERT (id,day,calls)
        VALUES ('global',CURRENT_DATE('UTC'),0)""",location="us-central1").result(timeout=60)
    import toml
    local = toml.load(".streamlit/secrets.toml")["gcp_service_account"]["client_email"]
    identities = [local,f"smartnature-runtime@{PROJECT}.iam.gserviceaccount.com"]
    for ds_id, role in [(DATASET,"WRITER"),(f"{PROJECT}.smart_nature_DB","READER")]:
        ds = bq.get_dataset(ds_id,timeout=30)
        ds.access_entries = [e for e in ds.access_entries
                            if not (e.entity_type=="userByEmail" and e.entity_id in identities)] + [
                            bigquery.AccessEntry(role,"userByEmail",email) for email in identities]
        bq.update_dataset(ds,["access_entries"],timeout=30)
    client = storage.Client(project=PROJECT,credentials=credentials)
    try:
        bucket = client.get_bucket(BUCKET,timeout=30)
    except NotFound:
        bucket = client.bucket(BUCKET)
        bucket.iam_configuration.uniform_bucket_level_access_enabled = True
        bucket.iam_configuration.public_access_prevention = "enforced"
        bucket = client.create_bucket(bucket,location="us-central1",timeout=30)
    bucket.iam_configuration.uniform_bucket_level_access_enabled = True
    bucket.iam_configuration.public_access_prevention = "enforced"
    bucket.add_lifecycle_delete_rule(age=7)
    bucket.patch(timeout=30)
    policy = bucket.get_iam_policy(requested_policy_version=3,timeout=30)
    for role in ["roles/storage.objectCreator","roles/storage.objectViewer"]:
        policy.setdefault(role,set()).update(f"serviceAccount:{email}" for email in identities)
    bucket.set_iam_policy(policy,timeout=30)
    print("Private upload bucket and scoped access verified.",flush=True)


if __name__ == "__main__":
    main()
