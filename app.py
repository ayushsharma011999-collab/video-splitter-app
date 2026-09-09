import os
import zipfile
import streamlit as st
from moviepy.editor import VideoFileClip, TextClip, CompositeVideoClip

st.title("🎥 Auto Video Splitter & Text Adder")
st.write(
    "Upload a long video, and it will split it into 30-second clips with 'Part"
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
        "Processing video... Please wait (this might take a minute)."
    ):
      output_dir = "output_clips"
      os.makedirs(output_dir, exist_ok=True)

      try:
        # Load video using MoviePy (No ffprobe error issues)
        video = VideoFileClip(input_path)
        total_duration = video.duration
        chunk_duration = 30  # 30 seconds
        clips = []

        start_time = 0
        part_num = 1

        while start_time < total_duration:
          end_time = min(start_time + chunk_duration, total_duration)

          # Cut the subclip
          subclip = video.subclip(start_time, end_time)

          # Create Text Overlay ("Part 1", "Part 2", etc.)
          txt_clip = (
              TextClip(
                  f"Part {part_num}",
                  fontsize=50,
                  color="white",
                  stroke_color="black",
                  stroke_width=2,
              )
              .set_duration(subclip.duration)
              .set_position(("center", 50))
          )  # Positioned at top-center

          # Overlay text on video clip
          final_clip = CompositeVideoClip([subclip, txt_clip])

          output_filename = f"Part_{part_num}.mp4"
          output_filepath = os.path.join(output_dir, output_filename)

          # Write output file
          final_clip.write_videofile(
              output_filepath,
              codec="libx264",
              audio_codec="aac",
              fps=24,
              preset="fast",
              logger=None,
          )

          clips.append(output_filepath)
          start_time += chunk_duration
          part_num += 1

        video.close()

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

      except Exception as e:
        st.error(f"An error occurred during processing: {e}")
