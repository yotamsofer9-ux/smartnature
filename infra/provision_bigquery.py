"""Inspect or create the app's BigQuery tables without replacing existing data.

Use --apply to create missing resources. No local historical data is imported.
"""
import argparse
import json

from google.cloud import bigquery
from google.oauth2 import service_account
from google.api_core.exceptions import NotFound


SCHEMAS = {
    "users": [("id", "INTEGER"), ("username", "STRING"),
              ("vehicle_type", "STRING"), ("skill_level", "STRING")],
    "reserves": [("id", "INTEGER"), ("name", "STRING"), ("lat", "FLOAT"),
                 ("lon", "FLOAT"), ("status", "STRING"),
                 ("description", "STRING"), ("accessibility", "STRING"),
                 ("last_update", "TIMESTAMP")],
    "reports": [("id", "INTEGER"), ("reserve_id", "INTEGER"),
                ("timestamp", "TIMESTAMP"), ("user_text", "STRING"),
                ("ai_analysis", "JSON"), ("image_path", "STRING")],
    "chat_history": [("id", "INTEGER"), ("timestamp", "TIMESTAMP"),
                     ("role", "STRING"), ("content", "STRING")],
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="yotamsofer")
    parser.add_argument("--location", default="us-central1")
    parser.add_argument("--credentials", help="Optional service-account JSON path")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    creds = (service_account.Credentials.from_service_account_file(args.credentials)
             if args.credentials else None)
    client = bigquery.Client(project=args.project, credentials=creds)
    dataset_id = f"{args.project}.smart_nature_DB"
    # An existing dataset's location is authoritative; it is never changed.
    try:
        dataset = client.get_dataset(dataset_id, timeout=30)
        print(json.dumps({"dataset": dataset_id, "state": "exists",
                          "location": dataset.location}))
    except NotFound:
        dataset = bigquery.Dataset(dataset_id)
        dataset.location = args.location
        if args.apply:
            dataset = client.create_dataset(dataset, exists_ok=True, timeout=30)
        print(json.dumps({"dataset": dataset_id,
                          "state": "created" if args.apply else "missing",
                          "location": dataset.location}))

    # Inspect every table before any table creation. Refuse incompatible schemas.
    missing = []
    for name, fields in SCHEMAS.items():
        table_id = f"{dataset_id}.{name}"
        try:
            table = client.get_table(table_id, timeout=30)
            actual = {f.name: f.field_type for f in table.schema}
            differences = {k: {"expected": t, "actual": actual.get(k)}
                           for k, t in fields if actual.get(k) != t}
            if differences:
                raise RuntimeError(f"Schema mismatch in {table_id}: {differences}")
            print(json.dumps({"table": table_id, "state": "exists",
                              "rows": table.num_rows}))
        except NotFound:
            missing.append(bigquery.Table(table_id, schema=[
                bigquery.SchemaField(k, t) for k, t in fields]))
    for table in missing:
        if args.apply:
            client.create_table(table, exists_ok=True, timeout=30)
            client.get_table(table.reference, timeout=30)
        print(json.dumps({"table": str(table.reference),
                          "state": "created_verified" if args.apply else "missing"}))


if __name__ == "__main__":
    main()
