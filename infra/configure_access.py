"""Grant app access in the new project without creating service-account keys."""
import subprocess
import toml
from google.cloud import bigquery
from google.oauth2.credentials import Credentials

PROJECT = "yotamsofer-513010"
RUNTIME = f"smartnature-runtime@{PROJECT}.iam.gserviceaccount.com"


def gcloud(*args):
    return subprocess.check_output(["gcloud", *args], text=True).strip()


def main():
    local = toml.load(".streamlit/secrets.toml")["gcp_service_account"]["client_email"]
    accounts = gcloud("iam", "service-accounts", "list", f"--project={PROJECT}",
                      "--format=value(email)").splitlines()
    if RUNTIME not in accounts:
        gcloud("iam", "service-accounts", "create", "smartnature-runtime",
               f"--project={PROJECT}", "--display-name=Smart Nature runtime")
    for email in [local, RUNTIME]:
        for role in ["roles/bigquery.jobUser", "roles/aiplatform.user",
                     "roles/serviceusage.serviceUsageConsumer"]:
            gcloud("projects", "add-iam-policy-binding", PROJECT,
                   f"--member=serviceAccount:{email}", f"--role={role}",
                   "--condition=None", "--format=none")
        print(f"Project roles configured: {email}", flush=True)
    client = bigquery.Client(project=PROJECT, credentials=Credentials(
        gcloud("auth", "print-access-token")))
    dataset = client.get_dataset(f"{PROJECT}.smart_nature_DB", timeout=30)
    entries = list(dataset.access_entries)
    for email in [local, RUNTIME]:
        if not any(e.entity_type == "userByEmail" and e.entity_id == email
                   and e.role in ("WRITER", "OWNER") for e in entries):
            entries.append(bigquery.AccessEntry("WRITER", "userByEmail", email))
    dataset.access_entries = entries
    client.update_dataset(dataset, ["access_entries"], timeout=30)
    print("Dataset access configured; no keys created.", flush=True)


if __name__ == "__main__":
    main()
