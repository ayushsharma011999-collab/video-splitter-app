import os
import shutil
import subprocess
import zipfile
import requests
import streamlit as st
import re
from datetime import datetime, timedelta
import json
import time
import threading
from streamlit.runtime.scriptrunner import add_script_run_ctx  # Added for thread safety

# =========================
# CONFIG & UI STYLING
# =========================

st.set_page_config(
    page_title="Pro Video Studio | Dashboard",
    page_icon="🎬",
    layout="wide"
)

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
    .stButton button {
        width: 100%;
        border-radius: 8px;
        font-weight: 600;
        padding: 0.5rem 1rem;
    }
    .log-box {
        background-color: #0d1117;
        border: 1px solid #30363d;
        padding: 12px;
        border-radius: 8px;
        font-family: monospace;
        font-size: 13px;
        color: #7ee787;
        max-height: 250px;
        overflow-y: auto;
    }
    </style>
""", unsafe_allow_html=True)

FFMPEG = shutil.which("ffmpeg")

if not FFMPEG:
    st.error("⚠️ FFmpeg install nahi hua. Please packages.txt check karein.")
    st.stop()

# Initialize Session State & History Logs
if "input_file" not in st.session_state:
    st.session_state.input_file = None
if "clips" not in st.session_state:
    st.session_state.clips = []
if "duration" not in st.session_state:
    st.session_state.duration = 0
if "activity_logs" not in st.session_state:
    st.session_state.activity_logs = []
if "queue_status" not in st.session_state:
    st.session_state.queue_status = "Idle"

def add_log(message):
    timestamp = datetime.now().strftime("%H:%M:%S")
    st.session_state.activity_logs.insert(0, f"[{timestamp}] {message}")


# =========================
# FUNCTIONS
# =========================

def get_video_duration(video_path):
    cmd = [FFMPEG, "-i", video_path]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    output = result.stderr

    match = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", output)
    if not match:
        return 0

    hours = int(match.group(1))
    minutes = int(match.group(2))
    seconds = float(match.group(3))

    return hours * 3600 + minutes * 60 + seconds


def split_video(video_path, output_dir, clip_duration=60, aspect_ratio="9:16", watermark_text=""):
    os.makedirs(output_dir, exist_ok=True)
    duration = get_video_duration(video_path)

    if duration <= 0:
        raise Exception("Video duration read nahi ho paayi.")

    total_clips = int(duration // clip_duration)
    if duration % clip_duration > 0:
        total_clips += 1

    clips = []
    
    if "9:16" in aspect_ratio:
        vf_scale = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"
    elif "16:9" in aspect_ratio:
        vf_scale = "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080"
    elif "1:1" in aspect_ratio:
        vf_scale = "scale=1080:1080:force_original_aspect_ratio=increase,crop=1080:1080"
    else:
        vf_scale = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"

    safe_watermark = watermark_text.replace("'", "").replace(":", "") if watermark_text else ""

    progress_bar = st.progress(0)
    status_text = st.empty()

    add_log(f"Started splitting video into {total_clips} clips ({clip_duration}s each)...")

    for i in range(total_clips):
        start = i * clip_duration
        output_file = os.path.join(output_dir, f"Reel_Part_{i + 1}.mp4")

        # FIX: Moved watermark & text filter generation inside the loop
        part_text = f"Part {i+1}/{total_clips}"

        overlay_filters = [
            f"drawtext=text='{part_text}':fontcolor=white:fontsize=60:"
            f"box=1:boxcolor=black@0.6:boxborderw=10:"
            f"x=(w-text_w)/2:y=50"
        ]

        if safe_watermark:
            overlay_filters.append(
                f"drawtext=text='{safe_watermark}':"
                f"fontcolor=white:fontsize=48:"
                f"box=1:boxcolor=black@0.5:boxborderw=10:"
                f"x=w-tw-50:y=h-th-50"
            )

        watermark_filter = "," + ",".join(overlay_filters)
        final_vf = vf_scale + watermark_filter

        cmd = [
            FFMPEG,
            "-y",
            "-ss", str(start),
            "-i", video_path,
            "-t", str(clip_duration),
            "-vf", final_vf,
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            output_file
        ]

        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        if result.returncode != 0:
            add_log(f"Error at clip {i+1}: FFmpeg failed.")
            raise Exception("FFmpeg error:\n\n" + result.stderr[-3000:])

        if os.path.exists(output_file):
            clips.append(output_file)

        progress_percentage = (i + 1) / total_clips
        progress_bar.progress(progress_percentage)
        status_text.text(f"⚡ Processing Clip {i + 1} of {total_clips}...")
        add_log(f"Successfully generated Reel_Part_{i + 1}.mp4")

    status_text.text("✨ Processing complete successfully!")
    add_log("Video splitting workflow completed successfully.")
    return clips


def create_zip(files, zip_name):
    with zipfile.ZipFile(zip_name, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for file in files:
            zip_file.write(file, os.path.basename(file))


def publish_to_facebook(video_path, page_id, access_token, caption, post_type="Facebook Reel (Short)"):
    try:
        if post_type == "Facebook Reel (Short)":
            add_log(f"Initiating Facebook Reel upload for {os.path.basename(video_path)}...")
            init_url = f"https://graph.facebook.com/v19.0/{page_id}/video_reels"
            init_payload = {"upload_phase": "start", "access_token": access_token}
            res = requests.post(init_url, data=init_payload)
            res_data = res.json()

            if "video_id" not in res_data or "upload_url" not in res_data:
                add_log(f"FB Reel Init Failed: {res_data}")
                return False, f"Reel Init Failed: {res_data}"

            video_id = res_data["video_id"]
            upload_url = res_data["upload_url"]
            file_size = os.path.getsize(video_path)

            with open(video_path, "rb") as video_file:
                headers = {"Authorization": f"OAuth {access_token}", "offset": "0", "file_size": str(file_size)}
                requests.post(upload_url, data=video_file, headers=headers)

            publish_url = f"https://graph.facebook.com/v19.0/{page_id}/video_reels"
            publish_payload = {
                "access_token": access_token,
                "video_id": video_id,
                "upload_phase": "finish",
                "video_state": "PUBLISHED",
                "description": caption
            }
            pub_res = requests.post(publish_url, data=publish_payload)
            pub_data = pub_res.json()

            if pub_data.get("success", False):
                add_log(f"Successfully published Reel: {os.path.basename(video_path)}")
                return True, "Facebook Reel successfully published! 🚀"
            else:
                add_log(f"FB Reel Publish Failed: {pub_data}")
                return False, f"Publish Error: {pub_data}"

        else:
            add_log(f"Initiating Normal Page Video upload for {os.path.basename(video_path)}...")
            upload_url = f"https://graph-video.facebook.com/v19.0/{page_id}/videos"
            
            with open(video_path, "rb") as video_file:
                files_payload = {"source": video_file}
                data_payload = {
                    "access_token": access_token,
                    "description": caption
                }
                res = requests.post(upload_url, data=data_payload, files=files_payload)
                res_data = res.json()

                if "id" in res_data:
                    add_log(f"Successfully published Video Post: {os.path.basename(video_path)}")
                    return True, "Normal Video post successfully published! 🚀"
                else:
                    add_log(f"FB Video Post Failed: {res_data}")
                    return False, f"Video Post Error: {res_data}"

    except Exception as e:
        add_log(f"FB Exception: {str(e)}")
        return False, str(e)


def background_hourly_poster(clips_list, page_id, access_token, caption, post_type):
    st.session_state.queue_status = "Running 🟢"
    add_log(f"Background hourly publishing queue started for {post_type}...")
    
    for idx, clip in enumerate(clips_list):
        add_log(f"Queue item {idx+1}/{len(clips_list)}: Waiting to publish {os.path.basename(clip)}...")
        
        if idx > 0:
            wait_time = 3600  # 1 hour
            elapsed = 0
            while elapsed < wait_time:
                time.sleep(60)
                elapsed += 60

        success, msg = publish_to_facebook(clip, page_id, access_token, f"{caption} (Part {idx+1})", post_type)
        if not success:
            add_log(f"Failed to auto-post {os.path.basename(clip)}: {msg}")
            
    st.session_state.queue_status = "Completed ✅"
    add_log("All clips in the hourly queue have been processed!")


# =========================
# DASHBOARD LAYOUT (UI)
# =========================

st.markdown("""
    <div style="padding: 10px 0; border-bottom: 1px solid #30363d; margin-bottom: 25px;">
        <h1 style="color: #c9d1d9; margin: 0; font-size: 28px;">🎬 Pro Video Studio & Facebook Publisher</h1>
        <p style="color: #8b949e; margin: 5px 0 0 0;">Split videos & choose to post either as Reels or Normal Videos to Facebook.</p>
    </div>
