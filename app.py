import os
import json
import math
import time
import tempfile
import subprocess
from pathlib import Path
from urllib.parse import quote

import requests
import msal
import streamlit as st


# ============================================================
# PAGE CONFIG
# ============================================================

st.set_page_config(
    page_title="Pro Video Studio | Hybrid OneDrive",
    page_icon="🎬",
    layout="wide"
)


# ============================================================
# CONSTANTS
# ============================================================

ONEDRIVE_USER = "my@011999.onmicrosoft.com"

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"

ONEDRIVE_FOLDER = "Pending_Posts"

# 5 MiB = exact multiple of 320 KiB
CHUNK_SIZE = 5 * 1024 * 1024

MAX_STREAMLIT_UPLOAD_SIZE_MB = 2048


# ============================================================
# CUSTOM CSS
# ============================================================

st.markdown(
    """
    <style>

    .main-title {
        font-size: 34px;
        font-weight: 700;
        margin-bottom: 5px;
    }

    .sub-title {
        font-size: 16px;
        opacity: 0.75;
        margin-bottom: 25px;
    }

    .status-box {
        padding: 15px;
        border-radius: 10px;
        margin: 10px 0;
    }

    </style>
    """,
    unsafe_allow_html=True
)


# ============================================================
# HEADER
# ============================================================

st.markdown(
    '<div class="main-title">🎬 Pro Video Studio</div>',
    unsafe_allow_html=True
)

st.markdown(
    '<div class="sub-title">Video Splitter + OneDrive Pending Posts</div>',
    unsafe_allow_html=True
)


# ============================================================
# SESSION STATE
# ============================================================

if "onedrive_access_token" not in st.session_state:
    st.session_state.onedrive_access_token = None

if "onedrive_drive_id" not in st.session_state:
    st.session_state.onedrive_drive_id = None

if "onedrive_connected" not in st.session_state:
    st.session_state.onedrive_connected = False


# ============================================================
# AZURE / MICROSOFT GRAPH AUTHENTICATION
# ============================================================

def get_application_access_token():
    """
    Gets Microsoft Graph application token using
    credentials stored in Streamlit Secrets.
    """

    try:
        client_id = st.secrets["AZURE_CLIENT_ID"]
        tenant_id = st.secrets["AZURE_TENANT_ID"]
        client_secret = st.secrets["AZURE_CLIENT_SECRET"]

    except Exception:
        st.error(
            """
            ❌ Azure credentials Streamlit Secrets me configured nahi hain.

            Required secrets:

            - AZURE_CLIENT_ID
            - AZURE_TENANT_ID
            - AZURE_CLIENT_SECRET
            """
        )
        st.stop()

    authority = (
        f"https://login.microsoftonline.com/{tenant_id}"
    )

    confidential_app = msal.ConfidentialClientApplication(
        client_id=client_id,
        authority=authority,
        client_credential=client_secret
    )

    result = confidential_app.acquire_token_for_client(
        scopes=[GRAPH_SCOPE]
    )

    if "access_token" not in result:

        error_description = result.get(
            "error_description",
            "Unknown Microsoft authentication error."
        )

        raise Exception(
            "Microsoft Graph authentication failed:\n"
            + error_description
        )

    return result["access_token"]


# ============================================================
# GRAPH REQUEST HELPER
# ============================================================

def graph_headers(access_token):
    return {
        "Authorization": f"Bearer {access_token}"
    }


# ============================================================
# GET ONEDRIVE DRIVE
# ============================================================

def get_onedrive_drive(access_token):

    url = (
        f"{GRAPH_BASE_URL}"
        f"/users/{quote(ONEDRIVE_USER, safe='')}"
        f"/drive"
    )

    response = requests.get(
        url,
        headers=graph_headers(access_token),
        timeout=60
    )

    if response.status_code != 200:

        raise Exception(
            f"OneDrive drive access failed.\n"
            f"Status: {response.status_code}\n"
            f"Response: {response.text}"
        )

    return response.json()


# ============================================================
# GET DRIVE ID
# ============================================================

def get_onedrive_drive_id(access_token):

    if st.session_state.onedrive_drive_id:
        return st.session_state.onedrive_drive_id

    drive = get_onedrive_drive(access_token)

    drive_id = drive.get("id")

    if not drive_id:
        raise Exception(
            "OneDrive Drive ID nahi mila."
        )

    st.session_state.onedrive_drive_id = drive_id

    return drive_id


# ============================================================
# CHECK / CREATE FOLDER
# ============================================================

