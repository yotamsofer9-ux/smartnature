import streamlit as st
# Force reload
import vertexai
from vertexai.generative_models import GenerativeModel
from google.oauth2 import service_account
import google.auth
from google.api_core.exceptions import NotFound, PermissionDenied
import os
import toml
from google.cloud import bigquery

# Default config
DEFAULT_PROJECT_ID = None
DEFAULT_LOCATION = "us-central1"
DEFAULT_MODEL_ID = "gemini-2.5-flash"  # Upgrade from 1.5-flash

class GoogleGenAIAdapter:
    """
    Adapter to make google-genai response objects behave like Vertex AI objects.
    Specifically, exposes a .text property.
    """
    def __init__(self, client, model_id, system_instruction=None):
        self.client = client
        self.model_id = model_id
        self.system_instruction = system_instruction

    def generate_content(self, prompt):
        try:
            # Prepare config if system_instruction is present
            config = {}
            if self.system_instruction:
                config['system_instruction'] = self.system_instruction

            response = self.client.models.generate_content(
                model=self.model_id,
                contents=prompt,
                config=config if config else None
            )
            return GoogleGenAIResponseAdapter(response)
        except Exception as e:
            # Re-raise exceptions to be caught by the app's error handling
            raise e

class GoogleGenAIResponseAdapter:
    def __init__(self, response):
        self.response = response
    
    @property
    def text(self):
        return self.response.text

def get_config_value(key, default=None, secrets_dict=None):
    """
    Priority:
    1. Streamlit Secrets (secrets.toml)
    2. Environment Variables
    3. Default value
    """
    # 1. Try passed secrets_dict (for testing)
    if secrets_dict and key in secrets_dict:
        return secrets_dict[key]
    
    # 2. Try st.secrets
    try:
        if key in st.secrets:
            return st.secrets[key]
    except FileNotFoundError:
        pass
        
    # 3. Try manual TOML load (fallback for local dev if st.secrets fails)
    try:
        local_secrets = toml.load(".streamlit/secrets.toml")
        if key in local_secrets:
            return local_secrets[key]
    except Exception:
        pass

    # 4. Try Environment Variable
    if key in os.environ:
        return os.environ[key]

    return default