""", unsafe_allow_html=True)

# Sidebar Control Panel
with st.sidebar:
    st.markdown("### 🎛️ Control Panel")
    st.markdown("---")
    
    uploaded_file = st.file_uploader(
        "📁 Upload Video File",
        type=["mp4", "mov", "avi", "mkv"]
    )
    
    st.markdown("---")
    st.markdown("### ⚙️ Video Parameters")
    clip_duration = st.slider("Clip Duration (Sec)", 15, 120, 60, 15)
    aspect_ratio = st.selectbox("Format", ["9:16 (Vertical / Reels)", "16:9 (YouTube)", "1:1 (Square)"])
    watermark_text = st.text_input("🏷️ Watermark", placeholder="@Channel")

    st.markdown("---")
    st.markdown("### 📘 Facebook Settings")
    fb_page_id = st.text_input("Facebook Page ID", placeholder="e.g. 1092837465")
    fb_access_token = st.text_input("Page Access Token", type="password", placeholder="EAAG...")
    
    fb_post_type = st.selectbox(
        "Select Facebook Post Type",
        ["Facebook Reel (Short)", "Normal Page Video Post"]
    )
    
    default_caption = st.text_area("Default Caption", value="Check out this amazing clip! 🔥")


if uploaded_file is not None:
    temp_input_path = os.path.join("/tmp", uploaded_file.name)
    if st.session_state.input_file != temp_input_path:
        with open(temp_input_path, "wb") as f:
            f.write(uploaded_file.getbuffer())
        st.session_state.input_file = temp_input_path
        st.session_state.duration = get_video_duration(temp_input_path)
        st.session_state.clips = []
        add_log(f"New video uploaded: {uploaded_file.name} ({st.session_state.duration:.1f}s)")

# Main Workspace
if st.session_state.input_file and os.path.exists(st.session_state.input_file):
    
    col_m1, col_m2, col_m3 = st.columns(3)
    with col_m1:
        st.markdown(f"""
            <div class="dashboard-card" style="text-align: center;">
                <p style="color: #8b949e; margin:0;">File Name</p>
                <p class="metric-value" style="font-size: 15px; overflow: hidden;">{os.path.basename(st.session_state.input_file)}</p>
            </div>
        """, unsafe_allow_html=True)
    with col_m2:
        st.markdown(f"""
            <div class="dashboard-card" style="text-align: center;">
                <p style="color: #8b949e; margin:0;">Total Duration</p>
                <p class="metric-value">{st.session_state.duration:.1f}s</p>
            </div>
        """, unsafe_allow_html=True)
    with col_m3:
        est_clips = int(st.session_state.duration // clip_duration) + (1 if st.session_state.duration % clip_duration > 0 else 0)
        st.markdown(f"""
            <div class="dashboard-card" style="text-align: center;">
                <p style="color: #8b949e; margin:0;">Estimated Parts</p>
                <p class="metric-value">~ {est_clips} Clips</p>
            </div>
        """, unsafe_allow_html=True)

    col_workspace, col_history = st.columns([1.2, 0.8])

    with col_workspace:
        st.markdown("### 🚀 Render Workspace")
        col_btn1, col_btn2 = st.columns([2, 1])
        with col_btn1:
            start_process = st.button("⚡ Start Splitting & Processing", type="primary", use_container_width=True)
        with col_btn2:
            if st.button("🧹 Clear Workspace", use_container_width=True):
                st.session_state.clips = []
                st.session_state.input_file = None
                add_log("Workspace cleared by user.")
                st.rerun()

    with col_history:
        st.markdown(f"### 📊 Live Telemetry & Queue ({st.session_state.queue_status})")
        logs_html = "<br>".join(st.session_state.activity_logs[:10]) if st.session_state.activity_logs else "No activity yet."
        st.markdown(f'<div class="log-box">{logs_html}</div>', unsafe_allow_html=True)

    if start_process:
        output_dir = "/tmp/reels"
        if os.path.exists(output_dir):
            shutil.rmtree(output_dir)
        os.makedirs(output_dir)

        try:
            with st.spinner("Rendering clips via FFmpeg..."):
                st.session_state.clips = split_video(
                    st.session_state.input_file,
                    output_dir,
                    clip_duration,
                    aspect_ratio,
                    watermark_text
                )
        except Exception as e:
            st.error("❌ Processing failed!")
            st.code(str(e))

    # Output Gallery & Hourly Scheduler Queue Trigger
    if st.session_state.clips:
        st.markdown("---")
        st.markdown(f"### 📦 Generated Clips & Auto-Posting Hub ({fb_post_type})")
        
        if st.button(f"⏰ Start 1-Hour Interval Auto-Posting Queue ({fb_post_type})", type="primary", use_container_width=True):
            if not fb_page_id or not fb_access_token:
                st.error("⚠️ Please enter Facebook Page ID and Access Token in the sidebar first!")
            else:
                bg_thread = threading.Thread(
                    target=background_hourly_poster,
                    args=(st.session_state.clips, fb_page_id, fb_access_token, default_caption, fb_post_type),
                    daemon=True
                )
                add_script_run_ctx(bg_thread) # FIX: Makes Streamlit session access thread-safe
                bg_thread.start()
                st.success(f"✅ Hourly background queue started for {fb_post_type}!")

        st.markdown("<br>", unsafe_allow_html=True)

        clip_cols = st.columns(2)
        for idx, clip in enumerate(st.session_state.clips):
            with clip_cols[idx % 2]:
                with st.container():
                    st.markdown(f"""
                        <div class="dashboard-card">
                            <h4>🎞️ {os.path.basename(clip)}</h4>
                            <p style="color: #8b949e; font-size: 12px; margin: 0;">Mode: {fb_post_type}</p>
                        </div>
                    """, unsafe_allow_html=True)
                    st.video(clip)
                    
                    with open(clip, "rb") as f:
                        st.download_button(
                            label=f"⬇️ Download {os.path.basename(clip)}",
                            data=f.read(),
                            file_name=os.path.basename(clip),
                            mime="video/mp4",
                            key=f"dl_clip_{idx}"
                        )
                    
                    if st.button(f"🚀 Publish Instantly as {fb_post_type} (Part #{idx+1})", key=f"fb_post_{idx}", type="secondary"):
                        if not fb_page_id or not fb_access_token:
                            st.error("⚠️ Please enter Facebook Page ID and Access Token in the sidebar!")
                        else:
                            with st.spinner(f"Publishing to Facebook as {fb_post_type}..."):
                                success, msg = publish_to_facebook(clip, fb_page_id, fb_access_token, default_caption, fb_post_type)
                                if success:
                                    st.success(msg)
                                else:
                                    st.error(msg)
                    
                    st.markdown("<br>", unsafe_allow_html=True)

else:
    st.markdown("""
        <div style="text-align: center; padding: 60px 20px; background-color: #161b22; border: 1px dashed #30363d; border-radius: 12px;">
            <h3>📂 No Video Loaded Yet</h3>
            <p style="color: #8b949e;">Please use the sidebar to upload a video file to begin automated processing and scheduling.</p>
        </div>
    """, unsafe_allow_html=True)