def ensure_pending_posts_folder(access_token):

    folder_path = quote(
        ONEDRIVE_FOLDER,
        safe=""
    )

    url = (
        f"{GRAPH_BASE_URL}"
        f"/users/{quote(ONEDRIVE_USER, safe='')}"
        f"/drive/root:/{folder_path}"
    )

    response = requests.get(
        url,
        headers=graph_headers(access_token),
        timeout=60
    )

    # Folder already exists
    if response.status_code == 200:
        return response.json()

    # Folder doesn't exist
    if response.status_code == 404:

        create_url = (
            f"{GRAPH_BASE_URL}"
            f"/users/{quote(ONEDRIVE_USER, safe='')}"
            f"/drive/root/children"
        )

        payload = {
            "name": ONEDRIVE_FOLDER,
            "folder": {},
            "@microsoft.graph.conflictBehavior": "fail"
        }

        create_response = requests.post(
            create_url,
            headers={
                **graph_headers(access_token),
                "Content-Type": "application/json"
            },
            json=payload,
            timeout=60
        )

        if create_response.status_code not in [200, 201]:

            raise Exception(
                f"Pending_Posts folder create failed.\n"
                f"Status: {create_response.status_code}\n"
                f"Response: {create_response.text}"
            )

        return create_response.json()

    raise Exception(
        f"Pending_Posts folder check failed.\n"
        f"Status: {response.status_code}\n"
        f"Response: {response.text}"
    )


# ============================================================
# SMALL FILE UPLOAD
# ============================================================

def upload_small_file(
    access_token,
    file_path,
    file_name
):

    encoded_folder = quote(
        ONEDRIVE_FOLDER,
        safe=""
    )

    encoded_file = quote(
        file_name,
        safe=""
    )

    url = (
        f"{GRAPH_BASE_URL}"
        f"/users/{quote(ONEDRIVE_USER, safe='')}"
        f"/drive/root:/{encoded_folder}/{encoded_file}:/content"
    )

    with open(file_path, "rb") as file:

        response = requests.put(
            url,
            headers={
                **graph_headers(access_token),
                "Content-Type": "application/octet-stream"
            },
            data=file,
            timeout=600
        )

    if response.status_code not in [200, 201]:

        raise Exception(
            f"File upload failed.\n"
            f"Status: {response.status_code}\n"
            f"Response: {response.text}"
        )

    return response.json()


# ============================================================
# LARGE FILE UPLOAD SESSION
# ============================================================

def create_upload_session(
    access_token,
    file_name
):

    encoded_folder = quote(
        ONEDRIVE_FOLDER,
        safe=""
    )

    encoded_file = quote(
        file_name,
        safe=""
    )

    url = (
        f"{GRAPH_BASE_URL}"
        f"/users/{quote(ONEDRIVE_USER, safe='')}"
        f"/drive/root:/{encoded_folder}/{encoded_file}"
        f":/createUploadSession"
    )

    payload = {
        "item": {
            "@microsoft.graph.conflictBehavior": "replace",
            "name": file_name
        }
    }

    response = requests.post(
        url,
        headers={
            **graph_headers(access_token),
            "Content-Type": "application/json"
        },
        json=payload,
        timeout=60
    )

    if response.status_code not in [200, 201]:

        raise Exception(
            f"Upload session creation failed.\n"
            f"Status: {response.status_code}\n"
            f"Response: {response.text}"
        )

    data = response.json()

    upload_url = data.get("uploadUrl")

    if not upload_url:
        raise Exception(
            "Upload session URL nahi mila."
        )

    return upload_url


# ============================================================
# LARGE FILE UPLOAD
# ============================================================

def upload_large_file(
    access_token,
    file_path,
    file_name,
    progress_callback=None
):

    upload_url = create_upload_session(
        access_token,
        file_name
    )

    total_size = os.path.getsize(file_path)

    uploaded = 0

    with open(file_path, "rb") as file:

        while uploaded < total_size:

            chunk = file.read(CHUNK_SIZE)

            if not chunk:
                break

            start = uploaded
            end = uploaded + len(chunk) - 1

            headers = {
                "Content-Length": str(len(chunk)),
                "Content-Range":
                    f"bytes {start}-{end}/{total_size}"
            }

            response = requests.put(
                upload_url,
                headers=headers,
                data=chunk,
                timeout=600
            )

            if response.status_code not in [
                200,
                201,
                202
            ]:

                raise Exception(
                    f"Chunk upload failed.\n"
                    f"Status: {response.status_code}\n"
                    f"Response: {response.text}"
                )

            uploaded += len(chunk)

            if progress_callback:

                progress = (
                    uploaded / total_size
                    if total_size
                    else 1
                )

                progress_callback(progress)

    return True


