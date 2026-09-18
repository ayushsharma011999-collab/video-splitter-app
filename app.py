import streamlit as st
import requests
import os
import json
import subprocess
import tempfile
import shutil
import io
import zipfile
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

    .progress-text {
        font-size: 16px;
        font-weight: 600;
    }

    .success-box {
        padding: 12px;
        border-radius: 10px;
        border: 1px solid rgba(0, 200, 100, 0.35);
        background: rgba(0, 200, 100, 0.08);
        margin-top: 10px;
        margin-bottom: 10px;
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

if "graph_token" not in st.session_state:
    st.session_state.graph_token = None

if "download_zip" not in st.session_state:
    st.session_state.download_zip = None

if "download_zip_name" not in st.session_state:
    st.session_state.download_zip_name = None

if "generated_clip_names" not in st.session_state:
    st.session_state.generated_clip_names = []

if "generated_total_clips" not in st.session_state:
    st.session_state.generated_total_clips = 0

if "processing_complete" not in st.session_state:
    st.session_state.processing_complete = False


# ============================================================
# SECRET HELPER
# ============================================================

def get_secret(name, default=""):

    try:
        return st.secrets[name]

    except Exception:

        return os.getenv(
            name,
            default
        )


# ============================================================
# MICROSOFT GRAPH TOKEN
# ============================================================

def get_application_access_token():

    tenant_id = get_secret(
        "AZURE_TENANT_ID"
    )

    client_id = get_secret(
        "AZURE_CLIENT_ID"
    )

    client_secret = get_secret(
        "AZURE_CLIENT_SECRET"
    )

    if not tenant_id:
        raise Exception(
            "AZURE_TENANT_ID is missing."
        )

    if not client_id:
        raise Exception(
            "AZURE_CLIENT_ID is missing."
        )

    if not client_secret:
        raise Exception(
            "AZURE_CLIENT_SECRET is missing."
        )

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
            f"{response.status_code} - "
            f"{response.text}"
        )

    token_data = response.json()

    access_token = token_data.get(
        "access_token"
    )

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
            f"{response.status_code} - "
            f"{response.text}"
        )

    return response.json()


# ============================================================
# GET FOLDER
# ============================================================

def get_folder(
    token,
    folder_name
):

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
        f"{response.status_code} - "
        f"{response.text}"
    )


# ============================================================
# CREATE FOLDER
# ============================================================

def create_folder(
    token,
    folder_name
):

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

    if response.status_code not in [
        200,
        201
    ]:

        raise Exception(
            f"Folder creation failed: "
            f"{response.status_code} - "
            f"{response.text}"
        )

    return response.json()


# ============================================================
# GET OR CREATE FOLDER
# ============================================================

def get_or_create_folder(
    token,
    folder_name
):

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
    file_name,
    progress_callback=None
):

    url = (
        f"{GRAPH_BASE_URL}/users/"
        f"{ONEDRIVE_USER}/drive/items/"
        f"{folder_id}:/{file_name}:/content"
    )

    file_size = os.path.getsize(
        file_path
    )

    if progress_callback:
        progress_callback(
            0,
            file_size
        )

    with open(
        file_path,
        "rb"
    ) as file:

        response = requests.put(
            url,
            headers=graph_headers(token),
            data=file,
            timeout=600
        )

    if response.status_code not in [
        200,
        201
    ]:

        raise Exception(
            f"File upload failed: "
            f"{response.status_code} - "
            f"{response.text}"
        )

    if progress_callback:
        progress_callback(
            file_size,
            file_size
        )

    return response.json()


# ============================================================
# LARGE FILE UPLOAD
# ============================================================

