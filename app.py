import os
import subprocess
import zipfile
import streamlit as st

st.title("🎥 Auto Video Splitter & Text App")
st.write(
    "Upload a long video. It will split into 30s clips with 'Part 1, Part 2...'"
    " inside the video, and custom file names!"
)

uploaded_file = st.file_uploader(
    "Upload your video (MP4, MOV)", type=["mp4", "mov", "avi"]
)

if uploaded_file is not None:
  input_path = "input_video.mp4"
  with open(input_path, "wb") as f:
    f.write(uploaded_file.read())

  st.success("Video uploaded successfully!")

  if st.button("🚀 Process & Split Video"):
    with st.spinner(
        "Processing video... Please wait (this can take a minute)."
    ):
      output_dir = "output_clips"
      os.makedirs(output_dir, exist_ok=True)

      chunk_duration = 30  # 30 seconds per clip
      clips = []
      part_num = 1
      start_time = 0

      while True:
        # File name format: Follow My Page for More Videos Part_1.mp4
        output_filename = f"Follow My Page for More Videos Part_{part_num}.mp4"
        output_filepath = os.path.join(output_dir, output_filename)

        # Text inside video screen: Only Part 1, Part 2, etc.
        text_to_draw = f"Part {part_num}"

        # FFmpeg filter to add text inside the video (Top-Center)
        video_filter = (
            f"drawtext=text='{text_to_draw}':fontcolor=white:fontsize=48:"
            f"borderw=3:bordercolor=black:x=(w-text_w)/2:y=50"
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
            # Add file to zip with its proper custom name
            zipf.write(clip, os.path.basename(clip))

        st.success(
            f"Done! Successfully created {len(clips)} clips with custom names"
            " and text."
        )

        with open(zip_filename, "rb") as f:
          st.download_button(
              label="📥 Download All Clips (.zip)",
              data=f,
              file_name="Follow_My_Page_Clips.zip",
              mime="application/zip",
          )
      else:
        st.error(
            "Could not process the video. Please try a different video format."
        )