# ============================================================
# UNIVERSAL FILE UPLOAD
# ============================================================

def upload_file_to_onedrive(
    access_token,
    file_path,
    file_name,
    progress_callback=None
):

    file_size = os.path.getsize(file_path)

    # Small file
    if file_size <= 4 * 1024 * 1024:

        result = upload_small_file(
            access_token,
            file_path,
            file_name
        )

        if progress_callback:
            progress_callback(1.0)

        return result

    # Large file
    return upload_large_file(
        access_token,
        file_path,
        file_name,
        progress_callback
    )


# ============================================================
# CONNECT ONEDRIVE
# ============================================================

def connect_onedrive():

    try:

        with st.spinner(
            "🔐 Microsoft Graph se connect ho raha hai..."
        ):

            token = get_application_access_token()

            drive = get_onedrive_drive(token)

            ensure_pending_posts_folder(token)

            st.session_state.onedrive_access_token = token
            st.session_state.onedrive_drive_id = drive["id"]
            st.session_state.onedrive_connected = True

        return True

    except Exception as e:

        st.session_state.onedrive_connected = False

        st.error(
            f"❌ OneDrive connection failed:\n\n{e}"
        )

        return False


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:

    st.header("⚙️ Settings")

    st.subheader("☁️ OneDrive")

    if st.session_state.onedrive_connected:

        st.success(
            "✅ OneDrive Connected"
        )

        st.caption(
            f"Folder: {ONEDRIVE_FOLDER}"
        )

    else:

        st.warning(
            "OneDrive not connected"
        )

    if st.button(
        "🔗 Connect OneDrive",
        use_container_width=True
    ):

        connect_onedrive()

    st.divider()

    st.subheader("🎞️ Video Settings")

    clip_duration = st.number_input(
        "Clip Duration (seconds)",
        min_value=1,
        max_value=600,
        value=30,
        step=1
    )

    aspect_ratio = st.selectbox(
        "Aspect Ratio",
        [
            "Original",
            "9:16",
            "16:9",
            "1:1"
        ]
    )

    watermark_text = st.text_input(
        "Watermark Text",
        value=""
    )

    st.divider()

    st.subheader("📘 Facebook Metadata")

    facebook_page_id = st.text_input(
        "Facebook Page ID",
        value=""
    )

    post_type = st.selectbox(
        "Post Type",
        [
            "Video",
            "Reel"
        ]
    )

    caption = st.text_area(
        "Caption",
        value=""
    )


# ============================================================
# MAIN UPLOAD
# ============================================================

st.header("📤 Upload Video")

uploaded_video = st.file_uploader(
    "Video file select karein",
    type=[
        "mp4",
        "mov",
        "mkv",
        "avi",
        "webm"
    ]
)


# ============================================================
# VIDEO PROCESSING FUNCTIONS
# ============================================================

def get_video_duration(file_path):

    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        file_path
    ]

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0:
        raise Exception(
            "FFprobe video duration read nahi kar paaya."
        )

    return float(
        result.stdout.strip()
    )


def split_video(
    input_file,
    output_directory,
    clip_duration
):

    os.makedirs(
        output_directory,
        exist_ok=True
    )

    output_pattern = os.path.join(
        output_directory,
        "clip_%03d.mp4"
    )

    command = [
        "ffmpeg",
        "-y",
        "-i",
        input_file,
        "-map",
        "0",
        "-c",
        "copy",
        "-f",
        "segment",
        "-segment_time",
        str(clip_duration),
        "-reset_timestamps",
        "1",
        output_pattern
    ]

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0:

        raise Exception(
            "Video splitting failed:\n"
            + result.stderr[-3000:]
        )

    clips = sorted(
        Path(output_directory).glob(
            "clip_*.mp4"
        )
    )

    return clips


# ============================================================
# PROCESS VIDEO
# ============================================================

