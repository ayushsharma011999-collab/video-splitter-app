import os
import subprocess
import zipfile
import streamlit as st

st.title("🎥 Auto Video Splitter & Text Adder")
st.write(
    "Upload a long video, and it will split it into 30-second clips with 'Part 1, Part 2...' text!"
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

      # 1. Get video duration using ffprobe
      cmd_duration = [
          "ffprobe",
          "-v",
          "error",
          "-show_entries",
          "format=duration",
          "-of",
          "default=noprint_wrappers=1:n=1",
          input_path,
      ]
      result = subprocess.run(
          cmd_duration, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
      )
      try:
        total_duration = float(result.stdout)
      except Exception:
        total_duration = 0

      if total_duration == 0:
        st.error(
            "Could not read video duration. Please try a different video."
        )
      else:
        chunk_duration = 30  # 30 seconds
        clips = []

        # Loop to cut video every 30 seconds and add text
        for i, start_time in enumerate(
            range(0, int(total_duration), chunk_duration)
        ):
          part_num = i + 1
          output_filename = f"Part_{part_num}.mp4"
          output_filepath = os.path.join(output_dir, output_filename)

          # FFmpeg command to cut 30s clip and burn text "Part X"
          # Text styling: White color, black border (borderw=2), positioned at top-center
          text_to_draw = f"Part {part_num}"
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

          subprocess.run(
              ffmpeg_cmd,
              stdout=subprocess.PIPE,
              stderr=subprocess.PIPE,
              text=True,
          )
          clips.append(output_filepath)

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
