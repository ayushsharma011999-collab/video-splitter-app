import os
import subprocess
import zipfile
import streamlit as st

st.title("🎥 Auto Video Splitter & Text Adder")
st.write(
    "Upload any long video, and it will split it into 30-second clips with 'Part"
    " 1, Part 2...' text automatically!"
)

uploaded_file = st.file_uploader(
    "Upload your video (MP4, MOV)", type=["mp4", "mov", "avi"]
)

if uploaded_file is not None:
  # Save uploaded video temporarily
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

      # Infinite loop that cuts clips sequentially until video ends
      while True:
        output_filename = f"Part_{part_num}.mp4"
        output_filepath = os.path.join(output_dir, output_filename)
        text_to_draw = f"Part {part_num}"

        # FFmpeg command to cut a 30s segment starting from 'start_time'
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
            (
                "drawtext=text='"
                + text_to_draw
                + "':fontcolor=white:fontsize=48:borderw=3:bordercolor=black:x=(w-text_w)/2:y=50"
            ),
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            output_filepath,
        ]

        process = subprocess.run(
            ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )

        # Check if the generated clip is empty or failed (meaning video has ended)
        if (
            not os.path.exists(output_filepath)
            or os.path.getsize(output_filepath) < 1000
        ):
          # Delete the last empty/failed file if created
          if os.path.exists(output_filepath):
            os.remove(output_filepath)
          break

        clips.append(output_filepath)
        start_time += chunk_duration
        part_num += 1

      if len(clips) > 0:
        # Zip all generated clips together
        zip_filename = "all_video_parts.zip"
        with zipfile.ZipFile(zip_filename, "w") as zipf:
          for clip in clips:
            zipf.write(clip, os.path.basename(clip))

        st.success(
            f"Done! Successfully created {len(clips)} clips with 'Part X'"
            " text."
        )

        # Provide Download Button for ZIP
        with open(zip_filename, "rb") as f:
          st.download_button(
              label="📥 Download All Clips (.zip)",
              data=f,
              file_name="video_parts.zip",
              mime="application/zip",
          )
      else:
        st.error(
            "Could not process the video. Please try a different video format"
            " (like .mp4)."
        )