if uploaded_video:

    st.success(
        f"Selected: {uploaded_video.name}"
    )

    st.write(
        f"File size: "
        f"{uploaded_video.size / (1024 * 1024):.2f} MB"
    )

    process_button = st.button(
        "🎬 Split Video & Upload to OneDrive",
        type="primary",
        use_container_width=True
    )

    if process_button:

        if not st.session_state.onedrive_connected:

            st.warning(
                "⚠️ Pehle **Connect OneDrive** button click karein."
            )

            st.stop()

        temp_root = tempfile.mkdtemp(
            prefix="video_studio_"
        )

        input_path = os.path.join(
            temp_root,
            uploaded_video.name
        )

        clips_directory = os.path.join(
            temp_root,
            "clips"
        )

        try:

            # ------------------------------------------------
            # SAVE ORIGINAL VIDEO
            # ------------------------------------------------

            with open(
                input_path,
                "wb"
            ) as file:

                file.write(
                    uploaded_video.getbuffer()
                )

            st.info(
                "📥 Video uploaded successfully. "
                "Processing start ho raha hai..."
            )

            # ------------------------------------------------
            # VIDEO DURATION
            # ------------------------------------------------

            duration = get_video_duration(
                input_path
            )

            st.write(
                f"⏱️ Video Duration: "
                f"{duration:.2f} seconds"
            )

            # ------------------------------------------------
            # SPLIT VIDEO
            # ------------------------------------------------

            with st.spinner(
                "✂️ Video split ho raha hai..."
            ):

                clips = split_video(
                    input_path,
                    clips_directory,
                    clip_duration
                )

            if not clips:

                raise Exception(
                    "Koi clip generate nahi hui."
                )

            st.success(
                f"✅ {len(clips)} clips generate hui."
            )

            # ------------------------------------------------
            # UPLOAD CLIPS
            # ------------------------------------------------

            st.subheader(
                "☁️ Uploading to OneDrive"
            )

            overall_progress = st.progress(
                0
            )

            status_text = st.empty()

            uploaded_count = 0

            total_clips = len(clips)

            for index, clip_path in enumerate(clips):

                clip_number = index + 1

                original_name = clip_path.name

                # Safe filename
                final_name = (
                    f"{Path(uploaded_video.name).stem}"
                    f"_clip_{clip_number:03d}.mp4"
                )

                status_text.write(
                    f"⬆️ Uploading "
                    f"{clip_number}/{total_clips}: "
                    f"{final_name}"
                )

                clip_progress = st.progress(
                    0
                )

                def update_progress(value):
                    clip_progress.progress(
                        min(
                            max(value, 0.0),
                            1.0
                        )
                    )

                upload_file_to_onedrive(
                    st.session_state.onedrive_access_token,
                    str(clip_path),
                    final_name,
                    update_progress
                )

                uploaded_count += 1

                overall_progress.progress(
                    uploaded_count / total_clips
                )

            # ------------------------------------------------
            # METADATA JSON
            # ------------------------------------------------

            metadata = {
                "source_video": uploaded_video.name,
                "post_type": post_type,
                "facebook_page_id": facebook_page_id,
                "caption": caption,
                "total_clips": total_clips,
                "clip_duration_seconds": clip_duration,
                "aspect_ratio": aspect_ratio,
                "watermark_text": watermark_text,
                "status": "Pending"
            }

            metadata_path = os.path.join(
                temp_root,
                "metadata.json"
            )

            with open(
                metadata_path,
                "w",
                encoding="utf-8"
            ) as metadata_file:

                json.dump(
                    metadata,
                    metadata_file,
                    indent=4,
                    ensure_ascii=False
                )

            # ------------------------------------------------
            # UPLOAD METADATA
            # ------------------------------------------------

            status_text.write(
                "⬆️ Uploading metadata.json..."
            )

            upload_file_to_onedrive(
                st.session_state.onedrive_access_token,
                metadata_path,
                f"{Path(uploaded_video.name).stem}_metadata.json"
            )

            # ------------------------------------------------
            # COMPLETE
            # ------------------------------------------------

            overall_progress.progress(
                1.0
            )

            st.success(
                f"""
                🎉 Processing complete!

                ✅ Clips generated: {total_clips}

                ✅ Clips uploaded: {uploaded_count}

                ☁️ OneDrive folder: {ONEDRIVE_FOLDER}

                📘 Metadata uploaded successfully.
                """
            )

            st.info(
                "GitHub Actions ab Pending_Posts folder "
                "se clips process/publish kar sakta hai."
            )

        except Exception as e:

            st.error(
                f"❌ Processing failed:\n\n{e}"
            )

        finally:

            # ------------------------------------------------
            # CLEAN TEMP FILES
            # ------------------------------------------------

            try:

                import shutil

                shutil.rmtree(
                    temp_root,
                    ignore_errors=True
                )

            except Exception:
                pass


# ============================================================
# FOOTER / STATUS
# ============================================================

st.divider()

if st.session_state.onedrive_connected:

    st.success(
        "🟢 OneDrive Ready — Pending_Posts folder active"
    )

else:

    st.caption(
        "🔴 OneDrive disconnected"
    )

