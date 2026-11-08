"""Make a private, checksummed recovery archive; never include credential files."""
import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

from google.cloud import bigquery
from google.oauth2.credentials import Credentials


def main():
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = Path("backups") / stamp
    root.mkdir(parents=True, mode=0o700)
    token = subprocess.check_output(["gcloud", "auth", "print-access-token"],
                                    text=True).strip()
    client = bigquery.Client(project="yotamsofer-513010", credentials=Credentials(token))
    manifest = {"project": client.project, "created_utc": stamp, "tables": {}}
    for name in ("users", "reserves", "reports", "chat_history"):
        table = client.get_table(f"{client.project}.smart_nature_DB.{name}", timeout=30)
        data = [dict(r) for r in client.list_rows(table, timeout=30)]
        path = root / f"{name}.json"
        path.write_text(json.dumps({"schema": [f.to_api_repr() for f in table.schema],
                                    "rows": data}, default=str, ensure_ascii=False))
        path.chmod(0o600)
        manifest["tables"][name] = {"rows": len(data), "sha256":
                                   hashlib.sha256(path.read_bytes()).hexdigest()}
        if name == "reserves":
            # Public demo seed contains locations only, never users or conversations.
            import csv
            with open("assets/reserves_demo.csv", "w", newline="") as output:
                writer = csv.DictWriter(output, fieldnames=[f.name for f in table.schema])
                writer.writeheader()
                writer.writerows(data)
    tracked = subprocess.check_output(["git", "ls-files"], text=True).splitlines()
    with zipfile.ZipFile(root / "code_before_changes.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for path in tracked:
            if Path(path).is_file() and not path.endswith((".json", "secrets.toml")):
                z.write(path)
        for path in Path("infra").glob("*.py"):
            z.write(path)
        z.write("RECOVERY_PLAN.md")
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps({"backup": str(root), "rows":
                      {k: v["rows"] for k, v in manifest["tables"].items()}}))


if __name__ == "__main__":
    main()
