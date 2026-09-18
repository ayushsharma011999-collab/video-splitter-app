import streamlit as st
import requests
import os
import json
import subprocess
import tempfile
import shutil
from pathlib import Path


# ============================================================
# PAGE CONFIG
# ============================================================

st.set_page_config(
    page_title="Pro Video Studio",
    page_icon="🎬",
    layout="wide"
)


# ============================================================
# CONFIGURATION
# ============================================================

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"

ONEDRIVE_USER = "my@011999.onmicrosoft.com"

CHUNK_SIZE = 5 * 1024 * 1024

MAX_UPLOAD_SIZE_MB = 2048


# ============================================================
# FACEBOOK DESTINATIONS
# ============================================================

PENDING_FOLDERS = {
    "Smart Deals India": "Pending_Posts",
    "Movies and web series": "Movies_Pending_Posts",
}

GITHUB_WORKFLOWS = {
    "Smart Deals India": "main.yml",
    "Movies and web series": "facebook-auto-poster-movies.yml",
}


# ============================================================
# CUSTOM CSS
# ============================================================

st.markdown(
    """
    <style>

    .main-title {
        font-size: 42px;
        font-weight: 800;
        margin-bottom: 0;
    }

    .sub-title {
        font-size: 18px;
        opacity: 0.75;
        margin-bottom: 25px;
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
# SECRET HELPER
# ============================================================

def get_secret(name, default=""):
    try:
        return st.secrets[name]
    except Exception:
        return os.getenv(name, default)


# ============================================================
# MICROSOFT GRAPH TOKEN
# ============================================================

def get_application_access_token():

    tenant_id = get_secret("AZURE_TENANT_ID")
    client_id = get_secret("AZURE_CLIENT_ID")
    client_secret = get_secret("AZURE_CLIENT_SECRET")

    if not tenant_id:
        raise Exception("AZURE_TENANT_ID is missing.")

    if not client_id:
        raise Exception("AZURE_CLIENT_ID is missing.")

    if not client_secret:
        raise Exception("AZURE_CLIENT_SECRET is missing.")

    token_url = (
        f"https://login.microsoftonline.com/"
        f"{tenant_id}/oauth2/v2.0/token"
    )

    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": "https://graph.microsoft.com/.default",
        "grant_type": "client_credentials",
    }

    response = requests.post(
        token_url,
        data=data,
        timeout=60
    )

    if response.status_code != 200:
        raise Exception(
            f"Microsoft token error: "
            f"{response.status_code} - {response.text}"
        )

    token_data = response.json()

    access_token = token_data.get("access_token")

    if not access_token:
        raise Exception(
            "Microsoft Graph access token not received."
        )

    return access_token


# ============================================================
# GRAPH HEADERS
# ============================================================

def graph_headers(token):

    return {
        "Authorization": f"Bearer {token}"
    }


# ============================================================
# CHECK ONEDRIVE
# ============================================================

def get_user_drive(token):

    url = (
        f"{GRAPH_BASE_URL}/users/"
        f"{ONEDRIVE_USER}/drive"
    )

    response = requests.get(
        url,
        headers=graph_headers(token),
        timeout=60
    )

    if response.status_code != 200:
        raise Exception(
            f"Unable to access OneDrive: "
            f"{response.status_code} - {response.text}"
        )

    return response.json()


# ============================================================
# GET FOLDER
# ============================================================

def get_folder(token, folder_name):

    url = (
        f"{GRAPH_BASE_URL}/users/"
        f"{ONEDRIVE_USER}/drive/root:/{folder_name}"
    )

    response = requests.get(
        url,
        headers=graph_headers(token),
        timeout=60
    )

    if response.status_code == 200:
        return response.json()

    if response.status_code == 404:
        return None

    raise Exception(
        f"Folder lookup failed: "
        f"{response.status_code} - {response.text}"
    )


# ============================================================
# CREATE FOLDER
# ============================================================

def create_folder(token, folder_name):

    url = (
        f"{GRAPH_BASE_URL}/users/"
        f"{ONEDRIVE_USER}/drive/root/children"
    )

    payload = {
        "name": folder_name,
        "folder": {},
        "@microsoft.graph.conflictBehavior": "fail"
    }

    response = requests.post(
        url,
        headers={
            **graph_headers(token),
            "Content-Type": "application/json"
        },
        json=payload,
        timeout=60
    )

    if response.status_code not in [200, 201]:
        raise Exception(
            f"Folder creation failed: "
            f"{response.status_code} - {response.text}"
        )

    return response.json()


# ============================================================
# GET OR CREATE FOLDER
# ============================================================

def get_or_create_folder(token, folder_name):

    folder = get_folder(
        token,
        folder_name
    )

    if folder:
        return folder

    return create_folder(
        token,
        folder_name
    )


# ============================================================
# SMALL FILE UPLOAD
# ============================================================

def upload_small_file(
    token,
    folder_id,
    file_path,
    file_name
):

    url = (
        f"{GRAPH_BASE_URL}/users/"
        f"{ONEDRIVE_USER}/drive/items/"
        f"{folder_id}:/{file_name}:/content"
    )

    with open(file_path, "rb") as file:

        response = requests.put(
            url,
            headers=graph_headers(token),
            data=file,
            timeout=600
        )

    if response.status_code not in [200, 201]:

        raise Exception(
            f"File upload failed: "
            f"{response.status_code} - {response.text}"
        )

    return response.json()


# ============================================================
# LARGE FILE UPLOAD
# ============================================================

def upload_large_file(
    token,
    folder_id,
    file_path,
    file_name
):

    create_session_url = (
        f"{GRAPH_BASE_URL}/users/"
        f"{ONEDRIVE_USER}/drive/items/"
        f"{folder_id}:/{file_name}:/createUploadSession"
    )

    session_payload = {
        "item": {
            "@microsoft.graph.conflictBehavior": "replace",
            "name": file_name
        }
    }

    session_response = requests.post(
        create_session_url,
        headers={
            **graph_headers(token),
            "Content-Type": "application/json"
        },
        json=session_payload,
        timeout=60
    )

    if session_response.status_code not in [200, 201]:

        raise Exception(
            f"Upload session failed: "
            f"{session_response.status_code} - "
            f"{session_response.text}"
        )

    upload_url = session_response.json().get(
        "uploadUrl"
    )

    if not upload_url:
        raise Exception(
            "Upload URL was not returned."
        )

    file_size = os.path.getsize(file_path)

    start = 0

    with open(file_path, "rb") as file:

        while start < file_size:

            end = min(
                start + CHUNK_SIZE,
                file_size
            ) - 1

            length = end - start + 1

            file.seek(start)

            chunk = file.read(length)

            headers = {
                "Content-Length": str(length),
                "Content-Range":
                    f"bytes {start}-{end}/{file_size}"
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
                    f"Chunk upload failed: "
                    f"{response.status_code} - "
                    f"{response.text}"
                )

            start = end + 1

    return response.json()


# ============================================================
# UPLOAD FILE
# ============================================================

def upload_file_to_onedrive(
    token,
    folder_id,
    file_path,
    file_name
):

    file_size = os.path.getsize(file_path)

    if file_size <= 4 * 1024 * 1024:

        return upload_small_file(
            token,
            folder_id,
            file_path,
            file_name
        )

    return upload_large_file(
        token,
        folder_id,
        file_path,
        file_name
    )


# ============================================================
# VIDEO DURATION
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
        capture_output=True,
        text=True
    )

    if result.returncode != 0:

        raise Exception(
            f"Unable to read video duration: "
            f"{result.stderr}"
        )

    try:

        return float(
            result.stdout.strip()
        )

    except Exception:

        raise Exception(
            "Invalid video duration."
        )


# ============================================================
# SPLIT VIDEO + EFFECTS + EPISODE/PART OVERLAY
# ============================================================

def split_video(
    input_path,
    output_dir,
    clip_duration,
    aspect_ratio,
    watermark_text,
    episode_number=1,
    effect_preset="Trending",
    enable_zoom=True,
    enable_motion=True,
    enable_fade=True,
    enable_text=True
):

    os.makedirs(output_dir, exist_ok=True)

    duration = get_video_duration(input_path)

    if duration <= 0:
        raise Exception("Video duration is invalid.")

    base_name = Path(input_path).stem
    original_file_name = Path(input_path).name

    output_files = []

    total_clips = int(
        (duration + clip_duration - 1) // clip_duration
    )

    def escape_drawtext(text):
        return (
            str(text)
            .replace("\\", "\\\\")
            .replace(":", "\\:")
            .replace("'", "\\'")
            .replace("%", "\\%")
            .replace(",", "\\,")
            .replace("[", "\\[")
            .replace("]", "\\]")
        )

    # --------------------------------------------------------
    # EFFECT PRESET
    # --------------------------------------------------------

    if effect_preset == "Clean":
        zoom_amount = 0.0
        motion_amount = 0.0

    elif effect_preset == "Dynamic":
        zoom_amount = 0.025
        motion_amount = 2.0

    elif effect_preset == "Cinematic":
        zoom_amount = 0.018
        motion_amount = 1.0

    else:
        # Trending
        zoom_amount = 0.022
        motion_amount = 1.5

    # --------------------------------------------------------
    # PROCESS EACH CLIP
    # --------------------------------------------------------

    for index in range(total_clips):

        part_number = index + 1
        start_time = index * clip_duration

        output_name = (
            f"{base_name}_clip_{part_number:03d}.mp4"
        )

        output_path = os.path.join(
            output_dir,
            output_name
        )

        # ----------------------------------------------------
        # EPISODE + PART TEXT
        # ----------------------------------------------------

        episode_text = (
            f"EP {int(episode_number):02d} "
            f"• PART {part_number:02d}/{total_clips}"
        )

        escaped_file_name = escape_drawtext(
            original_file_name
        )

        escaped_episode_text = escape_drawtext(
            episode_text
        )

        escaped_watermark = escape_drawtext(
            watermark_text
        )

        filters = []

        # ----------------------------------------------------
        # ASPECT RATIO
        # ----------------------------------------------------

        if aspect_ratio == "9:16":
            filters.append(
                "crop=ih*9/16:ih"
            )

        elif aspect_ratio == "16:9":
            filters.append(
                "crop=iw:iw*9/16"
            )

        elif aspect_ratio == "1:1":
            filters.append(
                "crop=min(iw\\,ih):min(iw\\,ih)"
            )

        # ----------------------------------------------------
        # SMOOTH ZOOM
        # ----------------------------------------------------

        if enable_zoom and zoom_amount > 0:

            zoom_filter = (
                "scale="
                "iw*1.022:"
                "ih*1.022,"
                "crop=iw/1.022:"
                "ih/1.022"
            )

            filters.append(zoom_filter)

        # ----------------------------------------------------
        # SUBTLE MOTION
        # ----------------------------------------------------

        if enable_motion and motion_amount > 0:

            motion_filter = (
                "crop="
                "iw:"
                "ih:"
                f"x='{motion_amount}*sin(n/45)':"
                "y=0"
            )

            filters.append(motion_filter)

        # ----------------------------------------------------
        # FADE IN / FADE OUT
        # ----------------------------------------------------

        if enable_fade:

            fade_duration = 0.35

            actual_clip_duration = min(
                float(clip_duration),
                float(duration - start_time)
            )

            fade_out_start = max(
                actual_clip_duration - fade_duration,
                0
            )

            filters.append(
                f"fade=t=in:st=0:d={fade_duration}"
            )

            filters.append(
                f"fade=t=out:st={fade_out_start}:d={fade_duration}"
            )

        # ----------------------------------------------------
        # FILE NAME - TOP RIGHT
        # ----------------------------------------------------

        if enable_text:

            filters.append(
                (
                    "drawtext="
                    f"text='{escaped_file_name}':"
                    "fontcolor=white:"
                    "fontsize=28:"
                    "x=w-tw-20:"
                    "y=20:"
                    "box=1:"
                    "boxcolor=black@0.55:"
                    "boxborderw=10"
                )
            )

            # ------------------------------------------------
            # EPISODE + PART - BOTTOM RIGHT
            # ------------------------------------------------

            filters.append(
                (
                    "drawtext="
                    f"text='{escaped_episode_text}':"
                    "fontcolor=white:"
                    "fontsize=30:"
                    "x=w-tw-20:"
                    "y=h-th-20:"
                    "box=1:"
                    "boxcolor=black@0.60:"
                    "boxborderw=10"
                )
            )

        # ----------------------------------------------------
        # WATERMARK
        # ----------------------------------------------------

        if watermark_text.strip():

            filters.append(
                (
                    "drawtext="
                    f"text='{escaped_watermark}':"
                    "fontcolor=white:"
                    "fontsize=28:"
                    "x=20:"
                    "y=h-th-20:"
                    "box=1:"
                    "boxcolor=black@0.50:"
                    "boxborderw=8"
                )
            )

        # ----------------------------------------------------
        # BUILD VIDEO FILTER
        # ----------------------------------------------------

        if not filters:
            video_filter = "null"
        else:
            video_filter = ",".join(filters)

        # ----------------------------------------------------
        # FFMPEG COMMAND
        # ----------------------------------------------------

        command = [
            "ffmpeg",
            "-y",
            "-ss",
            str(start_time),
            "-i",
            input_path,
            "-t",
            str(clip_duration),
            "-vf",
            video_filter,
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "23",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            output_path
        ]

        result = subprocess.run(
            command,
            capture_output=True,
            text=True
        )

        if result.returncode != 0:

            raise Exception(
                f"FFmpeg failed for clip "
                f"{part_number}: "
                f"{result.stderr}"
            )

        output_files.append(output_path)

    return output_files, total_clips


# ============================================================
# CREATE METADATA
# ============================================================

def create_metadata(
    source_video,
    total_clips,
    clip_duration,
    aspect_ratio,
    watermark_text
):

    return {
        "source_video": source_video,
        "total_clips": total_clips,
        "clip_duration_seconds": clip_duration,
        "aspect_ratio": aspect_ratio,
        "watermark_text": watermark_text,
        "status": "Pending"
    }


# ============================================================
# GITHUB AUTO POSTER
# ============================================================

def run_github_auto_poster(
    workflow_file
):

    github_token = get_secret(
        "GITHUB_TOKEN"
    )

    github_repo = get_secret(
        "GITHUB_REPO",
        "ayushsharma011999-collab/video-splitter-app"
    )

    if not github_token:

        raise Exception(
            "GITHUB_TOKEN is missing from Streamlit Secrets."
        )

    url = (
        f"https://api.github.com/repos/"
        f"{github_repo}/actions/workflows/"
        f"{workflow_file}/dispatches"
    )

    headers = {
        "Authorization":
            f"Bearer {github_token}",
        "Accept":
            "application/vnd.github+json",
        "X-GitHub-Api-Version":
            "2022-11-28"
    }

    payload = {
        "ref": "main"
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=60
    )

    if response.status_code not in [
        201,
        204
    ]:

        raise Exception(
            f"GitHub workflow dispatch failed: "
            f"{response.status_code} - "
            f"{response.text}"
        )

    return True


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:

    st.header("⚙️ Settings")

    st.subheader("📘 Facebook Settings")

    posting_destination = st.radio(
        "Select Facebook Page",
        [
            "Smart Deals India",
            "Movies and web series"
        ],
        index=0
    )

    selected_pending_folder = (
        PENDING_FOLDERS[
            posting_destination
        ]
    )

    selected_github_workflow = (
        GITHUB_WORKFLOWS[
            posting_destination
        ]
    )

    st.divider()

    st.subheader("🎞️ Video Settings")

    # ========================================================
    # VIDEO SETTINGS
    # ========================================================

    st.subheader("🎞️ Video Settings")

    # --------------------------------------------------------
    # CLIP DURATION
    # --------------------------------------------------------

    clip_duration = st.number_input(
        "Clip Duration (seconds)",
        min_value=15,
        max_value=1200,
        value=30,
        step=5,
        help="Maximum duration is 1200 seconds (20 minutes)."
    )

    minutes = int(clip_duration // 60)
    seconds = int(clip_duration % 60)

    st.caption(
        f"⏱️ Selected: {minutes} min {seconds} sec"
    )

    # --------------------------------------------------------
    # EPISODE NUMBER
    # --------------------------------------------------------

    episode_number = st.number_input(
        "Episode Number",
        min_value=1,
        max_value=9999,
        value=1,
        step=1,
        help="This number will appear inside every generated clip."
    )

    st.caption(
        f"📺 Episode: EP {int(episode_number):02d}"
    )

    # --------------------------------------------------------
    # ASPECT RATIO
    # --------------------------------------------------------

    aspect_ratio = st.selectbox(
        "Aspect Ratio",
        [
            "Original",
            "9:16",
            "16:9",
            "1:1"
        ],
        index=0
    )

    # --------------------------------------------------------
    # EFFECT PRESET
    # --------------------------------------------------------

    effect_preset = st.selectbox(
        "✨ Video Effect Preset",
        [
            "Trending",
            "Clean",
            "Dynamic",
            "Cinematic"
        ],
        index=0,
        help=(
            "Trending uses subtle zoom, motion and fade "
            "effects suitable for short-form videos."
        )
    )

    # --------------------------------------------------------
    # INDIVIDUAL EFFECTS
    # --------------------------------------------------------

    st.markdown("**✨ Effects**")

    enable_zoom = st.checkbox(
        "🔍 Smooth Zoom",
        value=True
    )

    enable_motion = st.checkbox(
        "🎥 Subtle Motion",
        value=True
    )

    enable_fade = st.checkbox(
        "🌊 Fade In / Out",
        value=True
    )

    enable_text = st.checkbox(
        "📝 Episode + Part Text",
        value=True
    )

    # --------------------------------------------------------
    # WATERMARK
    # --------------------------------------------------------

    watermark_text = st.text_input(
        "Watermark Text",
        value="",
        placeholder="Example: @MyPage"
    )

    # --------------------------------------------------------
    # PREVIEW INFORMATION
    # --------------------------------------------------------

    st.caption(
        "📍 Top Right → Video File Name"
    )

    st.caption(
        "📍 Bottom Right → EP XX • PART XX/XX"
    )

    st.caption(
        "✨ Trending preset → Smooth Zoom + Motion + Fade"
    )

    st.divider()

    st.subheader("📂 Selected Destination")

    st.info(
        f"**Facebook:** {posting_destination}\n\n"
        f"**OneDrive:** {selected_pending_folder}\n\n"
        f"**Workflow:** {selected_github_workflow}"
    )


# ============================================================
# UPLOAD VIDEO
# ============================================================

st.header("📤 Upload Video")

uploaded_file = st.file_uploader(
    "Choose a video",
    type=[
        "mp4",
        "mov",
        "mkv",
        "avi",
        "webm"
    ]
)


# ============================================================
# ONEDRIVE CONNECTION
# ============================================================

st.divider()

st.header("☁️ OneDrive")

if "graph_token" not in st.session_state:

    st.session_state.graph_token = None


connect_col1, connect_col2 = st.columns(
    [1, 3]
)

with connect_col1:

    connect_onedrive = st.button(
        "🔗 Connect OneDrive",
        use_container_width=True
    )


if connect_onedrive:

    try:

        with st.spinner(
            "Connecting to OneDrive..."
        ):

            token = get_application_access_token()

            get_user_drive(token)

            st.session_state.graph_token = token

        st.success(
            "OneDrive connected successfully."
        )

    except Exception as e:

        st.error(
            f"OneDrive connection failed: {e}"
        )


# ============================================================
# ONEDRIVE STATUS
# ============================================================

if st.session_state.graph_token:

    st.success(
        f"🟢 OneDrive Ready — "
        f"{selected_pending_folder} folder active"
    )

else:

    st.warning(
        "🟡 OneDrive not connected"
    )


# ============================================================
# PROCESS VIDEO
# ============================================================

if uploaded_file:

    st.divider()

    st.header("🎬 Video Processing")

    file_size_mb = (
        uploaded_file.size
        / (1024 * 1024)
    )

    st.info(
        f"File: {uploaded_file.name}  \n"
        f"Size: {file_size_mb:.2f} MB"
    )

    if file_size_mb > MAX_UPLOAD_SIZE_MB:

        st.error(
            f"File is too large. Maximum allowed "
            f"size is {MAX_UPLOAD_SIZE_MB} MB."
        )

        st.stop()

    process_button = st.button(
        "🚀 Split Video & Upload to OneDrive",
        type="primary",
        use_container_width=True
    )

    if process_button:

        if not st.session_state.graph_token:

            st.error(
                "Please connect OneDrive first."
            )

            st.stop()

        token = (
            st.session_state.graph_token
        )

        temp_dir = None

        try:

            # ------------------------------------------------
            # TEMP DIRECTORY
            # ------------------------------------------------

            temp_dir = tempfile.mkdtemp(
                prefix="pro_video_studio_"
            )

            input_path = os.path.join(
                temp_dir,
                uploaded_file.name
            )

            output_dir = os.path.join(
                temp_dir,
                "clips"
            )

            os.makedirs(
                output_dir,
                exist_ok=True
            )

            # ------------------------------------------------
            # SAVE VIDEO
            # ------------------------------------------------

            with open(
                input_path,
                "wb"
            ) as file:

                file.write(
                    uploaded_file.getbuffer()
                )

            # ------------------------------------------------
            # VIDEO DURATION
            # ------------------------------------------------

            with st.spinner(
                "Reading video information..."
            ):

                original_duration = (
                    get_video_duration(
                        input_path
                    )
                )

            original_minutes = (
                original_duration / 60
            )

            st.info(
                f"Original video duration: "
                f"{original_minutes:.2f} minutes"
            )

            # ------------------------------------------------
            # SPLIT
            # ------------------------------------------------

            with st.spinner(
                f"Splitting video into "
                f"{clip_duration}-second clips..."
            ):

                clips, total_clips = split_video(
                    input_path=input_path,
                    output_dir=output_dir,
                    clip_duration=clip_duration,
                    aspect_ratio=aspect_ratio,
                    watermark_text=watermark_text,
                    episode_number=episode_number,
                    effect_preset=effect_preset,
                    enable_zoom=enable_zoom,
                    enable_motion=enable_motion,
                    enable_fade=enable_fade,
                    enable_text=enable_text
                )

            st.success(
                f"Video split completed. "
                f"{total_clips} clip(s) created."
            )

            # ------------------------------------------------
            # SHOW CLIPS
            # ------------------------------------------------

            st.subheader(
                "📦 Generated Clips"
            )

            for clip in clips:

                clip_size = (
                    os.path.getsize(clip)
                    / (1024 * 1024)
                )

                st.write(
                    f"🎞️ {os.path.basename(clip)} "
                    f"— {clip_size:.2f} MB"
                )

            # ------------------------------------------------
            # GET/CREATE PENDING FOLDER
            # ------------------------------------------------

            with st.spinner(
                f"Opening {selected_pending_folder}..."
            ):

                folder = get_or_create_folder(
                    token,
                    selected_pending_folder
                )

            folder_id = folder.get("id")

            if not folder_id:

                raise Exception(
                    "OneDrive folder ID was not returned."
                )

            # ------------------------------------------------
            # METADATA
            # ------------------------------------------------

            metadata = create_metadata(
                source_video=uploaded_file.name,
                total_clips=total_clips,
                clip_duration=clip_duration,
                aspect_ratio=aspect_ratio,
                watermark_text=watermark_text
            )

            metadata_path = os.path.join(
                temp_dir,
                f"{Path(uploaded_file.name).stem}_metadata.json"
            )

            with open(
                metadata_path,
                "w",
                encoding="utf-8"
            ) as file:

                json.dump(
                    metadata,
                    file,
                    indent=2,
                    ensure_ascii=False
                )

            # ------------------------------------------------
            # UPLOAD CLIPS
            # ------------------------------------------------

            st.subheader(
                "☁️ Uploading to OneDrive"
            )

            progress = st.progress(0)

            total_uploads = len(clips)

            for index, clip in enumerate(
                clips,
                start=1
            ):

                clip_name = os.path.basename(
                    clip
                )

                with st.spinner(
                    f"Uploading {clip_name}..."
                ):

                    upload_file_to_onedrive(
                        token=token,
                        folder_id=folder_id,
                        file_path=clip,
                        file_name=clip_name
                    )

                progress.progress(
                    index / total_uploads
                )

                st.write(
                    f"✅ Uploaded: {clip_name}"
                )

            # ------------------------------------------------
            # UPLOAD METADATA
            # ------------------------------------------------

            with st.spinner(
                "Uploading metadata..."
            ):

                upload_file_to_onedrive(
                    token=token,
                    folder_id=folder_id,
                    file_path=metadata_path,
                    file_name=os.path.basename(
                        metadata_path
                    )
                )

            st.success(
                "✅ Metadata uploaded successfully."
            )

            st.success(
                f"🎉 All clips uploaded to "
                f"`{selected_pending_folder}`"
            )

            st.info(
                "Videos are now available for the "
                "Facebook Auto Poster."
            )

        except Exception as e:

            st.error(
                f"❌ Processing failed: {e}"
            )

            st.exception(e)

        finally:

            if temp_dir:

                shutil.rmtree(
                    temp_dir,
                    ignore_errors=True
                )


# ============================================================
# FACEBOOK AUTO POSTER BUTTON
# ============================================================

st.divider()

st.header("📢 Facebook Auto Poster")

st.info(
    f"Selected Page: **{posting_destination}**\n\n"
    f"OneDrive Folder: **{selected_pending_folder}**\n\n"
    f"GitHub Workflow: **{selected_github_workflow}**"
)

# ============================================================
# THIS IS THE FACEBOOK POST BUTTON
# ============================================================

run_poster = st.button(
    "▶️ Run Facebook Auto Poster",
    type="primary",
    use_container_width=True
)

if run_poster:

    try:

        with st.spinner(
            f"Starting {posting_destination} Facebook Auto Poster..."
        ):

            run_github_auto_poster(
                selected_github_workflow
            )

        st.success(
            "🚀 Facebook Auto Poster started successfully!"
        )

        st.success(
            f"📘 Destination: {posting_destination}"
        )

        st.info(
            "GitHub Actions will now pick the next "
            "pending video and post it to Facebook."
        )

    except Exception as e:

        st.error(
            f"❌ Facebook Auto Poster could not be started: {e}"
        )


# ============================================================
# FOOTER
# ============================================================

st.divider()

st.caption(
    "🎬 Pro Video Studio | "
    "Streamlit + OneDrive + GitHub Actions"
)
