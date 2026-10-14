import os
from pathlib import Path

import streamlit as st

from app_access import password_matches
from pwa_assets import ensure_pwa_icons
from splitter_ui import render_splitter


st.set_page_config(
    page_title="Video Splitter",
    page_icon="🎬",
    layout="centered",
    initial_sidebar_state="collapsed",
)


def require_app_access():
    expected_password = os.environ.get("APP_ACCESS_PASSWORD")
    production = os.environ.get("APP_ENV") == "production"

    if production and not expected_password:
        st.error(
            "App access is not configured. Set APP_ACCESS_PASSWORD on the "
            "hosting service before opening the app."
        )
        st.stop()

    if expected_password and not st.session_state.get("authenticated", False):
        st.title("Sign in to Video Splitter")
        with st.form("app_access_form"):
            password = st.text_input("Access password", type="password")
            submitted = st.form_submit_button("Continue")

        if submitted:
            if password_matches(password, expected_password):
                st.session_state["authenticated"] = True
                st.rerun()
            st.error("Incorrect access password.")
        st.stop()


require_app_access()
ensure_pwa_icons(Path(__file__).parent / "web" / "icons")

if os.environ.get("APP_ACCESS_PASSWORD"):
    if st.sidebar.button("Sign out", icon=":material/logout:"):
        st.session_state["authenticated"] = False
        st.rerun()

render_splitter()
