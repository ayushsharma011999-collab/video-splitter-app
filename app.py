import os
import shutil
import subprocess
import zipfile
import requests
import streamlit as st
import re
from datetime import datetime
import json
import time
import msal
from urllib.parse import urlparse, parse_qs

# =========================
# CONFIG & UI STYLING
# =========================

st.set_page_config(page_title="Pro Video Studio | Dashboard", page_icon="🎬", layout="wide")

st.markdown("""
    <style>
    .main { background-color: #0e1117; }
    .dashboard-card {
        background-color: #161b22;
        border: 1px solid #30363d;
        padding: 20px;
        border-radius: 12px;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
        margin-bottom: 20px;
    }
    .metric-value {
        font-size: 24px;
        font-weight: bold;
        color: #58a6ff;
    }
    </style>
""", unsafe_allow_html=True)

FFMPEG = shutil.which("ffmpeg")

if not FFMPEG:
    st.error("⚠️ FFmpeg install nahi hua. Please system check karein.")
    st.stop()

# Session States Initialize
if "input_file" not in st.session_state: st.session_state.input_file = None
if "clips" not in st.session_state: st.session_state.clips = []
if "duration" not in st.session_state: st.session_state.duration = 0
if "onedrive_access_token" not in st.session_state: st.session_state.onedrive_access_token = None
if "onedrive_refresh_token" not in st.session_state: st.session_state.onedrive_refresh_token = None

# =========================
# ONEDRIVE FUNCTIONS
# =========================
SCOPES = ["Files.ReadWrite.All", "offline_access"]
REDIRECT_URI = "http://localhost"

def get_msal_app(client_id, tenant_id, client_secret):
    authority = f"https://login.microsoftonline.com/{tenant_id}"
    return msal.ConfidentialClientApplication(client_id, authority=authority, client_credential=client_secret)

def upload_large_file_to_onedrive(file_path, access_token, folder_name="Pending_Posts"):
    file_name = os.path.basename(file_path)
    # 1. Create Folder (if not exists)
    # (Microsoft Graph automatically handles creating folders in path with Upload Session, but let's be direct)
    
    # 2. Create Upload Session
    session_url = f"https://graph.microsoft.com/v1.0/me/drive/root:/{folder_name}/{file_name}:/createUploadSession"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    res = requests.post(session_url, headers=headers)
    
    if res.status_code != 200:
        return False, f"Upload session failed: {res.text}"
    
    upload_url = res.json().get("uploadUrl")
    
    # 3. Upload File Bytes
    file_size = os.path.getsize(file_path)
    with open(file_path, 'rb') as f:
        upload_headers = {
            "Content-Length": str(file_size),
            "Content-Range": f"bytes 0-{file_size-1}/{file_size}"
        }
        upload_res = requests.put(upload_url, headers=upload_headers, data=f)
        
    if upload_res.status_code in [200, 201]:
        return True, "Upload Successful!"
    else:
        return False, f"Upload failed: {upload_res.text}"


# =========================
# VIDEO FUNCTIONS
# =========================
def get_video_duration(video_path):
    cmd = [FFMPEG, "-i", video_path]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    match = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", result.stderr)
    if not match: return 0
    return int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))

