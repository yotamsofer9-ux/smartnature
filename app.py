
# Requirements:
# streamlit
# streamlit-folium
# folium
# openmeteo-requests
# requests-cache
# retry_requests
# google-genai
# google-cloud-aiplatform
# pandas
# tornado==6.4.2
# watchdog

import streamlit as st
# import sqlite3 - Removed
import folium
from streamlit_folium import st_folium
import openmeteo_requests
import requests_cache
import pandas as pd
from retry_requests import retry
import os
from google.api_core.exceptions import NotFound, PermissionDenied
from datetime import datetime
import json
import random
from google.cloud import bigquery


from auth_manager import configure_gcp_auth, get_bq_client
from assets.knowledge_base import KNOWLEDGE_BASE

# --- Configuration & Setup ---
st.set_page_config(page_title="Smart Nature POC", page_icon="SmartNatureLogo.jpeg", layout="wide")

# Custom CSS for Nature Theme (Greens, Earth Tones)
st.markdown("""
<style>
/* Main Background - Soft Sage/Off-white Gradient */
.stApp {
    background: linear-gradient(to bottom right, #F1F8E9, #FFFFFF);
}

/* Sidebar styling - Dark text for contrast or light background */
[data-testid="stSidebar"] {
    background-color: #F9FBE7; /* Very light lime/green tint */
    border-right: 1px solid #C5E1A5;
}

/* Titles and Headers */
h1, h2, h3, h4, h5, h6, .stMarkdown {
    color: #2E7D32; /* Dark Green */
}

/* Buttons - Strong Forest Green */
div.stButton > button {
    background-color: #4CAF50;
    color: white;
    border: none;
    border-radius: 8px;
    font-weight: bold;
    transition: all 0.3s ease;
}
div.stButton > button:hover {
    background-color: #388E3C; /* Darker green on hover */
    color: white;
    box-shadow: 0 4px 6px rgba(0,0,0,0.1);
}

/* Input fields focus border */
div[data-baseweb="input"] > div {
    border-color: #4CAF50;
}

/* Tabs - Make them visible and green */
[data-testid="stTabs"] button {
    color: #2E7D32; /* Dark Green text for unselected tabs */
    font-weight: 500;
}
[data-testid="stTabs"] button[aria-selected="true"] {
    color: #1B5E20; /* Darker Green for selected tab */
    border-top-color: #4CAF50;
    font-weight: bold;
}
</style>
""", unsafe_allow_html=True)

# Check for GCP Credentials
# We rely on streamlit.secrets being available automatically in this context
PROJECT_ID = st.secrets.get("GCP_PROJECT_ID") # For display purposes if needed, though auth_manager handles it

# Construct System Instruction
knowledge_base_str = "\n".join([f"Q: {item['q']}\nA: {item['a']}" for item in KNOWLEDGE_BASE])

SYSTEM_INSTRUCTION = f"""
You are an expert Israeli nature guide.
Use the provided Knowledge Base to match user intent to specific locations and safety rules.

### Safety & Logistics Rules:
1. **Vehicle Compatibility**: 
   - "Private Car" cannot enter "4x4 Only" trails (e.g., Ramon Crater, Nahal Neqarot). 
   - "4x4 Only" trails require a "4x4" vehicle.
   - If a user has a "Private Car" and asks for a "4x4 Only" trail, you MUST warn them and suggest an alternative.
2. **Reserve Status**:
   - Check the provided Context for Real-Time Status.
   - If a reserve is "Closed" or "Red", DO NOT recommend it. Warn the user.

### Knowledge Base (Few-Shot Examples):
{knowledge_base_str}
"""

# Run Authentication Phase
model, auth_diagnostics = configure_gcp_auth(system_instruction=SYSTEM_INSTRUCTION)

if not model:
    USE_MOCK_AI = True
    st.sidebar.warning("⚠️ Running in AI Mock Mode")
    st.sidebar.error(f"Auth Failed: {auth_diagnostics['error']}")
    st.sidebar.info("Configure secrets.toml to enable Real AI.")
else:
    USE_MOCK_AI = False
    st.sidebar.success(f"✅ Real AI Active")

# --- Database Setup ---
# BigQuery Setup
auth_diagnostics = configure_gcp_auth()[1] # Re-fetch to get diagnostics if needed, or just use what we have below
PROJECT_ID = auth_diagnostics["project_id"]

if not PROJECT_ID or PROJECT_ID == "Unknown":
    st.error("GCP_PROJECT_ID is missing in configuration.")
    st.stop()

