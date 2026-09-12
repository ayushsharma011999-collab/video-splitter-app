import os
import subprocess
import zipfile
import streamlit as st

st.title("🎥 Auto Video Splitter & Text App")
st.write(
    "Upload a long video, select custom clip duration, and download split clips with overlaid text."
)

# Upload Section
uploaded_file = st.file_uploader(
    "Upload your video (MP4, MOV, AVI)", type=["mp4", "mov", "avi"]
)

# Settings Section
st.subheader("⚙️ Clip Settings")
col1, col2 = st.columns(2)

with col1:
    time_unit = st.selectbox(
        "Time Unit Select karein:",
        options=["Seconds", "Minutes"],
        index=0
    )

with col2:
    if time_unit == "Seconds":
        duration_input = st.number_input(
            "Clip Duration (Seconds):", min_value=1, max_value=3600, value=30, step=5
        )
        chunk_duration = duration_input
    else:
        duration_input = st.number_input(
            "Clip Duration (Minutes):", min_value=1, max_value=60, value=1, step=1
        )
        chunk_duration = duration_input * 60  # Minutes ko seconds me convert kiya

if uploaded_file is not None:
    input_path = "input_video.mp4"
    with open(input_path, "wb") as f:
        f.write(uploaded_file.read())

    st.success("Video uploaded successfully!")

    if st.button("🚀 Process & Split Video"):
        with st.spinner("Processing video... Please wait (this can take a few minutes)."):
            output_dir = "output_clips"
            os.makedirs(output_dir, exist_ok=True)

            clips = []
            part_num = 1
            start_time = 0

            while True:
                output_filename = f"Follow My Page for More Videos Part_{part_num}.mp4"
                output_filepath = os.path.join(output_dir, output_filename)

                text_to_draw = f"Part {part_num}"

                video_filter = (
                    f"drawtext=text='{text_to_draw}':fontcolor=white:fontsize=28:"
                    f"borderw=2:bordercolor=black:x=w-text_w-30:y=30"
                )

                ffmpeg_cmd = [
                    "ffmpeg",
                    "-y",
                    "-ss",
                    str(start_time),
                    "-i",
                    input_path,
                    "-t",
                    str(chunk_duration),
                    "-vf",
                    video_filter,
                    "-c:v",
                    "libx264",
                    "-c:a",
                    "aac",
                    output_filepath,
                ]

                process = subprocess.run(
                    ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
                )

                # Termination check
                if (
                    not os.path.exists(output_filepath)
                    or os.path.getsize(output_filepath) < 1000
                ):
                    if os.path.exists(output_filepath):
                        os.remove(output_filepath)
                    break

                clips.append(output_filepath)
                start_time += chunk_duration
                part_num += 1

            if len(clips) > 0:
                zip_filename = "all_video_parts.zip"
                with zipfile.ZipFile(zip_filename, "w") as zipf:
                    for clip in clips:
                        zipf.write(clip, os.path.basename(clip))

                st.success(
                    f"Done! Successfully created {len(clips)} clips with duration of {duration_input} {time_unit.lower()} each."
                )

                with open(zip_filename, "rb") as f:
                    st.download_button(
                        label="📥 Download All Clips (.zip)",
                        data=f,
                        file_name="Follow_My_Page_Clips.zip",
                        mime="application/zip",
                    )
            else:
                st.error("Could not process the video. Please check the video format or ffmpeg installation.")