def upload_large_file(
    token,
    folder_id,
    file_path,
    file_name,
    progress_callback=None
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

    if session_response.status_code not in [
        200,
        201
    ]:

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

    file_size = os.path.getsize(
        file_path
    )

    start = 0

    if progress_callback:
        progress_callback(
            0,
            file_size
        )

    with open(
        file_path,
        "rb"
    ) as file:

        while start < file_size:

            end = min(
                start + CHUNK_SIZE,
                file_size
            ) - 1

            length = (
                end -
                start +
                1
            )

            file.seek(start)

            chunk = file.read(
                length
            )

            headers = {
                "Content-Length":
                    str(length),

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

            if progress_callback:

                progress_callback(
                    start,
                    file_size
                )

    return response.json()


# ============================================================
# UPLOAD FILE
# ============================================================

def upload_file_to_onedrive(
    token,
    folder_id,
    file_path,
    file_name,
    progress_callback=None
):

    file_size = os.path.getsize(
        file_path
    )

    if file_size <= 4 * 1024 * 1024:

        return upload_small_file(
            token=token,
            folder_id=folder_id,
            file_path=file_path,
            file_name=file_name,
            progress_callback=progress_callback
        )

    return upload_large_file(
        token=token,
        folder_id=folder_id,
        file_path=file_path,
        file_name=file_name,
        progress_callback=progress_callback
    )


# ============================================================
# VIDEO DURATION
# ============================================================

def get_video_duration(
    file_path
):

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
# CREATE LOCAL ZIP
# ============================================================

def create_local_zip(
    clips,
    source_video_name
):

    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(
        zip_buffer,
        mode="w",
        compression=zipfile.ZIP_DEFLATED
    ) as zip_file:

        for clip in clips:

            zip_file.write(
                clip,
                arcname=os.path.basename(
                    clip
                )
            )

    zip_buffer.seek(0)

    zip_name = (
        f"{Path(source_video_name).stem}"
        f"_clips.zip"
    )

    return (
        zip_buffer.getvalue(),
        zip_name
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
    enable_text=True,
    progress_callback=None
):

    os.makedirs(
        output_dir,
        exist_ok=True
    )

    duration = get_video_duration(
        input_path
    )

    if duration <= 0:
        raise Exception(
            "Video duration is invalid."
        )

    base_name = Path(
        input_path
    ).stem

    original_file_name = Path(
        input_path
    ).name

    output_files = []

    total_clips = int(
        (duration + clip_duration - 1)
        // clip_duration
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

        zoom_amount = 0.022
        motion_amount = 1.5

    # --------------------------------------------------------
    # INITIAL PROGRESS
    # --------------------------------------------------------

    if progress_callback:

        progress_callback(
            0,
            total_clips,
            "Preparing video..."
        )

    # --------------------------------------------------------
    # PROCESS EACH CLIP
    # --------------------------------------------------------

    for index in range(
        total_clips
    ):

        part_number = index + 1

        start_time = (
            index *
            clip_duration
        )

        output_name = (
            f"{base_name}_clip_"
            f"{part_number:03d}.mp4"
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

        escaped_file_name = (
            escape_drawtext(
                original_file_name
            )
        )

        escaped_episode_text = (
            escape_drawtext(
                episode_text
            )
        )

        escaped_watermark = (
            escape_drawtext(
                watermark_text
            )
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

        if (
            enable_zoom
            and zoom_amount > 0
        ):

            zoom_filter = (
                "scale="
                "iw*1.022:"
                "ih*1.022,"
                "crop=iw/1.022:"
                "ih/1.022"
            )

            filters.append(
                zoom_filter
            )

        # ----------------------------------------------------
        # SUBTLE MOTION
        # ----------------------------------------------------

        if (
            enable_motion
            and motion_amount > 0
        ):

            motion_filter = (
                "crop="
                "iw:"
                "ih:"
                f"x='{motion_amount}*sin(n/45)':"
                "y=0"
            )

            filters.append(
                motion_filter
            )

        # ----------------------------------------------------
        # FADE IN / OUT
        # ----------------------------------------------------

        if enable_fade:

            fade_duration = 0.35

            actual_clip_duration = min(
                float(clip_duration),
                float(duration - start_time)
            )

            fade_out_start = max(
                actual_clip_duration -
                fade_duration,
                0
            )

            filters.append(
                f"fade=t=in:"
                f"st=0:d={fade_duration}"
            )

            filters.append(
                f"fade=t=out:"
                f"st={fade_out_start}:"
                f"d={fade_duration}"
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
            # EPISODE + PART
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
        # VIDEO FILTER
        # ----------------------------------------------------

        if not filters:

            video_filter = "null"

        else:

            video_filter = ",".join(
                filters
            )

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

        output_files.append(
            output_path
        )

        # ----------------------------------------------------
        # SPLIT PROGRESS
        # ----------------------------------------------------

        if progress_callback:

            progress_callback(
                part_number,
                total_clips,
                (
                    f"Created clip "
                    f"{part_number}/{total_clips}"
                )
            )

    return (
        output_files,
        total_clips
    )


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

    st.subheader(
        "📘 Facebook Settings"
    )

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

    st.subheader(
        "🎞️ Video Settings"
    )

    # --------------------------------------------------------
    # CLIP DURATION
    # --------------------------------------------------------

    clip_duration = st.number_input(
        "Clip Duration (seconds)",
        min_value=15,
        max_value=1200,
        value=30,
        step=5,
        help=(
            "Maximum duration is "
            "1200 seconds (20 minutes)."
        )
    )

    minutes = int(
        clip_duration // 60
    )

    seconds = int(
        clip_duration % 60
    )

    st.caption(
        f"⏱️ Selected: "
        f"{minutes} min {seconds} sec"
    )

    # --------------------------------------------------------
    # EPISODE
    # --------------------------------------------------------

    episode_number = st.number_input(
        "Episode Number",
        min_value=1,
        max_value=9999,
        value=1,
        step=1,
        help=(
            "This number will appear "
            "inside every generated clip."
        )
    )

    st.caption(
        f"📺 Episode: EP "
        f"{int(episode_number):02d}"
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
            "Trending uses subtle zoom, "
            "motion and fade effects."
        )
    )

    # --------------------------------------------------------
    # INDIVIDUAL EFFECTS
    # --------------------------------------------------------

    st.markdown(
        "**✨ Effects**"
    )

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
    # PREVIEW
    # --------------------------------------------------------

    st.caption(
        "📍 Top Right → Video File Name"
    )

    st.caption(
        "📍 Bottom Right → EP XX • PART XX/XX"
    )

    st.caption(
        "✨ Trending → Smooth Zoom + Motion + Fade"
    )

    st.divider()

    st.subheader(
        "📂 Selected Destination"
    )

    st.info(
        f"**Facebook:** {posting_destination}\n\n"
        f"**OneDrive:** {selected_pending_folder}\n\n"
        f"**Workflow:** {selected_github_workflow}"
    )


# ============================================================
# UPLOAD VIDEO
# ============================================================

st.header(
    "📤 Upload Video"
)

uploaded_file = st.file_uploader(
    "Choose a video",
    type=[
        "mp4",
        "mov",
        "mkv",
        "avi",
        "webm"
    ],
    help=(
        "Select the video that you want "
        "to split into clips."
    )
)


# ============================================================
# VIDEO UPLOAD / RECEIVE STATUS
# ============================================================

if uploaded_file:

    file_size_mb = (
        uploaded_file.size /
        (1024 * 1024)
    )

    st.info(
        f"🎬 **Selected Video:** "
        f"{uploaded_file.name}\n\n"
        f"📦 **Size:** "
        f"{file_size_mb:.2f} MB"
    )

    st.markdown(
        "**📥 Video Upload Status**"
    )

    upload_received_progress = st.progress(
        1.0
    )

    st.markdown(
        '<div class="progress-text">'
        '✅ Video received by Streamlit — 100%'
        '</div>',
        unsafe_allow_html=True
    )

    st.caption(
        "Note: Streamlit's native file uploader "
        "does not expose the browser's live upload "
        "percentage. The progress above confirms "
        "that the selected file has been received."
    )

    if file_size_mb > MAX_UPLOAD_SIZE_MB:

        st.error(
            f"File is too large. Maximum allowed "
            f"size is {MAX_UPLOAD_SIZE_MB} MB."
        )

        st.stop()


# ============================================================
# ONEDRIVE CONNECTION
# ============================================================

st.divider()

st.header(
    "☁️ OneDrive"
)

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

            token = (
                get_application_access_token()
            )

            get_user_drive(
                token
            )

            st.session_state.graph_token = (
                token
            )

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

    st.header(
        "🎬 Video Processing"
    )

    file_size_mb = (
        uploaded_file.size /
        (1024 * 1024)
    )

    st.info(
        f"File: {uploaded_file.name}  \n"
        f"Size: {file_size_mb:.2f} MB"
    )

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
            # RESET PREVIOUS DOWNLOAD
            # ------------------------------------------------

            st.session_state.download_zip = None

            st.session_state.download_zip_name = None

            st.session_state.generated_clip_names = []

            st.session_state.generated_total_clips = 0

            st.session_state.processing_complete = False

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
            # SAVE UPLOADED VIDEO
            # ------------------------------------------------

            st.subheader(
                "📥 Saving Uploaded Video"
            )

            save_progress = st.progress(
                0.0
            )

            save_status = st.empty()

            total_file_size = (
                uploaded_file.size
            )

            uploaded_file.seek(
                0
            )

            written_bytes = 0

            with open(
                input_path,
                "wb"
            ) as file:

                while True:

                    chunk = uploaded_file.read(
                        1024 * 1024
                    )

                    if not chunk:
                        break

                    file.write(
                        chunk
                    )

                    written_bytes += len(
                        chunk
                    )

                    if total_file_size > 0:

                        current_progress = (
                            written_bytes /
                            total_file_size
                        )

                    else:

                        current_progress = 1.0

                    current_progress = min(
                        current_progress,
                        1.0
                    )

                    save_progress.progress(
                        current_progress
                    )

                    save_status.markdown(
                        f"**📥 Saving video: "
                        f"{current_progress * 100:.1f}%** "
                        f"— "
                        f"{written_bytes / (1024 * 1024):.2f} / "
                        f"{total_file_size / (1024 * 1024):.2f} MB"
                    )

            save_progress.progress(
                1.0
            )

            save_status.success(
                "✅ Video successfully received and saved — 100%"
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
                original_duration /
                60
            )

            st.info(
                f"Original video duration: "
                f"{original_minutes:.2f} minutes"
            )

            # ------------------------------------------------
            # SPLIT PROGRESS
            # ------------------------------------------------

            st.subheader(
                "✂️ Splitting Video"
            )

            split_progress = st.progress(
                0.0
            )

            split_status = st.empty()

            split_count_status = st.empty()

            def split_progress_callback(
                current,
                total,
                message
            ):

                if total > 0:

                    percentage = (
                        current /
                        total
                    )

                else:

                    percentage = 0

                percentage = min(
                    max(
                        percentage,
                        0
                    ),
                    1
                )

                split_progress.progress(
                    percentage
                )

                split_status.markdown(
                    f"**✂️ {message} — "
                    f"{percentage * 100:.0f}%**"
                )

                split_count_status.write(
                    f"🎞️ Clips created: "
                    f"{current}/{total}"
                )

            # ------------------------------------------------
            # SPLIT
            # ------------------------------------------------

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
                enable_text=enable_text,
                progress_callback=split_progress_callback
            )

            split_progress.progress(
                1.0
            )

            split_status.success(
                f"✅ Video splitting completed — 100%"
            )

            split_count_status.success(
                f"🎉 {total_clips} clip(s) created successfully."
            )

            # ------------------------------------------------
            # SAVE CLIP INFO IN SESSION
            # ------------------------------------------------

            st.session_state.generated_total_clips = (
                total_clips
            )

            st.session_state.generated_clip_names = [
                os.path.basename(clip)
                for clip in clips
            ]

            # ------------------------------------------------
            # CREATE LOCAL ZIP
            # ------------------------------------------------

            with st.spinner(
                "Preparing local download..."
            ):

                (
                    zip_bytes,
                    zip_name
                ) = create_local_zip(
                    clips,
                    uploaded_file.name
                )

            st.session_state.download_zip = (
                zip_bytes
            )

            st.session_state.download_zip_name = (
                zip_name
            )

            # ------------------------------------------------
            # SHOW CLIPS
            # ------------------------------------------------

            st.subheader(
                "📦 Generated Clips"
            )

            for clip in clips:

                clip_size = (
                    os.path.getsize(
                        clip
                    ) /
                    (1024 * 1024)
                )

                st.write(
                    f"🎞️ "
                    f"{os.path.basename(clip)} "
                    f"— "
                    f"{clip_size:.2f} MB"
                )

            # ------------------------------------------------
            # LOCAL DOWNLOAD
            # ------------------------------------------------

            st.subheader(
                "💾 Local Storage"
            )

            st.success(
                f"✅ {total_clips} clip(s) are ready "
                f"for local download."
            )

            download_col1, download_col2 = st.columns(
                [2, 1]
            )

            with download_col1:

                st.download_button(
                    label=(
                        "💾 Download All Clips "
                        "to Local Storage"
                    ),
                    data=st.session_state.download_zip,
                    file_name=st.session_state.download_zip_name,
                    mime="application/zip",
                    use_container_width=True
                )

            with download_col2:

                st.metric(
                    "Total Clips",
                    total_clips
                )

            st.caption(
                "The browser will download the ZIP "
                "to your normal Downloads folder."
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

            folder_id = folder.get(
                "id"
            )

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
                f"{Path(uploaded_file.name).stem}"
                f"_metadata.json"
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
            # ONEDRIVE UPLOAD
            # ------------------------------------------------

            st.subheader(
                "☁️ Uploading to OneDrive"
            )

            overall_upload_progress = st.progress(
                0.0
            )

            upload_status = st.empty()

            upload_count_status = st.empty()

            total_uploads = len(
                clips
            )

            for index, clip in enumerate(
                clips,
                start=1
            ):

                clip_name = os.path.basename(
                    clip
                )

                clip_progress = st.progress(
                    0.0
                )

                clip_status = st.empty()

                def upload_progress_callback(
                    uploaded_bytes,
                    total_bytes,
                    current_index=index,
                    current_name=clip_name
                ):

                    if total_bytes > 0:

                        clip_percentage = (
                            uploaded_bytes /
                            total_bytes
                        )

                    else:

                        clip_percentage = 0

                    clip_percentage = min(
                        max(
                            clip_percentage,
                            0
                        ),
                        1
                    )

                    overall_percentage = (
                        (
                            current_index - 1
                        ) +
                        clip_percentage
                    ) / total_uploads

                    clip_progress.progress(
                        clip_percentage
                    )

                    clip_status.markdown(
                        f"**☁️ {current_name}: "
                        f"{clip_percentage * 100:.1f}%**"
                    )

                    overall_upload_progress.progress(
                        overall_percentage
                    )

                    upload_status.markdown(
                        f"**Overall OneDrive Upload: "
                        f"{overall_percentage * 100:.1f}%**"
                    )

                    upload_count_status.write(
                        f"📤 Uploaded: "
                        f"{current_index - 1}/"
                        f"{total_uploads} complete"
                    )

                upload_file_to_onedrive(
                    token=token,
                    folder_id=folder_id,
                    file_path=clip,
                    file_name=clip_name,
                    progress_callback=upload_progress_callback
                )

                clip_progress.progress(
                    1.0
                )

                clip_status.success(
                    f"✅ {clip_name} uploaded — 100%"
                )

                overall_upload_progress.progress(
                    index /
                    total_uploads
                )

                upload_status.markdown(
                    f"**Overall OneDrive Upload: "
                    f"{(index / total_uploads) * 100:.0f}%**"
                )

                upload_count_status.write(
                    f"📤 Uploaded: "
                    f"{index}/"
                    f"{total_uploads} complete"
                )

            # ------------------------------------------------
            # UPLOAD METADATA
            # ------------------------------------------------

            metadata_progress = st.progress(
                0.0
            )

            metadata_status = st.empty()

            metadata_status.markdown(
                "**📄 Uploading metadata...**"
            )

            upload_file_to_onedrive(
                token=token,
                folder_id=folder_id,
                file_path=metadata_path,
                file_name=os.path.basename(
                    metadata_path
                ),
                progress_callback=lambda current, total: (
                    metadata_progress.progress(
                        min(
                            current / total
                            if total > 0
                            else 0,
                            1.0
                        )
                    )
                )
            )

            metadata_progress.progress(
                1.0
            )

            metadata_status.success(
                "✅ Metadata uploaded successfully — 100%"
            )

            # ------------------------------------------------
            # FINAL SUCCESS
            # ------------------------------------------------

            overall_upload_progress.progress(
                1.0
            )

            upload_status.success(
                "🎉 All clips uploaded to OneDrive — 100%"
            )

            upload_count_status.success(
                f"✅ {total_uploads}/{total_uploads} "
                f"clips uploaded successfully."
            )

            st.success(
                f"🎉 All clips uploaded to "
                f"`{selected_pending_folder}`"
            )

            st.info(
                "Videos are now available for the "
                "Facebook Auto Poster."
            )

            # ------------------------------------------------
            # FINAL LOCAL DOWNLOAD BUTTON
            # ------------------------------------------------

            st.divider()

            st.subheader(
                "💾 Download to Local Storage"
            )

            st.success(
                "Your clips are also ready to download "
                "to your computer."
            )

            st.download_button(
                label=(
                    "💾 Download All Clips "
                    "to Local Storage"
                ),
                data=st.session_state.download_zip,
                file_name=st.session_state.download_zip_name,
                mime="application/zip",
                use_container_width=True
            )

            st.session_state.processing_complete = True

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
# SHOW LOCAL DOWNLOAD AFTER RERUN
# ============================================================

if (
    st.session_state.download_zip
    and st.session_state.download_zip_name
):

    st.divider()

    st.subheader(
        "💾 Local Download"
    )

    st.download_button(
        label=(
            "💾 Download All Generated Clips"
        ),
        data=st.session_state.download_zip,
        file_name=st.session_state.download_zip_name,
        mime="application/zip",
        use_container_width=True,
        key="persistent_local_download"
    )

    st.caption(
        f"📦 {st.session_state.generated_total_clips} "
        f"clip(s) ready — "
        f"{st.session_state.download_zip_name}"
    )


# ============================================================
# FACEBOOK AUTO POSTER BUTTON
# ============================================================

st.divider()

st.header(
    "📢 Facebook Auto Poster"
)

st.info(
    f"Selected Page: **{posting_destination}**\n\n"
    f"OneDrive Folder: **{selected_pending_folder}**\n\n"
    f"GitHub Workflow: **{selected_github_workflow}**"
)


# ============================================================
# FACEBOOK POST BUTTON
# ============================================================

run_poster = st.button(
    "▶️ Run Facebook Auto Poster",
    type="primary",
    use_container_width=True
)


if run_poster:

    try:

        with st.spinner(
            f"Starting {posting_destination} "
            f"Facebook Auto Poster..."
        ):

            run_github_auto_poster(
                selected_github_workflow
            )

        st.success(
            "🚀 Facebook Auto Poster started successfully!"
        )

        st.success(
            f"📘 Destination: "
            f"{posting_destination}"
        )

        st.info(
            "GitHub Actions will now pick the next "
            "pending video and post it to Facebook."
        )

    except Exception as e:

        st.error(
            "❌ Facebook Auto Poster could not "
            f"be started: {e}"
        )


# ============================================================
# FOOTER
# ============================================================

st.divider()

st.caption(
    "🎬 Pro Video Studio | "
    "Streamlit + OneDrive + GitHub Actions"
)