# Initialize BigQuery Client
bq_client = get_bq_client(project_id=PROJECT_ID)

if not bq_client:
    st.warning("⚠️ BigQuery Client failed to initialize. Database features will error.")

# --- Weather API ---
def get_weather(lat, lon):
    # Setup the Open-Meteo API client with cache and retry on error
    cache_session = requests_cache.CachedSession('.cache', expire_after=3600)
    retry_session = retry(cache_session, retries=5, backoff_factor=0.2)
    openmeteo = openmeteo_requests.Client(session=retry_session)

    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": ["temperature_2m", "weather_code"]
    }
    
    try:
        responses = openmeteo.weather_api(url, params=params)
        response = responses[0]
        current = response.Current()
        temp = current.Variables(0).Value()
        code = current.Variables(1).Value()
        return f"{temp:.1f}°C, Code: {int(code)}"
    except Exception as e:
        return "Weather unavailable"

# --- AI Logic ---
def analyze_hazard(image, text, use_mock):
    if use_mock:
        statuses = ['Green', 'Yellow', 'Red']
        status = random.choice(statuses)
        return {
            "severity": status,
            "suggested_status": status,
            "short_description": f"Mock Analysis: {text[:20]}... - {status} condition detected."
        }
    else:
        from vertexai.generative_models import Part
        from google.api_core.exceptions import NotFound, PermissionDenied
        # Real AI Logic using Vertex AI
        try:
            # Construct Multimodal Prompt
            input_parts = []
            
            prompt_text = f"""
            You are a hazard analysis AI. Analyze the provided report (text and optional image).
            User Description: {text}
            
            Determine:
            1. Severity: Green (Safe), Yellow (Caution), or Red (Danger).
            2. Suggested Status: Same values.
            3. Short Description: A concise summary in Hebrew (max 10 words).
            
            Output ONLY valid JSON.
            Example:
            {{
                "severity": "Red",
                "suggested_status": "Red",
                "short_description": "שיטפון במסלול, סכנה חמורה"
            }}
            """
            input_parts.append(prompt_text)

            if image:
                # Convert Streamlit UploadedFile to Vertex AI Part
                image_bytes = image.getvalue()
                image_part = Part.from_data(data=image_bytes, mime_type=image.type)
                input_parts.append(image_part)
            
            # Generate Content
            response = model.generate_content(input_parts)
            
            # Robust Parsing
            raw_text = response.text.strip()
            # Clean up potential markdown formatting
            if raw_text.startswith("```json"):
                raw_text = raw_text[7:]
            elif raw_text.startswith("```"):
                raw_text = raw_text[3:]
            if raw_text.endswith("```"):
                raw_text = raw_text[:-3]
            
            analysis_dict = json.loads(raw_text.strip())
            
            # Ensure keys exist
            required_keys = ["severity", "suggested_status", "short_description"]
            for key in required_keys:
                if key not in analysis_dict:
                    analysis_dict[key] = "Unknown"
            
            return analysis_dict

        except json.JSONDecodeError:
            return {
                "severity": "Yellow",
                "suggested_status": "Yellow",
                "short_description": "שגיאה בפענוח תשובת ה-AI"
            }
        except NotFound:
             return {
                "severity": "Unknown",
                "suggested_status": "Yellow",
                "short_description": "Error: Vertex AI API not enabled or Billing off."
            }
        except PermissionDenied:
             return {
                "severity": "Unknown",
                "suggested_status": "Yellow",
                "short_description": "Error: Service Account missing 'Vertex AI User' role."
            }
        except Exception as e:
             return {
                "severity": "Red",
                "suggested_status": "Red",
                "short_description": f"AI Error: {str(e)[:50]}"
            }

def get_agent_response(user_input, context, use_mock, history=None):
    if use_mock:
        return f"Mock Agent: I see you are in a {context.get('vehicle', 'User')}. {context.get('reserve_name', 'This place')} is currently {context.get('reserve_status', 'Unknown')}."
    else:
        try:
            # Build history string
            history_str = ""
            if history is not None and not history.empty:
                # Take last 10 messages for context
                recent_history = history.tail(10)
                for _, row in recent_history.iterrows():
                    role_label = "Model" if row['role'] == 'assistant' else "User"
                    history_str += f"{role_label}: {row['content']}\n"

            prompt = f"""
            [Current Context]:
            User Vehicle: {context.get('vehicle', 'Unknown')}
            User Skill Level: {context.get('skill', 'Unknown')}
            Real-Time Reserve Statuses: {context.get('all_reserves_status', 'Not Available')}

            [Previous Conversation]:
            {history_str}

            [User Question]:
            {user_input}
            """
            response = model.generate_content(prompt)
            return response.text
        except Exception as e:
            return f"Agent Error: {e}"

