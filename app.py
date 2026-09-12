import os
import time
import subprocess
import zipfile
import requests
import streamlit as st

st.set_page_config(page_title="FB Video Splitter & Auto-Poster", layout="wide")

st.title("🎥 Auto Video Splitter & Facebook Auto-Poster")
st.write(
    "Upload long video, split into clips, and post sequentially with Error Guard, Auto-Retry, and Resume options."
)

# --- SIDEBAR CONFIGURATION ---
st.sidebar.header("🔑 Facebook Credentials")
fb_page_id = st.sidebar.text_input("Facebook Page ID", type="password", help="Apna Page ID enter karein")
fb_access_token = st.sidebar.text_input("Page Access Token", type="password", help="Apna Page Access Token enter karein")

st.sidebar.header("⏱️ Posting Delay Settings")
delay_minutes = st.sidebar.number_input(
    "Clips ke beech Delay (Minutes):", 
    min_value=0, 
    max_value=1440, 
    value=5, 
    help="0 rakhenge toh saari clips bina delay ke sequentially post hongi."
)

st.sidebar.header("🔄 Retry Settings")
max_retries = st.sidebar.number_input("Max Retries on Error:", min_value=1, max_value=5, value=3)

# --- SESSION STATE MANAGEMENT ---
if "clips" not in st.session_state:
    st.session_state.clips = []
if "posted_count" not in st.session_state:
    st.session_state.posted_count = 0
if "is_stopped_due_to_error" not in st.session_state:
    st.session_state.is_stopped_due_to_error = False

# --- MAIN UI ---
st.subheader("1️⃣ Upload & Split Settings")
uploaded_file = st.file_uploader("Upload Video (MP4, MOV, AVI)", type=["mp4", "mov", "avi"])

col1, col2, col3 = st.columns(3)
with col1:
    time_unit = st.selectbox("Time Unit:", options=["Seconds", "Minutes"], index=0)

with col2:
    if time_unit == "Seconds":
        duration_input = st.number_input("Clip Duration (Seconds):", min_value=1, max_value=3600, value=30, step=5)
        chunk_duration = duration_input
    else:
        duration_input = st.number_input("Clip Duration (Minutes):", min_value=1, max_value=60, value=1, step=1)
        chunk_duration = duration_input * 60

with col3:
    custom_caption_prefix = st.text_input("Video Caption Title:", value="Follow My Page for More Videos!")

# --- FB UPLOAD FUNCTION WITH RETRY LOGIC ---
def post_video_to_facebook_with_retry(video_path, caption, page_id, access_token, max_retries=3):
    url = f"https://graph.facebook.com/v18.0/{page_id}/videos"
    payload = {
        'description': caption,
        'access_token': access_token
    }
    
    for attempt in range(1, max_retries + 1):
        try:
            with open(video_path, 'rb') as video_file:
                files = {'source': video_file}
                response = requests.post(url, data=payload, files=files, timeout=300)
                res_data = response.json()
                
                if "id" in res_data:
                    return {"success": True, "id": res_data["id"], "attempt": attempt}
                else:
                    error_msg = res_data.get('error', {}).get('message', 'Unknown API Error')
                    if attempt < max_retries:
                        time.sleep(5)  # Wait 5 sec before retry
                    else:
                        return {"success": False, "error": error_msg, "attempt": attempt}
        except Exception as e:
            if attempt < max_retries:
                time.sleep(5)
            else:
                return {"success": False, "error": str(e), "attempt": attempt}