def configure_gcp_auth(secrets_dict=None, system_instruction=None):
    """
    Configures Authentication for Vertex AI with strict fallback.
    Returns: (model_object, diagnostics_dict)
    
    Diagnostics dict contains:
    - auth_method: 'service_account', 'adc', 'api_key', or 'none'
    - project_id: str
    - location: str
    - model_id: str
    - error: str (if any)
    """
    
    diagnostics = {
        "auth_method": "none",
        "project_id": "Unknown",
        "location": "Unknown",
        "model_id": "Unknown",
        "error": None
    }
    
    # 1. Load Configuration
    project_id = get_config_value("GCP_PROJECT_ID", DEFAULT_PROJECT_ID, secrets_dict)
    location = get_config_value("GCP_LOCATION", DEFAULT_LOCATION, secrets_dict)
    model_id = get_config_value("GEMINI_MODEL_ID", DEFAULT_MODEL_ID, secrets_dict)
    
    diagnostics["project_id"] = project_id
    diagnostics["location"] = location
    diagnostics["model_id"] = model_id
    
    model = None

    # ---------------------------------------------------------
    # STRATEGY 1: Service Account (Preferred)
    # ---------------------------------------------------------
    service_account_info = None
    # Check secrets for dict
    try:
        if st.secrets.get("gcp_service_account"):
             service_account_info = st.secrets["gcp_service_account"]
    except FileNotFoundError:
        pass
    
    # Fallback to local TOML if not in st.secrets object
    if not service_account_info:
        try:
             local_secrets = toml.load(".streamlit/secrets.toml")
             if "gcp_service_account" in local_secrets:
                 service_account_info = local_secrets["gcp_service_account"]
        except Exception:
            pass

    if service_account_info:
        try:
            creds = service_account.Credentials.from_service_account_info(service_account_info)
            vertexai.init(project=project_id, location=location, credentials=creds)
            model = GenerativeModel(model_id, system_instruction=system_instruction)
            diagnostics["auth_method"] = "service_account"
        except Exception as e:
            print(f"Service Account auth failed: {e}")
            # Continue to next strategy

    # ---------------------------------------------------------
    # STRATEGY 2: ADC (Standard Fallback)
    # ---------------------------------------------------------
    if not model:
        try:
            # Check if we can get default creds
            creds, _ = google.auth.default()
            vertexai.init(project=project_id, location=location, credentials=creds)
            model = GenerativeModel(model_id, system_instruction=system_instruction)
            diagnostics["auth_method"] = "adc"
        except Exception as e:
            print(f"ADC auth failed: {e}")
            # Continue to next strategy

    # ---------------------------------------------------------
    # STRATEGY 3: API Key (Last Resort / Local Dev bypass)
    # ---------------------------------------------------------
    if not model:
        api_key = get_config_value("GOOGLE_API_KEY", secrets_dict=secrets_dict)
        if api_key:
            try:
                from google import genai
                client = genai.Client(api_key=api_key)
                # Use Adapter
                model = GoogleGenAIAdapter(client, model_id, system_instruction=system_instruction)
                diagnostics["auth_method"] = "api_key"
            except Exception as e:
                diagnostics["error"] = f"API Key init failed: {e}"
        else:
            if diagnostics["auth_method"] == "none":
                diagnostics["error"] = "No valid auth method found (SA, ADC, or API Key)."

    # ---------------------------------------------------------
    # FINAL: Mandatory Preflight Check
    # ---------------------------------------------------------
    if model:
        try:
            print(f"Running preflight check for {model_id}...")
            # Simple ping to verify access and model existence
            model.generate_content("ping") 
        except Exception as e:
            error_str = str(e)
            
            # 1. Check for Retired/Invalid Model (404)
            if "404" in error_str or "Publisher Model" in error_str and "not found" in error_str:
                diagnostics["error"] = (
                    f"Model '{model_id}' is retired or invalid. "
                    f"Set GEMINI_MODEL_ID to 'gemini-2.5-flash' or 'gemini-2.0-flash-lite'."
                )
                return None, diagnostics
            
            # 2. Check for Permissions
            if "PermissionDenied" in error_str or "403" in error_str:
                diagnostics["error"] = (
                    "Credentials lack permissions. "
                    "Ensure 'Vertex AI User' role is assigned and Vertex AI API is enabled."
                )
                return None, diagnostics
            
            # 3. Check for Missing ADC/Creds
            if "default credentials" in error_str.lower():
                diagnostics["error"] = (
                    "ADC missing. Set GOOGLE_APPLICATION_CREDENTIALS or use a Service Account."
                )
                return None, diagnostics

            # Generic error
            diagnostics["error"] = f"Preflight check failed: {error_str}"
            return None, diagnostics

    return model, diagnostics

def get_bq_client(project_id=None):
    """
    Returns an authenticated BigQuery client.
    Uses the same auth strategy as Vertex AI (ADC or Service Account).
    """
    # Try to load credentials from secrets if available
    creds = None
    
    # 1. Service Account from secrets
    try:
        if st.secrets.get("gcp_service_account"):
             service_account_info = st.secrets["gcp_service_account"]
             creds = service_account.Credentials.from_service_account_info(service_account_info)
    except FileNotFoundError:
        pass
        
    # 2. Local secrets fallback
    if not creds:
        try:
             local_secrets = toml.load(".streamlit/secrets.toml")
             if "gcp_service_account" in local_secrets:
                 service_account_info = local_secrets["gcp_service_account"]
                 creds = service_account.Credentials.from_service_account_info(service_account_info)
        except Exception:
            pass

    # Initialize client
    try:
        if creds:
            return bigquery.Client(credentials=creds, project=project_id)
        else:
            # Fallback to ADC
            return bigquery.Client(project=project_id)
    except Exception as e:
        print(f"Failed to init BigQuery client: {e}")
        return None
