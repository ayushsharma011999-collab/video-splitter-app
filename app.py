import os
import shutil
import subprocess
import zipfile
import requests
import streamlit as st


# =========================
# CONFIG
# =========================

st.set_page_config(
    page_title="Video Splitter",
    page_icon="🎬",
    layout="centered"
)

# Find FFmpeg installed by packages.txt
FFMPEG = shutil.which("ffmpeg")

if not FFMPEG:
    st.error("FFmpeg install nahi hua. Please packages.txt check karein.")
    st.stop()


# =========================
# FUNCTIONS
# =========================

def get_video_duration(video_path):
    cmd = [
        FFMPEG,
        "-i",
        video_path
    ]

    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    output = result.stderr

    import re

    match = re.search(
        r"Duration:\s*(\d+):(\d+):([\d.]+)",
        output
    )

    if not match:
        return 0

    hours = int(match.group(1))
    minutes = int(match.group(2))
    seconds = float(match.group(3))

    return hours * 3600 + minutes * 60 + seconds


def split_video(video_path, output_dir, clip_duration=60):

    os.makedirs(output_dir, exist_ok=True)

    duration = get_video_duration(video_path)

    if duration <= 0:
        raise Exception("Video duration read nahi ho paayi.")

    total_clips = int(duration // clip_duration)

    if duration % clip_duration > 0:
        total_clips += 1

    clips = []

    for i in range(total_clips):

        start = i * clip_duration

        output_file = os.path.join(
            output_dir,
            f"Reel_Part_{i + 1}.mp4"
        )

        cmd = [
            FFMPEG,
            "-y",
            "-ss",
            str(start),
            "-i",
            video_path,
            "-t",
            str(clip_duration),

            # 9:16 vertical video
            "-vf",
            "scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920",

            "-c:v",
            "libx264",

            "-preset",
            "veryfast",

            "-crf",
            "23",

            "-c:a",
            "aac",

            "-b:a",
            "128k",

            "-movflags",
            "+faststart",

            output_file
        ]

        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        if result.returncode != 0:
            raise Exception(
                "FFmpeg error:\n\n" + result.stderr[-3000:]
            )

        if os.path.exists(output_file):
            clips.append(output_file)

    return clips


def create_zip(files, zip_name):

    with zipfile.ZipFile(
        zip_name,
        "w",
        zipfile.ZIP_DEFLATED
    ) as zip_file:

        for file in files:
            zip_file.write(
                file,
                os.path.basename(file)
            )


# =========================
# UI
# =========================

st.title("🎬 Video Splitter")

st.write(
    "Upload video aur usko automatic 9:16 vertical clips mein split karein."
)


uploaded_file = st.file_uploader(
    "Video upload karein",
    type=[
        "mp4",
        "mov",
        "avi",
        "mkv"
    ]
)


if uploaded_file:

    input_file = os.path.join(
        "/tmp",
        uploaded_file.name
    )

    with open(input_file, "wb") as f:
        f.write(uploaded_file.getbuffer())

    st.success("Video upload ho gaya ✅")

    duration = get_video_duration(input_file)

    if duration > 0:
        st.info(
            f"Video duration: {duration:.1f} seconds"
        )

    clip_duration = st.number_input(
        "Har clip kitne seconds ka ho?",
        min_value=10,
        max_value=180,
        value=60,
        step=10
    )

    if st.button(
        "🎬 Video Split Karein",
        type="primary"
    ):

        output_dir = "/tmp/reels"

        # Remove old files
        if os.path.exists(output_dir):
            shutil.rmtree(output_dir)

        os.makedirs(output_dir)

        try:

            with st.spinner(
                "Video process ho raha hai..."
            ):

                clips = split_video(
                    input_file,
                    output_dir,
                    clip_duration
                )

            if not clips:
                st.error("Koi clip create nahi hui.")
                st.stop()

            st.success(
                f"✅ {len(clips)} clips successfully create ho gayi!"
            )

            # Show clips
            for clip in clips:

                st.video(clip)

                with open(clip, "rb") as f:

                    st.download_button(
                        label=f"⬇️ {os.path.basename(clip)}",
                        data=f.read(),
                        file_name=os.path.basename(clip),
                        mime="video/mp4"
                    )

            # ZIP
            zip_file = "/tmp/reels.zip"

            create_zip(
                clips,
                zip_file
            )

            with open(zip_file, "rb") as f:

                st.download_button(
                    label="📦 Download All Clips ZIP",
                    data=f.read(),
                    file_name="reels.zip",
                    mime="application/zip"
                )

        except Exception as e:

            st.error("❌ Video processing failed")

            st.code(str(e))
