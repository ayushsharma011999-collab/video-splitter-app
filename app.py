import os
import io
import json
import math
import shutil
import zipfile
import tempfile
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import quote

import requests
import streamlit as st


# ============================================================
# APP CONFIG
# ============================================================

st.set_page_config(
    page_title="Pro Video Studio",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded",
)

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"

ONEDRIVE_USER = "my@011999.onmicrosoft.com"

CHUNK_SIZE = 5 * 1024 * 1024

DEFAULT_REPO = "ayushsharma011999-collab/video-splitter-app"

FOLDER_OPTIONS = {
    "Smart Deals India": "Pending_Posts",
    "Movies and web series": "Movies_Pending_Posts",
}

WORKFLOW_OPTIONS = {
    "Smart Deals India": "main.yml",
    "Movies and web series": "facebook-auto-poster-movies.yml",
}


# ============================================================
# CSS
# ============================================================

st.markdown(
    """
    <style>
    .main-title {
        font-size: 2.3rem;
        font-weight: 800;
        margin-bottom: 0.1rem;
    }

    .sub-title {
        color: #777;
        margin-bottom: 1.5rem;
    }

    .status-card {
        padding: 14px;
        border-radius: 12px;
        border: 1px solid rgba(128,128,128,.25);
        margin-bottom: 10px;
    }

    .small-muted {
        color: #777;
        font-size: 0.88rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# SESSION STATE
# ============================================================

DEFAULT_STATE = {
    "onedrive_connected": False,
    "onedrive_drive_id": None,
    "onedrive_folder_id": None,
    "onedrive_folder_name": None,

    "job_dir": None,
    "input_path": None,
    "clips": [],
    "source_name": None,
    "metadata": None,

    "download_zip": None,
    "download_zip_name": None,

    "last_destination_name": None,
    "last_destination_folder": None,

    "split_complete": False,
    "upload_complete": False,
}

for key, value in DEFAULT_STATE.items():
    if key not in st.session_state:
        st.session_state[key] = value


# ============================================================
# GENERAL HELPERS
# ============================================================

def get_secret(name, default=None):
    try:
        value = st.secrets[name]

        if value is not None and str(value).strip():
            return str(value).strip()

    except Exception:
        pass

    value = os.getenv(name)

    if value is not None and value.strip():
        return value.strip()

    return default


def safe_filename(name):
    name = Path(name).name

    invalid_chars = '<>:"/\\|?*'

    for char in invalid_chars:
        name = name.replace(char, "_")

    name = name.strip().strip(".")

    if not name:
        return "video"

    return name


def ffmpeg_escape_text(text):
    text = str(text)

    replacements = [
        ("\\", r"\\"),
        (":", r"\:"),
        ("'", r"\'"),
        ("%", r"\%"),
        (",", r"\,"),
        ("[", r"\["),
        ("]", r"\]"),
        (";", r"\;"),
    ]

    for old, new in replacements:
        text = text.replace(old, new)

    return text


def run_command(command, timeout=3600):
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )

    except FileNotFoundError as exc:
        raise RuntimeError(
            "FFmpeg/FFprobe is not installed or is not available in PATH."
        ) from exc

    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            "FFmpeg processing timed out."
        ) from exc

    if result.returncode != 0:
        error_text = result.stderr.strip()

        if len(error_text) > 6000:
            error_text = error_text[-6000:]

        raise RuntimeError(
            error_text or "FFmpeg returned an error."
        )

    return result


def get_video_duration(video_path):
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        video_path,
    ]

    result = run_command(
        command,
        timeout=120,
    )

    try:
        duration = float(
            result.stdout.strip()
        )

    except ValueError as exc:
        raise RuntimeError(
            "Could not read video duration."
        ) from exc

    if duration <= 0:
        raise RuntimeError(
            "Video duration is invalid."
        )

    return duration


def find_font():
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]

    for font_path in candidates:
        if os.path.exists(font_path):
            return font_path

    return None


# ============================================================
# VISUAL PROCESSING
# ============================================================

def build_visual_filter(
    aspect_ratio,
    zoom_level,
    motion_pixels,
):
    filters = []

    # --------------------------------------------------------
    # ASPECT RATIO
    # --------------------------------------------------------

    if aspect_ratio == "9:16":

        filters.append(
            "scale="
            "if(gt(a,9/16),-2,ih*9/16):"
            "if(gt(a,9/16),ih,-2)"
        )

        filters.append(
            "crop="
            "ih*9/16:ih:"
            "(iw-ih*9/16)/2:0"
        )

    elif aspect_ratio == "16:9":

        filters.append(
            "scale="
            "if(gt(a,16/9),-2,ih*16/9):"
            "if(gt(a,16/9),ih,-2)"
        )

        filters.append(
            "crop="
            "iw:ih*0+iw*9/16:"
            "0:(ih-iw*9/16)/2"
        )

    elif aspect_ratio == "1:1":

        filters.append(
            "crop="
            "min(iw\\,ih):"
            "min(iw\\,ih):"
            "(iw-min(iw\\,ih))/2:"
            "(ih-min(iw\\,ih))/2"
        )

    # --------------------------------------------------------
    # ZOOM
    # --------------------------------------------------------

    if zoom_level > 1.0:

        zoom = f"{zoom_level:.4f}"

        filters.append(
            f"scale="
            f"iw*{zoom}:"
            f"ih*{zoom}"
        )

        filters.append(
            f"crop="
            f"iw/{zoom}:"
            f"ih/{zoom}:"
            f"(iw-iw/{zoom})/2:"
            f"(ih-ih/{zoom})/2"
        )

    # --------------------------------------------------------
    # MOTION
    # --------------------------------------------------------

    if motion_pixels > 0:

        mp = float(motion_pixels)

        crop_width = f"iw-{2 * mp:g}"

        x_expression = (
            f"{mp:g}+"
            f"{mp:g}*sin(n/30)"
        )

        filters.append(
            f"crop="
            f"{crop_width}:"
            f"ih:"
            f"x='{x_expression}':"
            "y=0"
        )

    return ",".join(filters)


# ============================================================
# DRAW TEXT
# ============================================================

def make_episode_overlay(
    episode_number,
    part_number,
    total_parts,
    font_path,
):
    text = (
        f"EP {episode_number} "
        f"• PART {part_number}/{total_parts}"
    )

    text = ffmpeg_escape_text(text)

    drawtext = (
        f"drawtext="
        f"text='{text}':"
        f"x=(w-text_w)/2:"
        f"y=h*0.08:"
        f"fontsize=h*0.035:"
        f"fontcolor=white:"
        f"bordercolor=black:"
        f"borderw=2:"
        f"shadowx=2:"
        f"shadowy=2"
    )

    if font_path:
        font_value = ffmpeg_escape_text(
            font_path
        )

        drawtext += (
            f":fontfile='{font_value}'"
        )

    return drawtext


def make_watermark(
    watermark_text,
    font_path,
):
    text = ffmpeg_escape_text(
        watermark_text.strip()
    )

    drawtext = (
        f"drawtext="
        f"text='{text}':"
        f"x=w-text_w-w*0.03:"
        f"y=h-text_h-h*0.03:"
        f"fontsize=h*0.026:"
        f"fontcolor=white@0.75:"
        f"bordercolor=black@0.5:"
        f"borderw=1"
    )

    if font_path:
        font_value = ffmpeg_escape_text(
            font_path
        )

        drawtext += (
            f":fontfile='{font_value}'"
        )

    return drawtext


# ============================================================
# VIDEO SPLITTER
# ============================================================

def split_video(
    input_path,
    output_dir,
    clip_duration,
    aspect_ratio,
    zoom_level,
    motion_pixels,
    fade_enabled,
    watermark_text,
    watermark_enabled,
    episode_number,
    progress_callback=None,
):
    os.makedirs(
        output_dir,
        exist_ok=True,
    )

    duration = get_video_duration(
        input_path
    )

    clip_duration = float(
        clip_duration
    )

    total_parts = max(
        1,
        math.ceil(
            duration / clip_duration
        ),
    )

    font_path = find_font()

    visual_filter = build_visual_filter(
        aspect_ratio=aspect_ratio,
        zoom_level=zoom_level,
        motion_pixels=motion_pixels,
    )

    clips = []

    for part_number in range(
        1,
        total_parts + 1,
    ):
        start_time = (
            (part_number - 1)
            * clip_duration
        )

        remaining = (
            duration - start_time
        )

        current_duration = min(
            clip_duration,
            remaining,
        )

        if current_duration <= 0:
            break

        output_path = os.path.join(
            output_dir,
            f"clip_{part_number:03d}.mp4",
        )

        filters = []

        if visual_filter:
            filters.append(
                visual_filter
            )

        # ----------------------------------------------------
        # EP / PART OVERLAY
        # ----------------------------------------------------

        filters.append(
            make_episode_overlay(
                episode_number=int(
                    episode_number
                ),
                part_number=part_number,
                total_parts=total_parts,
                font_path=font_path,
            )
        )

        # ----------------------------------------------------
        # WATERMARK
        # ----------------------------------------------------

        if (
            watermark_enabled
            and watermark_text
            and watermark_text.strip()
        ):
            filters.append(
                make_watermark(
                    watermark_text,
                    font_path,
                )
            )

        # ----------------------------------------------------
        # BUILD COMMAND
        # ----------------------------------------------------

        command = [
            "ffmpeg",
            "-y",
            "-ss",
            f"{start_time:.3f}",
            "-i",
            input_path,
            "-t",
            f"{current_duration:.3f}",
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
        ]

        # Fade is intentionally applied as a
        # separate video filter only when enabled.
        if fade_enabled:

            fade_in = (
                "fade=t=in:"
                "st=0:"
                "d=0.35"
            )

            fade_out_start = max(
                0,
                current_duration - 0.35,
            )

            fade_out = (
                "fade=t=out:"
                f"st={fade_out_start:.3f}:"
                "d=0.35"
            )

            filters.append(
                fade_in
            )

            filters.append(
                fade_out
            )

        filter_string = ",".join(
            filters
        )

        command.extend(
            [
                "-vf",
                filter_string,
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "23",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-movflags",
                "+faststart",
                output_path,
            ]
        )

        try:
            run_command(
                command,
                timeout=3600,
            )

        except Exception as exc:
            raise RuntimeError(
                f"Error while creating "
                f"PART {part_number}/{total_parts}:\n\n"
                f"{exc}"
            ) from exc

        if not os.path.exists(
            output_path
        ):
            raise RuntimeError(
                f"PART {part_number} was not created."
            )

        file_size = os.path.getsize(
            output_path
        )

        if file_size <= 0:
            raise RuntimeError(
                f"PART {part_number} is empty."
            )

        clips.append(
            output_path
        )

        if progress_callback:
            progress_callback(
                part_number / total_parts
            )

    if not clips:
        raise RuntimeError(
            "No clips were generated."
        )

    return clips


# ============================================================
# ZIP
# ============================================================

def create_zip(
    clips,
    source_name,
):
    zip_buffer = io.BytesIO()

    zip_base = Path(
        source_name
    ).stem

    zip_name = (
        f"{safe_filename(zip_base)}_clips.zip"
    )

    with zipfile.ZipFile(
        zip_buffer,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=1,
    ) as zip_file:

        for clip in clips:

            zip_file.write(
                clip,
                arcname=os.path.basename(
                    clip
                ),
            )

    zip_buffer.seek(0)

    return (
        zip_name,
        zip_buffer.getvalue(),
    )


# ============================================================
# ONEDRIVE / GRAPH
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
        raise RuntimeError(
            "AZURE_TENANT_ID is missing."
        )

    if not client_id:
        raise RuntimeError(
            "AZURE_CLIENT_ID is missing."
        )

    if not client_secret:
        raise RuntimeError(
            "AZURE_CLIENT_SECRET is missing."
        )

    token_url = (
        "https://login.microsoftonline.com/"
        f"{quote(tenant_id, safe='')}"
        "/oauth2/v2.0/token"
    )

    response = requests.post(
        token_url,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": GRAPH_SCOPE,
            "grant_type": "client_credentials",
        },
        timeout=60,
    )

    if response.status_code != 200:
        raise RuntimeError(
            "Microsoft token request failed "
            f"({response.status_code}):\n"
            f"{response.text[:3000]}"
        )

    data = response.json()

    token = data.get(
        "access_token"
    )

    if not token:
        raise RuntimeError(
            "Microsoft did not return an access token."
        )

    return token


def graph_headers(token):
    return {
        "Authorization": (
            f"Bearer {token}"
        )
    }


def get_drive(token):
    url = (
        f"{GRAPH_BASE_URL}/users/"
        f"{quote(ONEDRIVE_USER, safe='')}"
        "/drive"
    )

    response = requests.get(
        url,
        headers=graph_headers(token),
        timeout=60,
    )

    if response.status_code != 200:
        raise RuntimeError(
            "Could not access OneDrive "
            f"({response.status_code}):\n"
            f"{response.text[:3000]}"
        )

    return response.json()


def get_folder(
    token,
    drive_id,
    folder_name,
):
    url = (
        f"{GRAPH_BASE_URL}/drives/"
        f"{quote(drive_id, safe='')}"
        "/root:/"
        f"{quote(folder_name, safe='')}"
    )

    response = requests.get(
        url,
        headers=graph_headers(token),
        timeout=60,
    )

    if response.status_code == 200:
        return response.json()

    if response.status_code == 404:
        return None

    raise RuntimeError(
        "Could not check OneDrive folder "
        f"({response.status_code}):\n"
        f"{response.text[:3000]}"
    )


def create_folder(
    token,
    drive_id,
    folder_name,
):
    url = (
        f"{GRAPH_BASE_URL}/drives/"
        f"{quote(drive_id, safe='')}"
        "/root/children"
    )

    response = requests.post(
        url,
        headers={
            **graph_headers(token),
            "Content-Type": "application/json",
        },
        json={
            "name": folder_name,
            "folder": {},
            "@microsoft.graph.conflictBehavior": "fail",
        },
        timeout=60,
    )

    if response.status_code in (
        200,
        201,
    ):
        return response.json()

    if response.status_code == 409:

        existing = get_folder(
            token,
            drive_id,
            folder_name,
        )

        if existing:
            return existing

    raise RuntimeError(
        "Could not create OneDrive folder "
        f"({response.status_code}):\n"
        f"{response.text[:3000]}"
    )


def connect_onedrive(
    folder_name,
):
    token = get_application_access_token()

    drive = get_drive(
        token
    )

    folder = get_folder(
        token,
        drive["id"],
        folder_name,
    )

    if folder is None:
        folder = create_folder(
            token,
            drive["id"],
            folder_name,
        )

    st.session_state.onedrive_connected = True
    st.session_state.onedrive_drive_id = (
        drive["id"]
    )
    st.session_state.onedrive_folder_id = (
        folder["id"]
    )
    st.session_state.onedrive_folder_name = (
        folder_name
    )

    return (
        token,
        drive,
        folder,
    )


def upload_small_file(
    token,
    drive_id,
    folder_id,
    file_path,
):
    filename = os.path.basename(
        file_path
    )

    url = (
        f"{GRAPH_BASE_URL}/drives/"
        f"{quote(drive_id, safe='')}"
        "/items/"
        f"{quote(folder_id, safe='')}:"
        f"/{quote(filename, safe='')}"
        ":/content"
    )

    with open(
        file_path,
        "rb",
    ) as file_handle:

        response = requests.put(
            url,
            headers={
                **graph_headers(token),
                "Content-Type": "video/mp4",
            },
            data=file_handle,
            timeout=600,
        )

    if response.status_code not in (
        200,
        201,
    ):
        raise RuntimeError(
            "OneDrive upload failed "
            f"({response.status_code}):\n"
            f"{response.text[:3000]}"
        )

    return response.json()


def upload_large_file(
    token,
    drive_id,
    folder_id,
    file_path,
    progress_callback=None,
):
    filename = os.path.basename(
        file_path
    )

    create_url = (
        f"{GRAPH_BASE_URL}/drives/"
        f"{quote(drive_id, safe='')}"
        "/items/"
        f"{quote(folder_id, safe='')}:"
        f"/{quote(filename, safe='')}"
        ":/createUploadSession"
    )

    session_response = requests.post(
        create_url,
        headers={
            **graph_headers(token),
            "Content-Type": "application/json",
        },
        json={
            "item": {
                "@microsoft.graph.conflictBehavior": "replace",
                "name": filename,
            }
        },
        timeout=60,
    )

    if session_response.status_code not in (
        200,
        201,
    ):
        raise RuntimeError(
            "Could not create OneDrive upload session "
            f"({session_response.status_code}):\n"
            f"{session_response.text[:3000]}"
        )

    upload_url = (
        session_response
        .json()
        .get("uploadUrl")
    )

    if not upload_url:
        raise RuntimeError(
            "OneDrive did not return uploadUrl."
        )

    total_size = os.path.getsize(
        file_path
    )

    uploaded = 0

    with open(
        file_path,
        "rb",
    ) as file_handle:

        while uploaded < total_size:

            chunk = file_handle.read(
                CHUNK_SIZE
            )

            if not chunk:
                break

            start = uploaded

            end = (
                uploaded
                + len(chunk)
                - 1
            )

            response = requests.put(
                upload_url,
                headers={
                    "Content-Length": str(
                        len(chunk)
                    ),
                    "Content-Range": (
                        f"bytes "
                        f"{start}-{end}/"
                        f"{total_size}"
                    ),
                },
                data=chunk,
                timeout=600,
            )

            if response.status_code not in (
                200,
                201,
                202,
            ):
                raise RuntimeError(
                    "OneDrive chunk upload failed "
                    f"({response.status_code}):\n"
                    f"{response.text[:3000]}"
                )

            uploaded = end + 1

            if progress_callback:
                progress_callback(
                    uploaded / total_size
                )

    if uploaded != total_size:
        raise RuntimeError(
            "OneDrive upload incomplete: "
            f"{uploaded} / {total_size} bytes."
        )

    if response.status_code in (
        200,
        201,
    ):
        return response.json()

    return {
        "name": filename,
        "size": total_size,
    }


def upload_file_to_onedrive(
    token,
    drive_id,
    folder_id,
    file_path,
    progress_callback=None,
):
    file_size = os.path.getsize(
        file_path
    )

    # Small file
    if file_size <= 4 * 1024 * 1024:

        if progress_callback:
            progress_callback(0.2)

        result = upload_small_file(
            token,
            drive_id,
            folder_id,
            file_path,
        )

        if progress_callback:
            progress_callback(1.0)

        return result

    # Large file
    return upload_large_file(
        token,
        drive_id,
        folder_id,
        file_path,
        progress_callback,
    )


# ============================================================
# GITHUB ACTIONS
# ============================================================

def dispatch_github_workflow(
    page_name,
):
    github_token = get_secret(
        "GITHUB_TOKEN"
    )

    repo = get_secret(
        "GITHUB_REPO",
        DEFAULT_REPO,
    )

    if not github_token:
        raise RuntimeError(
            "GITHUB_TOKEN is missing "
            "from Streamlit Secrets."
        )

    workflow_file = (
        WORKFLOW_OPTIONS[
            page_name
        ]
    )

    url = (
        "https://api.github.com/repos/"
        f"{repo}/actions/workflows/"
        f"{quote(workflow_file, safe='')}"
        "/dispatches"
    )

    response = requests.post(
        url,
        headers={
            "Authorization": (
                f"Bearer {github_token}"
            ),
            "Accept": (
                "application/vnd.github+json"
            ),
            "X-GitHub-Api-Version": (
                "2022-11-28"
            ),
        },
        json={
            "ref": "main"
        },
        timeout=60,
    )

    if response.status_code != 204:
        raise RuntimeError(
            "GitHub workflow dispatch failed "
            f"({response.status_code}):\n"
            f"{response.text[:3000]}"
        )

    return True


# ============================================================
# CLEAN OLD JOB
# ============================================================

def cleanup_previous_job():
    old_job = st.session_state.get(
        "job_dir"
    )

    if old_job and os.path.exists(
        old_job
    ):
        try:
            shutil.rmtree(
                old_job,
                ignore_errors=True,
            )
        except Exception:
            pass

    st.session_state.job_dir = None
    st.session_state.input_path = None
    st.session_state.clips = []
    st.session_state.source_name = None
    st.session_state.metadata = None
    st.session_state.download_zip = None
    st.session_state.download_zip_name = None
    st.session_state.split_complete = False
    st.session_state.upload_complete = False


# ============================================================
# HEADER
# ============================================================

st.markdown(
    '<div class="main-title">'
    "🎬 Pro Video Studio"
    "</div>",
    unsafe_allow_html=True,
)

st.markdown(
    '<div class="sub-title">'
    "Video Splitter + OneDrive Pending Posts"
    "</div>",
    unsafe_allow_html=True,
)


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:

    st.header("⚙️ Settings")

    # --------------------------------------------------------
    # VIDEO SETTINGS
    # --------------------------------------------------------

    st.subheader("🎞️ Video Settings")

    clip_duration = st.number_input(
        "Clip Duration (seconds)",
        min_value=5,
        max_value=600,
        value=30,
        step=5,
    )

    aspect_ratio = st.selectbox(
        "Aspect Ratio",
        [
            "Original",
            "9:16",
            "16:9",
            "1:1",
        ],
    )

    # --------------------------------------------------------
    # VISUAL PROCESSING
    # --------------------------------------------------------

    st.subheader(
        "🎨 Visual Processing"
    )

    zoom_option = st.selectbox(
        "Zoom",
        [
            "Off",
            "Light",
            "Medium",
            "Strong",
        ],
    )

    zoom_values = {
        "Off": 1.0,
        "Light": 1.015,
        "Medium": 1.022,
        "Strong": 1.035,
    }

    zoom_level = zoom_values[
        zoom_option
    ]

    motion_option = st.selectbox(
        "Motion",
        [
            "Off",
            "Light",
            "Medium",
            "Strong",
        ],
    )

    motion_values = {
        "Off": 0,
        "Light": 2,
        "Medium": 4,
        "Strong": 6,
    }

    motion_pixels = motion_values[
        motion_option
    ]

    fade_enabled = st.checkbox(
        "Fade In / Fade Out",
        value=False,
    )

    # --------------------------------------------------------
    # EPISODE
    # --------------------------------------------------------

    st.subheader(
        "🔢 Episode"
    )

    episode_number = st.number_input(
        "Episode Number",
        min_value=1,
        max_value=9999,
        value=1,
        step=1,
    )

    # --------------------------------------------------------
    # WATERMARK
    # --------------------------------------------------------

    st.subheader(
        "💧 Watermark"
    )

    watermark_enabled = st.checkbox(
        "Enable Watermark",
        value=False,
    )

    watermark_text = st.text_input(
        "Watermark Text",
        value="🎬 Pro Video Studio",
        disabled=not watermark_enabled,
    )

    # --------------------------------------------------------
    # ONEDRIVE
    # --------------------------------------------------------

    st.subheader(
        "☁️ OneDrive"
    )

    destination_name = st.selectbox(
        "Destination",
        list(
            FOLDER_OPTIONS.keys()
        ),
    )

    destination_folder = (
        FOLDER_OPTIONS[
            destination_name
        ]
    )

    if st.button(
        "🔌 Connect OneDrive",
        use_container_width=True,
    ):
        try:

            with st.spinner(
                "Connecting to OneDrive..."
            ):
                connect_onedrive(
                    destination_folder
                )

            st.success(
                "OneDrive Ready — "
                f"{destination_folder} "
                "folder active"
            )

        except Exception as exc:
            st.error(
                f"❌ {exc}"
            )

    if st.session_state.onedrive_connected:

        st.success(
            "✅ Connected: "
            f"{st.session_state.onedrive_folder_name}"
        )

    else:

        st.warning(
            "OneDrive not connected"
        )

    # --------------------------------------------------------
    # FACEBOOK
    # --------------------------------------------------------

    st.subheader(
        "📘 Facebook"
    )

    page_id = st.text_input(
        "Facebook Page ID",
        value="",
    )

    post_type = st.selectbox(
        "Post Type",
        [
            "Facebook Reel (Short)",
            "Normal Page Video Post",
        ],
    )

    caption = st.text_area(
        "Caption",
        value="",
        height=100,
    )


# ============================================================
# VIDEO UPLOADER
# ============================================================

uploaded_file = st.file_uploader(
    "📤 Upload Video",
    type=[
        "mp4",
        "mov",
        "mkv",
        "avi",
        "webm",
        "m4v",
    ],
)


# ============================================================
# NEW FILE DETECTION
# ============================================================

if uploaded_file is not None:

    current_name = uploaded_file.name

    previous_name = (
        st.session_state.get(
            "source_name"
        )
    )

    if (
        previous_name
        and previous_name != current_name
        and st.session_state.split_complete
    ):
        cleanup_previous_job()


# ============================================================
# UPLOADED FILE INFORMATION
# ============================================================

if uploaded_file is not None:

    st.success(
        f"Uploaded: **{uploaded_file.name}** "
        f"— "
        f"{uploaded_file.size / (1024 * 1024):.2f} MB"
    )

    split_button = st.button(
        "✂️ Split Video",
        type="primary",
        use_container_width=True,
    )

    # ========================================================
    # SPLIT
    # ========================================================

    if split_button:

        cleanup_previous_job()

        job_dir = tempfile.mkdtemp(
            prefix="pro_video_studio_"
        )

        input_filename = safe_filename(
            uploaded_file.name
        )

        input_path = os.path.join(
            job_dir,
            input_filename,
        )

        clips_dir = os.path.join(
            job_dir,
            "clips",
        )

        os.makedirs(
            clips_dir,
            exist_ok=True,
        )

        st.session_state.job_dir = (
            job_dir
        )

        st.session_state.input_path = (
            input_path
        )

        st.session_state.source_name = (
            uploaded_file.name
        )

        try:

            # ------------------------------------------------
            # SAVE INPUT
            # ------------------------------------------------

            save_progress = st.progress(
                0,
                text="Saving uploaded video..."
            )

            with open(
                input_path,
                "wb",
            ) as file_handle:

                file_handle.write(
                    uploaded_file.getbuffer()
                )

            save_progress.progress(
                1.0,
                text="Video saved"
            )

            # ------------------------------------------------
            # READ DURATION
            # ------------------------------------------------

            st.info(
                "🎬 Reading video information..."
            )

            duration = get_video_duration(
                input_path
            )

            total_parts = max(
                1,
                math.ceil(
                    duration
                    / float(
                        clip_duration
                    )
                ),
            )

            col1, col2, col3 = st.columns(3)

            with col1:
                st.metric(
                    "Duration",
                    f"{duration:.1f}s"
                )

            with col2:
                st.metric(
                    "Clip Duration",
                    f"{clip_duration}s"
                )

            with col3:
                st.metric(
                    "Total Clips",
                    total_parts
                )

            # ------------------------------------------------
            # SPLIT PROGRESS
            # ------------------------------------------------

            split_progress = st.progress(
                0,
                text="Starting video processing..."
            )

            split_status = st.empty()

            def update_split_progress(
                value
            ):
                value = max(
                    0.0,
                    min(
                        1.0,
                        float(value)
                    ),
                )

                percentage = int(
                    value * 100
                )

                split_progress.progress(
                    value,
                    text=(
                        "Processing video — "
                        f"{percentage}%"
                    ),
                )

                split_status.info(
                    "🎞️ Creating video parts..."
                )

            # ------------------------------------------------
            # RUN SPLITTER
            # ------------------------------------------------

            clips = split_video(
                input_path=input_path,
                output_dir=clips_dir,
                clip_duration=clip_duration,
                aspect_ratio=aspect_ratio,
                zoom_level=zoom_level,
                motion_pixels=motion_pixels,
                fade_enabled=fade_enabled,
                watermark_text=watermark_text,
                watermark_enabled=watermark_enabled,
                episode_number=int(
                    episode_number
                ),
                progress_callback=(
                    update_split_progress
                ),
            )

            split_progress.progress(
                1.0,
                text="Video processing complete"
            )

            split_status.success(
                f"✅ {len(clips)} clips created"
            )

            st.session_state.clips = clips
            st.session_state.split_complete = True

            # ------------------------------------------------
            # METADATA
            # ------------------------------------------------

            metadata = {
                "source_file": uploaded_file.name,
                "created_at_utc": (
                    datetime.now(
                        timezone.utc
                    ).isoformat()
                ),
                "duration_seconds": duration,
                "clip_duration_seconds": (
                    clip_duration
                ),
                "total_clips": len(clips),
                "aspect_ratio": aspect_ratio,
                "zoom_option": zoom_option,
                "zoom_level": zoom_level,
                "motion_option": motion_option,
                "motion_pixels": motion_pixels,
                "fade_enabled": fade_enabled,
                "episode_number": int(
                    episode_number
                ),
                "watermark_enabled": (
                    watermark_enabled
                ),
                "watermark_text": (
                    watermark_text
                    if watermark_enabled
                    else ""
                ),
                "destination_name": (
                    destination_name
                ),
                "destination_folder": (
                    destination_folder
                ),
                "facebook_page_id": page_id,
                "facebook_post_type": (
                    post_type
                ),
                "caption": caption,
            }

            st.session_state.metadata = (
                metadata
            )

            st.session_state.last_destination_name = (
                destination_name
            )

            st.session_state.last_destination_folder = (
                destination_folder
            )

            # ------------------------------------------------
            # CREATE ZIP
            # ------------------------------------------------

            zip_status = st.empty()

            zip_status.info(
                "📦 Creating ZIP file..."
            )

            zip_name, zip_bytes = create_zip(
                clips,
                uploaded_file.name,
            )

            st.session_state.download_zip = (
                zip_bytes
            )

            st.session_state.download_zip_name = (
                zip_name
            )

            zip_status.success(
                "✅ ZIP created successfully"
            )

        except Exception as exc:

            st.error(
                "❌ Video processing failed."
            )

            st.exception(
                exc
            )


# ============================================================
# GENERATED CLIPS SECTION
# ============================================================

if (
    st.session_state.split_complete
    and st.session_state.clips
):

    clips = st.session_state.clips

    st.divider()

    st.subheader(
        "🎞️ Generated Clips"
    )

    st.success(
        f"✅ {len(clips)} clips ready"
    )

    # --------------------------------------------------------
    # ZIP DOWNLOAD
    # --------------------------------------------------------

    if (
        st.session_state.download_zip
    ):

        st.download_button(
            "💾 Download All Clips (ZIP)",
            data=(
                st.session_state.download_zip
            ),
            file_name=(
                st.session_state.download_zip_name
            ),
            mime="application/zip",
            type="primary",
            use_container_width=True,
            key="download_all_zip",
        )

    # --------------------------------------------------------
    # INDIVIDUAL CLIPS
    # --------------------------------------------------------

    for index, clip in enumerate(
        clips,
        start=1,
    ):

        if not os.path.exists(
            clip
        ):
            st.warning(
                f"PART {index} file is no longer available."
            )
            continue

        file_size = (
            os.path.getsize(
                clip
            )
            / (1024 * 1024)
        )

        with st.container(
            border=True
        ):

            st.write(
                f"**PART {index}/{len(clips)}** "
                f"— "
                f"{os.path.basename(clip)} "
                f"— "
                f"{file_size:.2f} MB"
            )

            try:

                with open(
                    clip,
                    "rb",
                ) as clip_file:

                    clip_bytes = (
                        clip_file.read()
                    )

                st.video(
                    clip_bytes
                )

                st.download_button(
                    f"⬇️ Download Part {index}",
                    data=clip_bytes,
                    file_name=os.path.basename(
                        clip
                    ),
                    mime="video/mp4",
                    key=(
                        f"download_part_"
                        f"{index}_"
                        f"{st.session_state.source_name}"
                    ),
                )

            except Exception as exc:

                st.warning(
                    f"Could not preview PART "
                    f"{index}: {exc}"
                )


# ============================================================
# ONEDRIVE UPLOAD SECTION
# ============================================================

if (
    st.session_state.split_complete
    and st.session_state.clips
):

    st.divider()

    st.subheader(
        "☁️ OneDrive Upload"
    )

    current_folder = (
        st.session_state.last_destination_folder
        or destination_folder
    )

    st.write(
        f"Destination folder: "
        f"**{current_folder}**"
    )

    if not st.session_state.onedrive_connected:

        st.warning(
            "Please connect OneDrive from "
            "the sidebar before uploading."
        )

    else:

        if st.button(
            f"☁️ Upload {len(st.session_state.clips)} "
            "Clips to OneDrive",
            use_container_width=True,
        ):

            upload_progress = st.progress(
                0,
                text="Starting OneDrive upload..."
            )

            upload_status = st.empty()

            try:

                token = (
                    get_application_access_token()
                )

                drive_id = (
                    st.session_state
                    .onedrive_drive_id
                )

                folder_id = (
                    st.session_state
                    .onedrive_folder_id
                )

                clips = (
                    st.session_state.clips
                )

                total_uploads = len(
                    clips
                )

                for current_index, clip in enumerate(
                    clips,
                    start=1,
                ):

                    clip_name = os.path.basename(
                        clip
                    )

                    upload_status.info(
                        f"☁️ Uploading "
                        f"{clip_name} "
                        f"({current_index}/"
                        f"{total_uploads})"
                    )

                    def clip_progress(
                        value,
                        current_index=current_index,
                        total_uploads=total_uploads,
                    ):
                        value = max(
                            0.0,
                            min(
                                1.0,
                                float(value)
                            ),
                        )

                        # IMPORTANT:
                        # This is the corrected formula.
                        overall_percentage = (
                            (
                                (
                                    current_index
                                    - 1
                                )
                                + value
                            )
                            / total_uploads
                        )

                        upload_progress.progress(
                            overall_percentage,
                            text=(
                                f"Uploading "
                                f"{current_index}/"
                                f"{total_uploads} "
                                f"— "
                                f"{value * 100:.0f}%"
                            ),
                        )

                    upload_file_to_onedrive(
                        token=token,
                        drive_id=drive_id,
                        folder_id=folder_id,
                        file_path=clip,
                        progress_callback=(
                            clip_progress
                        ),
                    )

                    upload_progress.progress(
                        current_index
                        / total_uploads,
                        text=(
                            f"Uploaded "
                            f"{current_index}/"
                            f"{total_uploads}"
                        ),
                    )

                upload_progress.progress(
                    1.0,
                    text="All uploads completed"
                )

                upload_status.success(
                    f"✅ All {total_uploads} clips "
                    f"uploaded to "
                    f"{current_folder}"
                )

                st.session_state.upload_complete = (
                    True
                )

            except Exception as exc:

                st.error(
                    "❌ OneDrive upload failed."
                )

                st.exception(
                    exc
                )


# ============================================================
# FACEBOOK AUTO POSTER
# ============================================================

if (
    st.session_state.split_complete
    and st.session_state.clips
):

    st.divider()

    st.subheader(
        "🚀 Facebook Auto Poster"
    )

    st.caption(
        "This dispatches the GitHub Actions workflow "
        "for the selected destination."
    )

    if st.button(
        "🚀 Start Facebook Auto Poster",
        use_container_width=True,
    ):

        try:

            dispatch_github_workflow(
                st.session_state.last_destination_name
                or destination_name
            )

            st.success(
                "✅ Facebook GitHub Actions "
                "workflow dispatched successfully."
            )

        except Exception as exc:

            st.error(
                "❌ Facebook workflow dispatch failed."
            )

            st.exception(
                exc
            )


# ============================================================
# METADATA
# ============================================================

if st.session_state.metadata:

    st.divider()

    with st.expander(
        "📝 Processing Metadata"
    ):

        st.json(
            st.session_state.metadata
        )


# ============================================================
# RESET
# ============================================================

if (
    st.session_state.split_complete
    and st.session_state.clips
):

    st.divider()

    if st.button(
        "🗑️ Clear Current Video",
        use_container_width=True,
    ):

        cleanup_previous_job()

        st.rerun()


# ============================================================
# FOOTER
# ============================================================

st.divider()

st.caption(
    "🎬 Pro Video Studio — "
    "Video Splitter + OneDrive Pending Posts"
)