# --- UPLOAD & PROCESS LOGIC ---
if uploaded_file is not None:
    input_path = "input_video.mp4"
    with open(input_path, "wb") as f:
        f.write(uploaded_file.read())

    st.success("Video upload ho gayi hai!")

    btn_col1, btn_col2 = st.columns(2)
    
    start_btn = btn_col1.button("🚀 Process, Split & Start Posting")
    resume_btn = False
    if st.session_state.is_stopped_due_to_error and st.session_state.posted_count < len(st.session_state.clips):
        resume_btn = btn_col2.button("🔄 Fix Error & Resume Posting")

    if start_btn:
        st.session_state.posted_count = 0
        st.session_state.is_stopped_due_to_error = False
        
        if not fb_page_id or not fb_access_token:
            st.error("⚠️ Error: Post karne ke liye Sidebar me Facebook Page ID aur Access Token daalna zaroori hai.")
        else:
            with st.spinner("Video split ho rahi hai... Kripya wait karein."):
                output_dir = "output_clips"
                os.makedirs(output_dir, exist_ok=True)

                clips = []
                part_num = 1
                start_time = 0

                while True:
                    output_filename = f"Part_{part_num}.mp4"
                    output_filepath = os.path.join(output_dir, output_filename)

                    text_to_draw = f"Part {part_num}"
                    video_filter = (
                        f"drawtext=text='{text_to_draw}':fontcolor=white:fontsize=28:"
                        f"borderw=2:bordercolor=black:x=w-text_w-30:y=30"
                    )

                    ffmpeg_cmd = [
                        "ffmpeg", "-y",
                        "-ss", str(start_time),
                        "-i", input_path,
                        "-t", str(chunk_duration),
                        "-vf", video_filter,
                        "-c:v", "libx264",
                        "-c:a", "aac",
                        output_filepath,
                    ]

                    subprocess.run(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

                    if not os.path.exists(output_filepath) or os.path.getsize(output_filepath) < 1000:
                        if os.path.exists(output_filepath):
                            os.remove(output_filepath)
                        break

                    clips.append(output_filepath)
                    start_time += chunk_duration
                    part_num += 1

                st.session_state.clips = clips

    # Execution Loop for Posting
    if (start_btn or resume_btn) and len(st.session_state.clips) > 0:
        clips = st.session_state.clips
        total_clips = len(clips)
        
        st.success(f" Total **{total_clips}** clips taiyar hain!")
        st.subheader("📊 Live Posting Dashboard & Sequence Guard")

        m_col1, m_col2, m_col3, m_col4 = st.columns(4)
        m_col1.metric("Total Clips", total_clips)
        m_posted = m_col2.metric("Posted", st.session_state.posted_count)
        m_remaining = m_col3.metric("Remaining", total_clips - st.session_state.posted_count)
        m_status = m_col4.metric("Status", "Posting..." if not st.session_state.is_stopped_due_to_error else "Halted")

        progress_bar = st.progress(st.session_state.posted_count / total_clips)
        status_text = st.empty()
        log_container = st.container()

        st.session_state.is_stopped_due_to_error = False

        # Loop starting from where it last left off (Sequence preservation)
        for idx in range(st.session_state.posted_count, total_clips):
            clip_path = clips[idx]
            current_part = idx + 1
            caption = f"{custom_caption_prefix} - Part {current_part}"

            status_text.text(f"Uploading Part {current_part} of {total_clips} to Facebook...")

            # Post with Retries
            res = post_video_to_facebook_with_retry(
                clip_path, caption, fb_page_id, fb_access_token, max_retries=max_retries
            )

            if res["success"]:
                st.session_state.posted_count += 1
                with log_container:
                    st.write(f"✅ **Part {current_part}/{total_clips}** Posted Successfully! (Attempt: {res['attempt']}, Video ID: `{res['id']}`)")
                
                # Update Dashboard metrics
                m_col2.metric("Posted", st.session_state.posted_count)
                m_col3.metric("Remaining", total_clips - st.session_state.posted_count)
                progress_bar.progress(st.session_state.posted_count / total_clips)

                # Delay Timer for next post
                if st.session_state.posted_count < total_clips and delay_minutes > 0:
                    delay_seconds = int(delay_minutes * 60)
                    for sec_left in range(delay_seconds, 0, -1):
                        status_text.text(f"⏳ Next part ({current_part + 1}) upload hone me {sec_left} seconds baki hain...")
                        time.sleep(1)
            else:
                # CRITICAL STEP: STOP EVERYTHING IF POSTING FAILS
                st.session_state.is_stopped_due_to_error = True
                m_col4.metric("Status", "⚠️ ERROR HALT")
                with log_container:
                    st.error(f"❌ **Part {current_part}/{total_clips}** Failed after {max_retries} attempts: {res['error']}")
                    st.warning("🚨 Sequence Guard Active: Aage ke parts posting STOP kar diye gaye hain taaki sequence na kharab ho. Error fix karke 'Resume Posting' button dabayein.")
                break

        if not st.session_state.is_stopped_due_to_error and st.session_state.posted_count == total_clips:
            status_text.text("🎉 Saari clips perfect sequence me successful upload ho gayi hain!")

        # ZIP Backup Download
        zip_filename = "all_video_parts.zip"
        with zipfile.ZipFile(zip_filename, "w") as zipf:
            for clip in clips:
                zipf.write(clip, os.path.basename(clip))

        with open(zip_filename, "rb") as f:
            st.download_button(
                label="📥 Backup ke liye Clips (.zip) Download Karein",
                data=f,
                file_name="Facebook_Clips_Backup.zip",
                mime="application/zip",
            )
