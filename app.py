import os
import time
import subprocess
import zipfile
import requests
import streamlit as st

st.set_page_config(page_title="FB Reels Splitter & Auto-Poster", layout="wide")

st.title("🎥 Auto Video Splitter & Facebook Reels Auto-Poster")
st.write(
    "Upload video (MP4, MKV, AVI, MOV), automatically convert/crop to 9:16 Vertical Reel format, and post directly as Facebook Reels."
)

# --- SIDEBAR CONFIGURATION ---
st.sidebar.header("🔑 Facebook Credentials")
fb_page_id = st.sidebar.text_input("Facebook Page ID", type="password", help="Apna Page ID enter karein")
fb_access_token = st.sidebar.text_input("Page Access Token", type="password", help="Apna Page Access Token enter karein")

st.sidebar.header("⏱️ Posting Delay Settings")
delay_minutes = st.sidebar.number_input(
    "Reels ke beech Delay (Minutes):", 
    min_value=0, 
    max_value=1440, 
    value=5, 
    help="0 rakhenge toh saari reels bina delay ke sequentially post hongi."
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

# Updated File Uploader with MKV / Matroska MIME types to fix "video/matroska" error
uploaded_file = st.file_uploader(
    "Upload Video (MP4, MKV, AVI, MOV)", 
    type=["mp4", "mkv", "avi", "mov", "matroska", "x-matroska"]
)

col1, col2, col3 = st.columns(3)
with col1:
    time_unit = st.selectbox("Time Unit:", options=["Seconds", "Minutes"], index=0)

with col2:
    if time_unit == "Seconds":
        duration_input = st.number_input("Clip Duration (Seconds):", min_value=1, max_value=90, value=30, step=5)
        chunk_duration = duration_input
    else:
        duration_input = st.number_input("Clip Duration (Minutes):", min_value=1, max_value=2, value=1, step=1)
        chunk_duration = duration_input * 60

with col3:
    custom_caption_prefix = st.text_input("Reels Caption Title:", value="Follow My Page for More Reels!")

# --- FB REELS UPLOAD FUNCTION (3-STEP REELS API) ---
def post_reel_to_facebook(video_path, caption, page_id, access_token):
    try:
        # Step 1: Initialize Reel Upload Session
        init_url = f"https://graph.facebook.com/v18.0/{page_id}/video_reels"
        init_payload = {
            'upload_phase': 'start',
            'access_token': access_token
        }
        init_res = requests.post(init_url, data=init_payload, timeout=60).json()

        if "video_id" not in init_res:
            err = init_res.get('error', {}).get('message', 'Failed to initialize Reel upload.')
            return {"success": False, "error": err}

        video_id = init_res["video_id"]
        upload_url = init_res.get("upload_url")

        if not upload_url:
            upload_url = f"https://rupload.facebook.com/video-reels/{video_id}"

        # Step 2: Upload Video Binary File
        file_size = os.path.getsize(video_path)
        headers = {
            'Authorization': f'OAuth {access_token}',
            'offset': '0',
            'file_size': str(file_size)
        }
        with open(video_path, 'rb') as video_file:
            upload_res = requests.post(upload_url, headers=headers, data=video_file, timeout=300)

        upload_json = upload_res.json()
        if not upload_json.get("success"):
            err = upload_json.get('error', {}).get('message', 'Reel video transfer failed.')
            return {"success": False, "error": err}

        # Step 3: Publish Reel
        publish_url = f"https://graph.facebook.com/v18.0/{page_id}/video_reels"
        publish_payload = {
            'upload_phase': 'finish',
            'video_id': video_id,
            'video_state': 'PUBLISHED',
            'description': caption,
            'access_token': access_token
        }
        pub_res = requests.post(publish_url, data=publish_payload, timeout=60).json()

        if pub_res.get("success") or "id" in pub_res or pub_res.get("video_state") == "PUBLISHED":
            return {"success": True, "id": video_id}
        else:
            err = pub_res.get('error', {}).get('message', 'Failed to publish Reel.')
            return {"success": False, "error": err}

    except Exception as e:
        return {"success": False, "error": str(e)}

# --- REEL POST WITH RETRY ---
def post_reel_with_retry(video_path, caption, page_id, access_token, max_retries=3):
    for attempt in range(1, max_retries + 1):
        res = post_reel_to_facebook(video_path, caption, page_id, access_token)
        if res["success"]:
            res["attempt"] = attempt
            return res
        else:
            if attempt < max_retries:
                time.sleep(5)
            else:
                res["attempt"] = attempt
                return res

# --- UPLOAD & PROCESS LOGIC ---
if uploaded_file is not None:
    # Preserve original extension
    file_extension = os.path.splitext(uploaded_file.name)[1].lower()
    if not file_extension:
        file_extension = ".mkv" if "matroska" in uploaded_file.type else ".mp4"
        
    input_path = f"input_video{file_extension}"
    with open(input_path, "wb") as f:
        f.write(uploaded_file.read())

    st.success(f"Video ({file_extension.upper()}) upload ho gayi hai!")

    btn_col1, btn_col2 = st.columns(2)
    start_btn = btn_col1.button("🚀 Process, Convert to 9:16 Reel & Post")
    resume_btn = False
    if st.session_state.is_stopped_due_to_error and st.session_state.posted_count < len(st.session_state.clips):
        resume_btn = btn_col2.button("🔄 Fix Error & Resume Posting")

    if start_btn:
        st.session_state.posted_count = 0
        st.session_state.is_stopped_due_to_error = False
        
        if not fb_page_id or not fb_access_token:
            st.error("⚠️ Error: Post karne ke liye Sidebar me Facebook Page ID aur Access Token daalna zaroori hai.")
        else:
            with st.spinner("Video 9:16 Vertical Reel format me split aur convert ho rahi hai..."):
                output_dir = "output_clips"
                os.makedirs(output_dir, exist_ok=True)

                clips = []
                part_num = 1
                start_time = 0

                while True:
                    output_filename = f"Reel_Part_{part_num}.mp4"
                    output_filepath = os.path.join(output_dir, output_filename)

                    text_to_draw = f"Part {part_num}"
                    
                    video_filter = (
                        f"scale=1080:1920:force_original_aspect_ratio=increase,"
                        f"crop=1080:1920,"
                        f"drawtext=text='{text_to_draw}':fontcolor=white:fontsize=48:"
                        f"borderw=3:bordercolor=black:x=w-text_w-50:y=100"
                    )

                    # FFmpeg optimized command for MKV decoding
                    ffmpeg_cmd = [
                        "ffmpeg", "-y",
                        "-analyzeduration", "10M",
                        "-probesize", "10M",
                        "-ss", str(start_time),
                        "-i", input_path,
                        "-t", str(chunk_duration),
                        "-vf", video_filter,
                        "-c:v", "libx264",
                        "-preset", "fast",
                        "-crf", "23",
                        "-c:a", "aac",
                        "-b:a", "128k",
                        "-pix_fmt", "yuv420p",
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

    # Execution Loop for Reel Posting
    if (start_btn or resume_btn) and len(st.session_state.clips) > 0:
        clips = st.session_state.clips
        total_clips = len(clips)
        
        st.success(f" Total **{total_clips}** Reels (9:16 Vertical) taiyar hain!")
        st.subheader("📊 Live Reels Posting Dashboard & Sequence Guard")

        m_col1, m_col2, m_col3, m_col4 = st.columns(4)
        m_col1.metric("Total Reels", total_clips)
        m_posted = m_col2.metric("Posted", st.session_state.posted_count)
        m_remaining = m_col3.metric("Remaining", total_clips - st.session_state.posted_count)
        m_status = m_col4.metric("Status", "Posting Reels..." if not st.session_state.is_stopped_due_to_error else "Halted")

        progress_bar = st.progress(st.session_state.posted_count / total_clips)
        status_text = st.empty()
        log_container = st.container()

        st.session_state.is_stopped_due_to_error = False

        for idx in range(st.session_state.posted_count, total_clips):
            clip_path = clips[idx]
            current_part = idx + 1
            caption = f"{custom_caption_prefix} - Part {current_part} #reels #viral"

            status_text.text(f"Uploading Reel Part {current_part} of {total_clips} to Facebook Reels...")

            res = post_reel_with_retry(
                clip_path, caption, fb_page_id, fb_access_token, max_retries=max_retries
            )

            if res["success"]:
                st.session_state.posted_count += 1
                with log_container:
                    st.write(f"✅ **Reel Part {current_part}/{total_clips}** Published as Reel! (Reel ID: `{res['id']}`)")
                
                m_col2.metric("Posted", st.session_state.posted_count)
                m_col3.metric("Remaining", total_clips - st.session_state.posted_count)
                progress_bar.progress(st.session_state.posted_count / total_clips)

                if st.session_state.posted_count < total_clips and delay_minutes > 0:
                    delay_seconds = int(delay_minutes * 60)
                    for sec_left in range(delay_seconds, 0, -1):
                        status_text.text(f"⏳ Next Reel ({current_part + 1}) upload hone me {sec_left} seconds baki hain...")
                        time.sleep(1)
            else:
                st.session_state.is_stopped_due_to_error = True
                m_col4.metric("Status", "⚠️ ERROR HALT")
                with log_container:
                    st.error(f"❌ **Reel Part {current_part}/{total_clips}** Failed: {res['error']}")
                    st.warning("🚨 Sequence Guard Active: Token update karein aur 'Fix Error & Resume Posting' button dabayein.")
                break

        if not st.session_state.is_stopped_due_to_error and st.session_state.posted_count == total_clips:
            status_text.text("🎉 Saari Reels perfect sequence me Facebook Reels section me post ho gayi hain!")

        zip_filename = "all_reels_parts.zip"
        with zipfile.ZipFile(zip_filename, "w") as zipf:
            for clip in clips:
                zipf.write(clip, os.path.basename(clip))

        with open(zip_filename, "rb") as f:
            st.download_button(
                label="📥 Backup ke liye Reels (.zip) Download Karein",
                data=f,
                file_name="Facebook_Reels_Backup.zip",
                mime="application/zip",
            )