# --- Sidebar ---
st.sidebar.image("SmartNatureLogo.jpeg", width=150)
st.sidebar.title("Smart Nature")
if USE_MOCK_AI:
    st.sidebar.warning("⚠️ Running in AI Mock Mode")
    st.sidebar.info("To enable Real AI, ensure GCP_PROJECT_ID is set and 'gcp_service_account' is configured in secrets.toml.")
else:
    st.sidebar.success("✅ Online")
    with st.sidebar.expander("Connection Details"):
        st.write(f"**Method:** {auth_diagnostics['auth_method']}")
        st.write(f"**Project:** {auth_diagnostics['project_id']}")
        st.write(f"**Location:** {auth_diagnostics['location']}")
        st.write(f"**Model:** {auth_diagnostics['model_id']}")

# User Selection
try:
    df_users = bq_client.query(f"SELECT * FROM `{PROJECT_ID}.smart_nature_DB.users`").to_dataframe()
except Exception as e:
    st.error(f"Error loading users: {e}")
    df_users = pd.DataFrame() # Empty fallback

if not df_users.empty:
    user_names = [f"{row['username']} ({row['vehicle_type']})" for index, row in df_users.iterrows()]
    selected_user_str = st.sidebar.selectbox("Login as:", user_names)
    selected_username = selected_user_str.split(" (")[0]
    current_user = df_users[df_users['username'] == selected_username].iloc[0]
else:
    st.sidebar.error("No users found.")

# --- Main App ---
tab1, tab2, tab3 = st.tabs(["🗺️ Live Map", "📢 Smart Reporter", "🤖 AI Agent"])

# --- Tab 1: Live Map ---
with tab1:
    st.subheader("Reserve Status Dashboard")
    
    # Load reserves from BigQuery
    try:
        df_reserves = bq_client.query(f"SELECT * FROM `{PROJECT_ID}.smart_nature_DB.reserves`").to_dataframe()
    except Exception as e:
        st.error(f"Error loading reserves: {e}")
        df_reserves = pd.DataFrame()

    
    if not df_reserves.empty:
        # Calculate center for map (avg of seed locations)
        start_lat = df_reserves['lat'].mean()
        start_lon = df_reserves['lon'].mean()
        
        m = folium.Map(location=[start_lat, start_lon], zoom_start=8)
        
        status_colors = {'Green': 'green', 'Yellow': 'orange', 'Red': 'red'}
        
        for index, row in df_reserves.iterrows():
            weather = get_weather(row['lat'], row['lon'])
            
            popup_html = f"""
            <b>{row['name']}</b><br>
            Status: {row['status']}<br>
            Weather: {weather}<br>
            <i>{row['description']}</i>
            """
            
            folium.Marker(
                [row['lat'], row['lon']],
                popup=folium.Popup(popup_html, max_width=300),
                tooltip=row['name'],
                icon=folium.Icon(color=status_colors.get(row['status'], 'gray'), icon='info-sign')
            ).add_to(m)
            
        st_folium(m, width=700, height=500)
    else:
        st.info("No reserves in database.")

