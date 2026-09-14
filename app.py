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

# Custom Dashboard CSS Injection
st.markdown("""
    <style>
    /* Main background & font adjustments */
    .main {
        background-color: #0e1117;
    }
    /* Card Container Styling */
    .dashboard-card {
        background-color: #161b22;
        border: 1px solid #30363d;
        padding: 20px;
        border-radius: 12px;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
        margin-bottom: 20px;
    }
    /* Metric styling */
    .metric-value {
        font-size: 24px;
        font-weight: bold;
        color: #58a6ff;
    }
    /* Header title style */
    h1, h2, h3 {
        letter-spacing: -0.5px;
    }
    /* Custom button spacing */
    .stButton button {
        width: 100%;
        border-radius: 8px;
        font-weight: 600;
        padding: 0.5rem 1rem;
    }
    </style>
""", unsafe_allow_html=True)

# Find FFmpeg installed by packages.txt
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
    
    # Aspect Ratio Filter mapping
    if "9:16" in aspect_ratio:
        vf_scale = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"
    elif "16:9" in aspect_ratio:
        vf_scale = "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080"
    elif "1:1" in aspect_ratio:
        vf_scale = "scale=1080:1080:force_original_aspect_ratio=increase,crop=1080:1080"
    else:
        vf_scale = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"

    # Watermark Filter logic
    if watermark_text.strip():
        safe_text = watermark_text.replace("'", "").replace(":", "")
        watermark_filter = f",drawtext=text='{safe_text}':fontcolor=white:fontsize=48:box=1:boxcolor=black@0.5:boxborderw=10:x=w-tw-50:y=h-th-50"
    else:
        watermark_filter = ""

    final_vf = vf_scale + watermark_filter

    # Progress bar setup inside dashboard card look
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


# =========================
# DASHBOARD LAYOUT (UI)
# =========================

# Top Header Banner
st.markdown("""
    <div style="padding: 10px 0; border-bottom: 1px solid #30363d; margin-bottom: 25px;">
        <h1 style="color: #c9d1d9; margin: 0; font-size: 28px;">🎬 Pro Video Studio Dashboard</h1>
        <p style="color: #8b949e; margin: 5px 0 0 0;">Transform long videos into viral vertical shorts & reels instantly.</p>
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
    
    clip_duration = st.slider(
        "Clip Duration (Seconds)",
        min_value=15,
        max_value=120,
        value=60,
        step=15
    )

    aspect_ratio = st.selectbox(
        "Aspect Ratio Format",
        [
            "9:16 (Vertical / Reels / Shorts)",
            "16:9 (Horizontal / YouTube)",
            "1:1 (Square / Feed Post)"
        ]
    )

    watermark_text = st.text_input(
        "🏷️ Watermark / Handle",
        placeholder="@YourChannel",
        max_chars=25
    )

# Handle file upload persistence
if uploaded_file is not None:
    temp_input_path = os.path.join("/tmp", uploaded_file.name)
    if st.session_state.input_file != temp_input_path:
        with open(temp_input_path, "wb") as f:
            f.write(uploaded_file.getbuffer())
        st.session_state.input_file = temp_input_path
        st.session_state.duration = get_video_duration(temp_input_path)
        st.session_state.clips = []

# Main Dashboard Workspace
if st.session_state.input_file and os.path.exists(st.session_state.input_file):
    
    # Metrics Row
    col_m1, col_m2, col_m3 = st.columns(3)
    with col_m1:
        st.markdown(f"""
            <div class="dashboard-card" style="text-align: center;">
                <p style="color: #8b949e; margin:0;">File Name</p>
                <p class="metric-value" style="font-size: 16px; overflow: hidden; text-overflow: ellipsis;">{os.path.basename(st.session_state.input_file)}</p>
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
                <p style="color: #8b949e; margin:0;">Estimated Clips</p>
                <p class="metric-value">~ {est_clips} Parts</p>
            </div>
        """, unsafe_allow_html=True)

    # Action Trigger Section inside a card container
    st.markdown("### 🚀 Render Workspace")
    with st.container():
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
            with st.spinner("Rendering clips via FFmpeg engine..."):
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

    # Output Gallery Section if clips are ready
    if st.session_state.clips:
        st.markdown("---")
        st.markdown("### 📦 Generated Output Gallery")
        
        # Download All ZIP Button at top of gallery
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

        # Display Clips in a Grid layout (2 columns)
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
                    with open(clip, "rb") as f:
                        st.download_button(
                            label=f"⬇️ Download {os.path.basename(clip)}",
                            data=f.read(),
                            file_name=os.path.basename(clip),
                            mime="video/mp4",
                            key=f"dl_clip_{idx}"
                        )
                    st.markdown("<br>", unsafe_allow_html=True)

else:
    # Empty State Dashboard View
    st.markdown("""
        <div style="text-align: center; padding: 60px 20px; background-color: #161b22; border: 1px dashed #30363d; border-radius: 12px;">
            <h3>📂 No Video Loaded Yet</h3>
            <p style="color: #8b949e;">Please use the left sidebar to upload a video file (.mp4, .mov, .avi) to initialize the dashboard.</p>
        </div>
    """, unsafe_allow_html=True)
