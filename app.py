import os
import shutil
import subprocess
import zipfile
import requests
import streamlit as st
import re

# =========================
# CONFIG
# =========================

st.set_page_config(
    page_title="Advanced Video Splitter",
    page_icon="🎬",
    layout="centered"
)

# Find FFmpeg installed by packages.txt
FFMPEG = shutil.which("ffmpeg")

if not FFMPEG:
    st.error("FFmpeg install nahi hua. Please packages.txt check karein.")
    st.stop()

# Initialize Session State variables to prevent reset on mobile refresh/reload
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
    if aspect_ratio == "9:16 (Vertical / Reels)":
        vf_scale = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"
    elif aspect_ratio == "16:9 (Horizontal / YouTube)":
        vf_scale = "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080"
    elif aspect_ratio == "1:1 (Square / Post)":
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

    # Progress bar setup
    progress_bar = st.progress(0)
    status_text = st.empty()

    for i in range(total_clips):
        start = i * clip_duration
        output_file = os.path.join(output_dir, f"Clip_Part_{i + 1}.mp4")

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

        # Update progress bar
        progress_percentage = (i + 1) / total_clips
        progress_bar.progress(progress_percentage)
        status_text.text(f"Processing clip {i + 1} of {total_clips}...")

    status_text.text("Processing complete! 🎉")
    return clips


def create_zip(files, zip_name):
    with zipfile.ZipFile(zip_name, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for file in files:
            zip_file.write(file, os.path.basename(file))


# =========================
# UI
# =========================

st.title("🎬 Advanced Video Splitter")
st.write("Video upload karein, aspect ratio select karein, watermark dalein aur automatic clips banayein!")

uploaded_file = st.file_uploader(
    "Video upload karein",
    type=["mp4", "mov", "avi", "mkv"]
)

# Handle file upload and store persistently in session state
if uploaded_file is not None:
    # Save only if it's a new file or not saved yet
    temp_input_path = os.path.join("/tmp", uploaded_file.name)
    
    if st.session_state.input_file != temp_input_path:
        with open(temp_input_path, "wb") as f:
            f.write(uploaded_file.getbuffer())
        
        st.session_state.input_file = temp_input_path
        st.session_state.duration = get_video_duration(temp_input_path)
        st.session_state.clips = [] # Reset old clips on new file upload

# If file exists in session (survives minor reloads if browser keeps tmp)
if st.session_state.input_file and os.path.exists(st.session_state.input_file):
    st.success("Video upload ho gaya ✅")

    if st.session_state.duration > 0:
        st.info(f"Video duration: {st.session_state.duration:.1f} seconds")

    # Sidebar / Options Section
    st.markdown("### ⚙️ Customization Settings")
    
    col1, col2 = st.columns(2)

    with col1:
        clip_duration = st.number_input(
            "Har clip kitne seconds ki ho?",
            min_value=10,
            max_value=180,
            value=60,
            step=10
        )

    with col2:
        aspect_ratio = st.selectbox(
            "Video Aspect Ratio Select Karein",
            [
                "9:16 (Vertical / Reels)",
                "16:9 (Horizontal / YouTube)",
                "1:1 (Square / Post)"
            ]
        )

    watermark_text = st.text_input(
        "🏷️ Custom Watermark Text (Optional)",
        placeholder="Jaise: @AapkaChannelName",
        max_chars=30
    )

    if st.button("🎬 Video Split & Process Karein", type="primary"):
        output_dir = "/tmp/reels"

        # Remove old files
        if os.path.exists(output_dir):
            shutil.rmtree(output_dir)

        os.makedirs(output_dir)

        try:
            st.session_state.clips = split_video(
                st.session_state.input_file,
                output_dir,
                clip_duration,
                aspect_ratio,
                watermark_text
            )
        except Exception as e:
            st.error("❌ Video processing failed")
            st.code(str(e))

    # Display clips if already generated (saved in session)
    if st.session_state.clips:
        st.success(f"✅ {len(st.session_state.clips)} clips successfully create ho gayi!")

        for clip in st.session_state.clips:
            if os.path.exists(clip):
                st.video(clip)
                with open(clip, "rb") as f:
                    st.download_button(
                        label=f"⬇️ {os.path.basename(clip)}",
                        data=f.read(),
                        file_name=os.path.basename(clip),
                        mime="video/mp4",
                        key=f"dl_{os.path.basename(clip)}"
                    )

        # ZIP Download
        zip_file = "/tmp/reels.zip"
        create_zip(st.session_state.clips, zip_file)

        if os.path.exists(zip_file):
            with open(zip_file, "rb") as f:
                st.download_button(
                    label="📦 Download All Clips ZIP",
                    data=f.read(),
                    file_name="reels.zip",
                    mime="application/zip",
                    key="dl_zip_all"
                )