def split_video(video_path, output_dir, clip_duration=60, aspect_ratio="9:16", watermark_text=""):
    os.makedirs(output_dir, exist_ok=True)
    duration = get_video_duration(video_path)
    total_clips = int(duration // clip_duration) + (1 if duration % clip_duration > 0 else 0)
    clips = []
    
    vf_scale = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"
    if "16:9" in aspect_ratio: vf_scale = "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080"
    elif "1:1" in aspect_ratio: vf_scale = "scale=1080:1080:force_original_aspect_ratio=increase,crop=1080:1080"

    safe_watermark = watermark_text.replace("'", "").replace(":", "") if watermark_text else ""
    progress_bar = st.progress(0)
    
    for i in range(total_clips):
        start = i * clip_duration
        output_file = os.path.join(output_dir, f"Reel_Part_{i + 1}.mp4")
        part_text = f"Part {i+1}/{total_clips}"
        
        overlay_filters = [f"drawtext=text='{part_text}':fontcolor=white:fontsize=60:box=1:boxcolor=black@0.6:boxborderw=10:x=(w-text_w)/2:y=50"]
        if safe_watermark:
            overlay_filters.append(f"drawtext=text='{safe_watermark}':fontcolor=white:fontsize=48:box=1:boxcolor=black@0.5:boxborderw=10:x=w-tw-50:y=h-th-50")
            
        final_vf = vf_scale + "," + ",".join(overlay_filters)

        cmd = [FFMPEG, "-y", "-ss", str(start), "-i", video_path, "-t", str(clip_duration), "-vf", final_vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", output_file]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        if os.path.exists(output_file): clips.append(output_file)
        progress_bar.progress((i + 1) / total_clips)
        
    return clips


# =========================
# DASHBOARD LAYOUT (UI)
# =========================

st.markdown("""
    <div style="padding: 10px 0; border-bottom: 1px solid #30363d; margin-bottom: 25px;">
        <h1 style="color: #c9d1d9; margin: 0; font-size: 28px;">🎬 Pro Video Studio (Hybrid OneDrive Edition)</h1>
    </div>
""", unsafe_allow_html=True)

with st.sidebar:
    st.markdown("### ☁️ Step 1: Azure Credentials")
    client_id = st.text_input("Azure Client ID", type="password")
    tenant_id = st.text_input("Azure Tenant ID", type="password")
    client_secret = st.text_input("Azure Client Secret", type="password")
    
    st.markdown("---")
    st.markdown("### 📘 Step 2: Facebook Settings")
    fb_page_id = st.text_input("Facebook Page ID")
    fb_access_token = st.text_input("FB Page Access Token", type="password")
    fb_post_type = st.selectbox("Select Facebook Post Type", ["Facebook Reel (Short)", "Normal Page Video Post"])
    default_caption = st.text_area("Default Caption", value="Check out this amazing clip! 🔥")

    st.markdown("---")
    st.markdown("### 🎛️ Step 3: Video Parameters")
    uploaded_file = st.file_uploader("📁 Upload Video File", type=["mp4", "mov"])
    clip_duration = st.slider("Clip Duration (Sec)", 15, 120, 60, 15)
    aspect_ratio = st.selectbox("Format", ["9:16 (Vertical / Reels)", "16:9 (YouTube)", "1:1 (Square)"])
    watermark_text = st.text_input("🏷️ Watermark", placeholder="@Channel")


# ---------------------------------------------
# ONEDRIVE AUTHENTICATION WORKSPACE
# ---------------------------------------------
if client_id and tenant_id and client_secret:
    if not st.session_state.onedrive_refresh_token:
        st.warning("⚠️ OneDrive Connect nahi hai. Please niche diye gaye step se login karein.")
        msal_app = get_msal_app(client_id, tenant_id, client_secret)
        auth_url = msal_app.get_authorization_request_url(SCOPES, redirect_uri=REDIRECT_URI)
        
        st.markdown(f"**[🔗 Click Here to Login to Microsoft/OneDrive]({auth_url})**")
        st.write("Login karne ke baad URL ek error page jaisa dikhega (`http://localhost/?code=...`). Uss poore URL ko copy karein aur niche paste karein:")
        
        redirected_url = st.text_input("Paste URL here:")
        if st.button("🔐 Verify & Connect"):
            try:
                parsed_url = urlparse(redirected_url)
                code = parse_qs(parsed_url.query)['code'][0]
                result = msal_app.acquire_token_by_authorization_code(code, scopes=SCOPES, redirect_uri=REDIRECT_URI)
                
                if "access_token" in result:
                    st.session_state.onedrive_access_token = result["access_token"]
                    st.session_state.onedrive_refresh_token = result["refresh_token"]
                    st.success("✅ OneDrive Successfully Connected!")
                    st.rerun()
                else:
                    st.error("❌ Authentication Failed: " + result.get("error_description", "Unknown error"))
            except Exception as e:
                st.error("❌ Valid URL nahi hai. Please sahi URL paste karein.")
    else:
        st.success("✅ OneDrive Connected & Ready!")


# ---------------------------------------------
# VIDEO PROCESSING WORKSPACE
# ---------------------------------------------
if uploaded_file is not None:
    temp_input_path = os.path.join("/tmp", uploaded_file.name)
    if st.session_state.input_file != temp_input_path:
        with open(temp_input_path, "wb") as f: f.write(uploaded_file.getbuffer())
        st.session_state.input_file = temp_input_path
        st.session_state.duration = get_video_duration(temp_input_path)
        st.session_state.clips = []

if st.session_state.input_file and st.session_state.onedrive_refresh_token:
    st.markdown("---")
    st.markdown("### 🚀 Render Workspace")
    
    if st.button("⚡ Start Splitting Video", type="primary"):
        output_dir = "/tmp/reels"
        if os.path.exists(output_dir): shutil.rmtree(output_dir)
        os.makedirs(output_dir)

        with st.spinner("Rendering clips via FFmpeg..."):
            st.session_state.clips = split_video(st.session_state.input_file, output_dir, clip_duration, aspect_ratio, watermark_text)
            st.success("✨ Processing complete!")

    if st.session_state.clips:
        st.markdown(f"**Total Clips Generated:** {len(st.session_state.clips)}")
        
        # FINAL BUTTON TO UPLOAD TO ONEDRIVE
        if st.button("☁️ Upload to OneDrive (Send to Background Automation)", type="primary"):
            if not fb_page_id or not fb_access_token:
                st.error("⚠️ Sidebar me Facebook Details daalna zaroori hai!")
            else:
                progress = st.progress(0)
                status = st.empty()
                total = len(st.session_state.clips) + 1 # +1 for metadata
                
                # 1. Upload Videos
                for idx, clip in enumerate(st.session_state.clips):
                    status.text(f"Uploading {os.path.basename(clip)} to OneDrive...")
                    success, msg = upload_large_file_to_onedrive(clip, st.session_state.onedrive_access_token)
                    if not success:
                        st.error(f"Error in {os.path.basename(clip)}: {msg}")
                    progress.progress((idx + 1) / total)

                # 2. Generate & Upload Metadata
                status.text("Uploading Facebook Metadata Config...")
                meta_path = "/tmp/metadata.json"
                metadata = {
                    "fb_page_id": fb_page_id,
                    "fb_access_token": fb_access_token,
                    "caption": default_caption,
                    "post_type": fb_post_type,
                    "azure_client_id": client_id,
                    "azure_tenant_id": tenant_id,
                    "azure_client_secret": client_secret,
                    "azure_refresh_token": st.session_state.onedrive_refresh_token
                }
                with open(meta_path, "w") as f:
                    json.dump(metadata, f, indent=4)
                    
                upload_large_file_to_onedrive(meta_path, st.session_state.onedrive_access_token)
                progress.progress(1.0)
                
                status.empty()
                st.success("🎉 Safaltapoorvak Upload Ho Gaya! Ab aap Streamlit Dashboard band kar sakte hain. GitHub Actions baki ka kaam background me sambhal lega.")
