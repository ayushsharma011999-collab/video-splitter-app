import os
import shutil
import subprocess
import zipfile
import requests
import streamlit as st
import re

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
    </style>
""", unsafe_allow_html=True)

FFMPEG = shutil.which("ffmpeg")

if not FFMPEG:
    st.error("⚠️ FFmpeg install nahi hua. Please packages.txt check karein.")
    st.stop()

# Initialize Session State
if "input_file" not in st.session_state:
    st.session_state.input_file = None
if "clips" not in st.session_state:
    st.session_state.clips = []
if "duration" not in st.session_state:
    st.session_state.duration = 0


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

    if watermark_text.strip():
        safe_text = watermark_text.replace("'", "").replace(":", "")
        watermark_filter = f",drawtext=text='{safe_text}':fontcolor=white:fontsize=48:box=1:boxcolor=black@0.5:boxborderw=10:x=w-tw-50:y=h-th-50"
    else:
        watermark_filter = ""

    final_vf = vf_scale + watermark_filter

    progress_bar = st.progress(0)
    status_text = st.empty()

    for i in range(total_clips):
        start = i * clip_duration
        output_file = os.path.join(output_dir, f"Reel_Part_{i + 1}.mp4")

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
            raise Exception("FFmpeg error:\n\n" + result.stderr[-3000:])

        if os.path.exists(output_file):
            clips.append(output_file)

        progress_percentage = (i + 1) / total_clips
        progress_bar.progress(progress_percentage)
        status_text.text(f"⚡ Processing Clip {i + 1} of {total_clips}...")

    status_text.text("✨ Processing complete successfully!")
    return clips


def create_zip(files, zip_name):
    with zipfile.ZipFile(zip_name, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for file in files:
            zip_file.write(file, os.path.basename(file))


def publish_to_facebook_reel(video_path, page_id, access_token, caption):
    """
    Publishes a video file as a Facebook Reel using Meta Graph API.
    Step 1: Initialize upload session.
    Step 2: Upload binary video chunks.
    Step 3: Publish the reel.
    """
    try:
        # Step 1: Initialize upload session
        init_url = f"https://graph.facebook.com/v19.0/{page_id}/video_reels"
        init_payload = {
            "upload_phase": "start",
            "access_token": access_token
        }
        res = requests.post(init_url, data=init_payload)
        res_data = res.json()

        if "video_id" not in res_data or "upload_url" not in res_data:
            return False, f"Initialization Failed: {res_data}"

        video_id = res_data["video_id"]
        upload_url = res_data["upload_url"]
        file_size = os.path.getsize(video_path)

        # Step 2: Upload video binary data to rupload endpoint
        with open(video_path, "rb") as video_file:
            headers = {
                "Authorization": f"OAuth {access_token}",
                "offset": "0",
                "file_size": str(file_size)
            }
            upload_res = requests.post(upload_url, data=video_file, headers=headers)
            upload_data = upload_res.json()

            if not upload_data.get("success", False) and upload_res.status_code != 200:
                # Some successful responses return status 200 directly without explicit success flag
                pass

        # Step 3: Publish the Reel session
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
            return True, "Reel successfully published to Facebook Page! 🚀"
        else:
            return False, f"Publish Error: {pub_data}"

    except Exception as e:
        return False, str(e)


# =========================
# DASHBOARD LAYOUT (UI)
# =========================

st.markdown("""
    <div style="padding: 10px 0; border-bottom: 1px solid #30363d; margin-bottom: 25px;">
        <h1 style="color: #c9d1d9; margin: 0; font-size: 28px;">🎬 Pro Video Studio & FB Auto-Poster</h1>
        <p style="color: #8b949e; margin: 5px 0 0 0;">Split videos into shorts & publish directly to Facebook Pages automatically.</p>
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
    st.markdown("### 📘 Facebook Auto-Post Setup")
    fb_page_id = st.text_input("Facebook Page ID", placeholder="e.g. 1092837465")
    fb_access_token = st.text_input("Page Access Token", type="password", placeholder="EAAG...")
    default_caption = st.text_area("Default Reel Caption", value="Check out this amazing reel! 🔥 #Reels #Shorts")


# Handle file upload persistence
if uploaded_file is not None:
    temp_input_path = os.path.join("/tmp", uploaded_file.name)
    if st.session_state.input_file != temp_input_path:
        with open(temp_input_path, "wb") as f:
            f.write(uploaded_file.getbuffer())
        st.session_state.input_file = temp_input_path
        st.session_state.duration = get_video_duration(temp_input_path)
        st.session_state.clips = []

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

    st.markdown("### 🚀 Render Workspace")
    col_btn1, col_btn2 = st.columns([2, 1])
    with col_btn1:
        start_process = st.button("⚡ Start Splitting & Processing", type="primary", use_container_width=True)
    with col_btn2:
        if st.button("🧹 Clear Workspace", use_container_width=True):
            st.session_state.clips = []
            st.session_state.input_file = None
            st.rerun()

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

    # Output Gallery & FB Auto-Post Buttons
    if st.session_state.clips:
        st.markdown("---")
        st.markdown("### 📦 Generated Clips & Social Publishing Hub")
        
        zip_file = "/tmp/reels.zip"
        create_zip(st.session_state.clips, zip_file)
        if os.path.exists(zip_file):
            with open(zip_file, "rb") as f:
                st.download_button(
                    label="📥 Download All Clips as ZIP Package",
                    data=f.read(),
                    file_name="processed_reels.zip",
                    mime="application/zip",
                    key="dl_zip_top",
                    use_container_width=True
                )
        
        st.markdown("<br>", unsafe_allow_html=True)

        clip_cols = st.columns(2)
        for idx, clip in enumerate(st.session_state.clips):
            with clip_cols[idx % 2]:
                with st.container():
                    st.markdown(f"""
                        <div class="dashboard-card">
                            <h4>🎞️ {os.path.basename(clip)}</h4>
                        </div>
                    """, unsafe_allow_html=True)
                    st.video(clip)
                    
                    # Local Download button
                    with open(clip, "rb") as f:
                        st.download_button(
                            label=f"⬇️ Download {os.path.basename(clip)}",
                            data=f.read(),
                            file_name=os.path.basename(clip),
                            mime="video/mp4",
                            key=f"dl_clip_{idx}"
                        )
                    
                    # Facebook Auto Post Trigger Button
                    if st.button(f"🚀 Publish to Facebook Page (Reel #{idx+1})", key=f"fb_post_{idx}", type="secondary"):
                        if not fb_page_id or not fb_access_token:
                            st.error("⚠️ Please enter Facebook Page ID and Access Token in the sidebar!")
                        else:
                            with st.spinner(f"Publishing {os.path.basename(clip)} to Facebook..."):
                                success, msg = publish_to_facebook_reel(clip, fb_page_id, fb_access_token, default_caption)
                                if success:
                                    st.success(msg)
                                else:
                                    st.error(msg)
                    
                    st.markdown("<br>", unsafe_allow_html=KeyError if 'KeyError' in globals() else "<br>")

else:
    st.markdown("""
        <div style="text-align: center; padding: 60px 20px; background-color: #161b22; border: 1px dashed #30363d; border-radius: 12px;">
            <h3>📂 No Video Loaded Yet</h3>
            <p style="color: #8b949e;">Please use the sidebar to upload a video file to begin automated processing and posting.</p>
        </div>
    """, unsafe_allow_html=True)
