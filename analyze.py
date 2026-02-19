import streamlit as st
import json
import pandas as pd
from github import Github
import os

# --- 1. CONFIG (CENSOR ALERT) ---
# Your GitHub repository path
REPO_NAME = "your-github-username/starpets-tracker" 

st.set_page_config(page_title="Starpets Hunter", page_icon="🎯")
st.title("🎯 Starpets Target Manager")

# --- 2. THE KEY (CENSOR ALERT) ---
# This line looks for your secret token in Streamlit's hidden vault
if "GITHUB_TOKEN" not in st.secrets:
    st.error("Go to Streamlit Settings > Secrets and add your GITHUB_TOKEN!")
    st.stop()

token = st.secrets["GITHUB_TOKEN"]
g = Github(token)

try:
    repo = g.get_repo(REPO_NAME)
except Exception as e:
    st.error(f"Could not access repository '{REPO_NAME}'. Check REPO_NAME in analyze.py. Error: {e}")
    st.stop()

# --- 3. LOAD DATA ---
@st.cache_data(ttl=60)
def load_config():
    try:
        file = repo.get_contents("config.json")
        data = json.loads(file.decoded_content.decode())
        # The scraper expects: {"alerts": [{"pet_name": "...", "target_price": ...}, ...]}
        alerts = data.get("alerts", [])
        return alerts, file.sha
    except Exception as e:
        st.warning(f"Could not load config.json (maybe it doesn't exist yet?): {e}")
        return [], None

current_alerts, file_sha = load_config()

# --- 4. THE UI ---
st.write("Edit your hunt list below. The robot will check these every hour.")

# Convert the list of dicts to a DataFrame for easier editing
if current_alerts:
    df = pd.DataFrame(current_alerts)
    # Ensure columns are in the right order
    if "pet_name" in df.columns and "target_price" in df.columns:
        df = df[["pet_name", "target_price"]]
    else:
        df = pd.DataFrame(columns=["pet_name", "target_price"])
else:
    df = pd.DataFrame(columns=["pet_name", "target_price"])

# Rename columns for UI
df.columns = ["Pet Name", "Max Price (€)"]

edited_df = st.data_editor(df, num_rows="dynamic", use_container_width=True)

# --- 5. SAVE BUTTON ---
if st.button("🚀 Update Hunter Robot", type="primary"):
    # Convert the table back into the structure the scraper expects
    new_alerts_list = []
    for _, row in edited_df.iterrows():
        if row["Pet Name"] and not pd.isna(row["Max Price (€)"]):
            new_alerts_list.append({
                "pet_name": str(row["Pet Name"]),
                "target_price": float(row["Max Price (€)"])
            })
    
    new_config = {"alerts": new_alerts_list}
    
    try:
        # Push the change to GitHub
        update_msg = "Updated targets via Streamlit"
        content_json = json.dumps(new_config, indent=4)
        
        if file_sha:
            repo.update_file(
                path="config.json",
                message=update_msg,
                content=content_json,
                sha=file_sha
            )
        else:
            repo.create_file(
                path="config.json",
                message="Created config.json via Streamlit",
                content=content_json
            )
            
        st.success("GitHub updated! The robot will see these new targets on its next run.")
        st.balloons()
        # Clear cache so it reloads the new data
        st.cache_data.clear()
    except Exception as e:
        st.error(f"Failed to update GitHub: {e}")