# --- Tab 2: Smart Reporter ---
with tab2:
    st.subheader("Report a Hazard")
    
    if not df_reserves.empty:
        selected_reserve_name = st.selectbox("Select Reserve", df_reserves['name'].tolist())
        selected_reserve = df_reserves[df_reserves['name'] == selected_reserve_name].iloc[0]
        
        report_text = st.text_area("Describe the situation...")
        uploaded_image = st.file_uploader("Upload Image (Optional)", type=['png', 'jpg', 'jpeg'])
        
        if st.button("Submit Report"):
            if not report_text and not uploaded_image:
                st.error("Please provide text or an image.")
            else:
                with st.spinner("Processing Report..."):
                    # Process AI
                    analysis = analyze_hazard(uploaded_image, report_text, USE_MOCK_AI)
                    
                    # Save to DB
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    
                    # Use BQ Parameterized Query for Report
                    report_query = f"""
                        INSERT INTO `{PROJECT_ID}.smart_nature_DB.reports` 
                        (reserve_id, timestamp, user_text, ai_analysis, image_path)
                        VALUES (@reserve_id, @timestamp, @user_text, PARSE_JSON(@ai_analysis), @image_path)
                    """
                    report_params = [
                        bigquery.ScalarQueryParameter("reserve_id", "INT64", int(selected_reserve['id'])),
                        bigquery.ScalarQueryParameter("timestamp", "STRING", timestamp),
                        bigquery.ScalarQueryParameter("user_text", "STRING", report_text),
                        bigquery.ScalarQueryParameter("ai_analysis", "STRING", json.dumps(analysis)),
                        bigquery.ScalarQueryParameter("image_path", "STRING", uploaded_image.name if uploaded_image else None)
                    ]
                    
                    try:
                        job_config = bigquery.QueryJobConfig(query_parameters=report_params)
                        bq_client.query(report_query, job_config=job_config).result()
                        
                        # Update Reserve Status
                        new_status = analysis.get('suggested_status', selected_reserve['status'])
                        new_desc = analysis.get('short_description', selected_reserve['description'])
                        
                        update_query = f"""
                            UPDATE `{PROJECT_ID}.smart_nature_DB.reserves`
                            SET status = @status, description = @description, last_update = @timestamp
                            WHERE id = @id
                        """
                        update_params = [
                            bigquery.ScalarQueryParameter("status", "STRING", new_status),
                            bigquery.ScalarQueryParameter("description", "STRING", new_desc),
                            bigquery.ScalarQueryParameter("timestamp", "STRING", timestamp),
                            bigquery.ScalarQueryParameter("id", "INT64", int(selected_reserve['id']))
                        ]
                        job_config_update = bigquery.QueryJobConfig(query_parameters=update_params)
                        bq_client.query(update_query, job_config=job_config_update).result()

                        st.success(f"Report Submitted! Status updated to {new_status}.")
                        st.json(analysis)
                        st.rerun() # Refresh to show new status
                    except Exception as e:
                        st.error(f"Failed to save report to BigQuery: {e}")


# --- Tab 3: AI Travel Agent ---
with tab3:
    st.subheader("AI Travel Agent")
    
    # Load chat history
    try:
        history = bq_client.query(f"SELECT * FROM `{PROJECT_ID}.smart_nature_DB.chat_history` ORDER BY timestamp ASC").to_dataframe()
    except Exception as e:
        # If table is empty or error, fallback to empty
        history = pd.DataFrame(columns=['role', 'content'])

    for index, row in history.iterrows():
        with st.chat_message(row['role']):
            st.write(row['content'])
            
    user_query = st.chat_input("Ask about reserves or routes...")
    
    if user_query:
        # Display user message
        with st.chat_message("user"):
            st.write(user_query)
        
        # Save user message
        # Save user message
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            chat_query = f"""
                INSERT INTO `{PROJECT_ID}.smart_nature_DB.chat_history` (timestamp, role, content)
                VALUES (@timestamp, @role, @content)
            """
            chat_params = [
                bigquery.ScalarQueryParameter("timestamp", "STRING", timestamp),
                bigquery.ScalarQueryParameter("role", "STRING", "user"),
                bigquery.ScalarQueryParameter("content", "STRING", user_query)
            ]
            job_config = bigquery.QueryJobConfig(query_parameters=chat_params)
            bq_client.query(chat_query, job_config=job_config).result()
        except Exception as e:
             st.error(f"Failed to save chat: {e}")

        
        # Get context
        # Ideally we'd do a geo-lookup or use selected reserve, falling back to 'General'
        context = {
            "vehicle": current_user['vehicle_type'],
            "skill": current_user['skill_level'],
            "all_reserves_status": df_reserves[['name', 'status']].to_dict('records') # Pass all statuses for context
        }
        
        response = get_agent_response(user_query, context, USE_MOCK_AI, history)
        
        with st.chat_message("assistant"):
            st.write(response)
            
        with st.chat_message("assistant"):
            st.write(response)
            
        try:
            chat_params_ai = [
                bigquery.ScalarQueryParameter("timestamp", "STRING", timestamp),
                bigquery.ScalarQueryParameter("role", "STRING", "assistant"),
                bigquery.ScalarQueryParameter("content", "STRING", response)
            ]
            job_config_ai = bigquery.QueryJobConfig(query_parameters=chat_params_ai)
            bq_client.query(chat_query, job_config=job_config_ai).result()
        except Exception as e:
             st.error(f"Failed to save assistant response: {e}")

