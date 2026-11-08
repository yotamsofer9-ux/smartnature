"""Copy the four original tables using separate source/target identities.

Never replaces existing destination tables. Data stays in memory during transfer.
"""
import datetime
import io
import json
import subprocess

from google.cloud import bigquery
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from google.api_core.exceptions import NotFound


def main():
    source = bigquery.Client(project="yotamsofer", credentials=
        service_account.Credentials.from_service_account_file(
            "yotamsofer-a74cc3540a49.json"))
    token = subprocess.check_output(
        ["gcloud", "auth", "print-access-token"], text=True).strip()
    target = bigquery.Client(project="yotamsofer-513010",
                             credentials=Credentials(token))
    source_dataset = source.get_dataset("yotamsofer.smart_nature_DB", timeout=30)
    destination = "yotamsofer-513010.smart_nature_DB"
    try:
        dataset = target.get_dataset(destination, timeout=30)
        if dataset.location != source_dataset.location:
            raise RuntimeError("Destination dataset location differs from source")
    except NotFound:
        dataset = None

    transfers = []
    for name in ("users", "reserves", "reports", "chat_history"):
        table = source.get_table(f"yotamsofer.smart_nature_DB.{name}", timeout=30)
        try:
            target.get_table(f"{destination}.{name}", timeout=30)
        except NotFound:
            pass
        else:
            raise RuntimeError(f"Destination table already exists: {name}; stopping")
        rows = [dict(row) for row in source.list_rows(table, timeout=30)]
        if len(rows) != table.num_rows:
            raise RuntimeError(f"Source row count changed for {name}; stopping")
        transfers.append((name, table, rows))
        print(json.dumps({"preflight": name, "rows": len(rows)}), flush=True)

    if dataset is None:
        dataset = bigquery.Dataset(destination)
        dataset.location = source_dataset.location
        target.create_dataset(dataset, timeout=30)
        print(json.dumps({"created_dataset": destination}), flush=True)

    def encode(value):
        if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
            return value.isoformat()
        raise TypeError(type(value).__name__)

    for name, original, rows in transfers:
        table_id = f"{destination}.{name}"
        # WRITE_EMPTY protects against a destination appearing after preflight.
        payload = "\n".join(json.dumps(row, default=encode) for row in rows).encode()
        config = bigquery.LoadJobConfig(
            schema=original.schema,
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
            write_disposition=bigquery.WriteDisposition.WRITE_EMPTY)
        job = target.load_table_from_file(io.BytesIO(payload), table_id,
            job_config=config, location=dataset.location, timeout=30)
        job.result(timeout=120)
        copied = target.get_table(table_id, timeout=30)
        if (copied.num_rows != len(rows) or
                [f.to_api_repr() for f in copied.schema] !=
                [f.to_api_repr() for f in original.schema]):
            raise RuntimeError(f"Verification failed: {name}")
        print(json.dumps({"verified": table_id, "rows": copied.num_rows,
                          "schema_matches": True, "job": job.job_id}), flush=True)


if __name__ == "__main__":
    main()
