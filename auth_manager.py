"""Shared authentication for local Streamlit and keyless Cloud Run deployments."""
import os
from pathlib import Path

import google.auth
from google import genai
from google.cloud import bigquery
from google.oauth2 import service_account
import streamlit as st
import toml

DEFAULT_PROJECT_ID = "yotamsofer-513010"
DEFAULT_LOCATION = "global"
DEFAULT_MODEL_ID = "gemini-3.8-flash"


def _secrets():
    try:
        return st.secrets.to_dict()
    except FileNotFoundError:
        path = Path(__file__).parent / ".streamlit" / "secrets.toml"
        return toml.load(path) if path.exists() else {}


def get_config_value(key, default=None, secrets_dict=None):
    # Cloud Run environment overrides any local configuration.
    if secrets_dict is not None and key in secrets_dict:
        return secrets_dict[key]
    if key in os.environ:
        return os.environ[key]
    return _secrets().get(key, default)


def get_credentials(secrets_dict=None):
    # Do not even load a local key when running with a managed service identity.
    config = {} if os.environ.get("K_SERVICE") else (secrets_dict if secrets_dict is not None else _secrets())
    if config.get("gcp_service_account"):
        return service_account.Credentials.from_service_account_info(
            dict(config["gcp_service_account"]),
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        ), "service_account"
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"])
    return credentials, "adc"


class GoogleGenAIAdapter:
    def __init__(self, client, model_id, system_instruction=None):
        self.client = client
        self.model_id = model_id
        self.system_instruction = system_instruction

    def generate_content(self, prompt):
        return self.client.models.generate_content(
            model=self.model_id, contents=prompt,
            config={"system_instruction": self.system_instruction}
            if self.system_instruction else None)


@st.cache_resource(show_spinner=False)
def configure_gcp_auth(secrets_dict=None, system_instruction=None):
    project = get_config_value("GCP_PROJECT_ID", DEFAULT_PROJECT_ID, secrets_dict)
    location = get_config_value("GCP_LOCATION", DEFAULT_LOCATION, secrets_dict)
    model_id = get_config_value("GEMINI_MODEL_ID", DEFAULT_MODEL_ID, secrets_dict)
    diagnostics = dict(project_id=project, location=location, model_id=model_id,
                       auth_method="none", error=None)
    try:
        credentials, method = get_credentials(secrets_dict)
        client = genai.Client(vertexai=True, project=project, location=location,
                              credentials=credentials,
                              http_options={"timeout": 30000})
        diagnostics["auth_method"] = method
        model = GoogleGenAIAdapter(client, model_id, system_instruction)
        # Check once per cached configuration, rather than on every UI action.
        model.generate_content("Reply only: OK")
        return model, diagnostics
    except Exception as exc:
        diagnostics["error"] = str(exc)
        return None, diagnostics


@st.cache_resource(show_spinner=False)
def get_bq_client(project_id=None):
    credentials, _ = get_credentials()
    return bigquery.Client(credentials=credentials,
        project=project_id or get_config_value("GCP_PROJECT_ID", DEFAULT_PROJECT_ID))
