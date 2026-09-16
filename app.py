import os
import json
import re
import shutil
import subprocess

import requests
import streamlit as st


# ============================================================
# PAGE CONFIG
# ============================================================

st.set_page_config(
    page_title="Pro Video Studio",
    page_icon="🎬",
    layout="wide"
)


# ============================================================
# CONFIG
# ============================================================

ONEDRIVE_USER = "my@011999.onmicrosoft.com"

# -----------------------------
# Posting Destinations
# -----------------------------

PENDING_FOLDERS = {
    "Smart Deals India": "Pending_Posts",
    "Movies and web series": "Movies_Pending_Posts",
}

GITHUB_WORKFLOWS = {
    "Smart Deals India": "main.yml",
    "Movies and web series": "facebook-auto-poster-movies.yml",
}

GITHUB_OWNER = "ayushsharma011999-collab"
GITHUB_REPO = "video-splitter-app"
GITHUB_BRANCH = "main"

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"

CHUNK_SIZE = 5 * 1024 * 1024

UPLOAD_DIR = "/tmp/uploads"
OUTPUT_DIR = "/tmp/video_output"

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)


# ============================================================
# HEADER
# ============================================================

st.title("🎬 Pro Video Studio")
st.caption("Video Splitter + OneDrive Pending Posts + Facebook Auto Poster")


# ============================================================
# HELPER: GET AZURE SECRETS
# ============================================================

def get_secret(name):
    try:
        return st.secrets.get(name, "")
    except Exception:
        return ""


# ============================================================
# MICROSOFT GRAPH TOKEN
# ============================================================

def get_application_access_token():
    tenant_id = get_secret("AZURE_TENANT_ID")
    client_id = get_secret("AZURE_CLIENT_ID")
    client_secret = get_secret("AZURE_CLIENT_SECRET")

    if not tenant_id:
        return None, "AZURE_TENANT_ID missing"

    if not client_id:
        return None, "AZURE_CLIENT_ID missing"

    if not client_secret:
        return None, "AZURE_CLIENT_SECRET missing"

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

    try:
        response = requests.post(
            token_url,
            data=data,
            timeout=60
        )

        if response.status_code != 200:
            try:
                error_data = response.json()
            except Exception:
                error_data = response.text

            return None, (
                f"Microsoft Graph token failed "
                f"(HTTP {response.status_code}): {error_data}"
            )

        token = response.json().get("access_token")

        if not token:
            return None, "Access token missing in Microsoft response"

        return token, None

    except Exception as e:
        return None, f"Token request error: {e}"


# ============================================================
# ONEDRIVE - ENSURE FOLDER
# ============================================================

def ensure_pending_folder(graph_token, folder_name):
    headers = {
        "Authorization": f"Bearer {graph_token}"
    }

    folder_url = (
        f"{GRAPH_BASE_URL}/users/"
        f"{ONEDRIVE_USER}/drive/root:/{folder_name}"
    )

    try:
        response = requests.get(
            folder_url,
            headers=headers,
            timeout=60
        )

        if response.status_code == 200:
            return True, response.json()

        if response.status_code != 404:
            return False, (
                f"Folder check failed "
                f"(HTTP {response.status_code}): "
                f"{response.text}"
            )

        # Folder does not exist - create it
        create_url = (
            f"{GRAPH_BASE_URL}/users/"
            f"{ONEDRIVE_USER}/drive/root/children"
        )

        payload = {
            "name": folder_name,
            "folder": {},
            "@microsoft.graph.conflictBehavior": "fail"
        }

        create_response = requests.post(
            create_url,
            headers={
                **headers,
                "Content-Type": "application/json"
            },
            json=payload,
            timeout=60
        )

        if create_response.status_code in (200, 201):
            return True, create_response.json()

        # In case another process created it meanwhile
        if create_response.status_code == 409:
            return True, {"name": folder_name}

        return False, (
            f"Folder creation failed "
            f"(HTTP {create_response.status_code}): "
            f"{create_response.text}"
        )

    except Exception as e:
        return False, f"OneDrive folder error: {e}"


# ============================================================
# ONEDRIVE - UPLOAD LARGE FILE
# ============================================================

def upload_large_file_to_onedrive(
    graph_token,
    local_file_path,
    folder_name,
    remote_file_name
):
    headers = {
        "Authorization": f"Bearer {graph_token}"
    }

    create_session_url = (
        f"{GRAPH_BASE_URL}/users/"
        f"{ONEDRIVE_USER}/drive/root:/"
        f"{folder_name}/{remote_file_name}:/"
        f"createUploadSession"
    )

    payload = {
        "item": {
            "@microsoft.graph.conflictBehavior": "replace",
            "name": remote_file_name
        }
    }

    try:
        session_response = requests.post(
            create_session_url,
            headers={
                **headers,
                "Content-Type": "application/json"
            },
            json=payload,
            timeout=60
        )

        if session_response.status_code not in (200, 201):
            return False, (
                f"Upload session creation failed "
                f"(HTTP {session_response.status_code}): "
                f"{session_response.text}"
            )

        session_data = session_response.json()
        upload_url = session_data.get("uploadUrl")

        if not upload_url:
            return False, "uploadUrl missing from OneDrive response"

        file_size = os.path.getsize(local_file_path)

        with open(local_file_path, "rb") as file_handle:

            start = 0

            while start < file_size:

                end = min(
                    start + CHUNK_SIZE,
                    file_size
                ) - 1

                chunk_length = end - start + 1

                file_handle.seek(start)

                chunk_data = file_handle.read(chunk_length)

                chunk_headers = {
                    "Content-Length": str(chunk_length),
                    "Content-Range": (
                        f"bytes {start}-{end}/{file_size}"
                    ),
                }

                upload_response = requests.put(
                    upload_url,
                    headers=chunk_headers,
                    data=chunk_data,
                    timeout=300
                )

                if upload_response.status_code not in (
                    200,
                    201,
                    202
                ):
                    return False, (
                        f"Chunk upload failed "
                        f"(HTTP {upload_response.status_code}): "
                        f"{upload_response.text}"
                    )

                start = end + 1

        return True, "Uploaded successfully"

    except Exception as e:
        return False, f"OneDrive upload error: {e}"


# ============================================================
# FFMPEG - GET DURATION
# ============================================================

def get_video_duration(video_path):

    ffprobe = shutil.which("ffprobe")

    if not ffprobe:
        return None

    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                video_path
            ],
            capture_output=True,
            text=True,
            timeout=120
        )

        if result.returncode != 0:
            return None

        value = result.stdout.strip()

        if not value:
            return None

        return float(value)

    except Exception:
        return None


# ============================================================
# FFMPEG - SPLIT VIDEO
# ============================================================

def split_video(
    input_path,
    output_directory,
    clip_duration,
    aspect_ratio,
    watermark_text
):

    ffmpeg = shutil.which("ffmpeg")

    if not ffmpeg:
        return False, [], "FFmpeg is not installed"

    os.makedirs(output_directory, exist_ok=True)

    duration = get_video_duration(input_path)

    if duration is None:
        return False, [], "Unable to detect video duration"

    total_clips = int(
        (duration + clip_duration - 0.001) // clip_duration
    )

    if duration > total_clips * clip_duration:
        total_clips += 1

    source_name = os.path.splitext(
        os.path.basename(input_path)
    )[0]

    output_files = []

    # --------------------------------------------------------
    # Video filter
    # --------------------------------------------------------

    filters = []

    if aspect_ratio == "9:16":

        filters.append(
            "scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920"
        )

    elif aspect_ratio == "16:9":

        filters.append(
            "scale=1920:1080:force_original_aspect_ratio=increase,"
            "crop=1920:1080"
        )

    elif aspect_ratio == "1:1":

        filters.append(
            "scale=1080:1080:force_original_aspect_ratio=increase,"
            "crop=1080:1080"
        )

    # Part number text
    filters.append(
        "drawtext="
        "text='Part %d/%d':"
        "x=(w-text_w)/2:"
        "y=h-120:"
        "fontsize=48:"
        "fontcolor=white:"
        "borderw=3:"
        "bordercolor=black"
        % (1, total_clips)
    )

    if watermark_text.strip():

        safe_watermark = (
            watermark_text
            .replace("\\", "\\\\")
            .replace(":", "\\:")
            .replace("'", "\\'")
        )

        filters.append(
            "drawtext="
            f"text='{safe_watermark}':"
            "x=40:"
            "y=40:"
            "fontsize=32:"
            "fontcolor=white:"
            "borderw=2:"
            "bordercolor=black"
        )

    filter_complex = ",".join(filters)

    # --------------------------------------------------------
    # Generate clips
    # --------------------------------------------------------

    for part_number in range(1, total_clips + 1):

        output_file = os.path.join(
            output_directory,
            f"{source_name}_clip_{part_number:03d}.mp4"
        )

        start_time = (
            part_number - 1
        ) * clip_duration

        command = [
            ffmpeg,
            "-y",
            "-ss",
            str(start_time),
            "-i",
            input_path,
            "-t",
            str(clip_duration),
            "-vf",
            filter_complex,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            output_file
        ]

        try:

            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=900
            )

            if result.returncode != 0:

                return (
                    False,
                    output_files,
                    result.stderr[-3000:]
                )

            if os.path.exists(output_file):

                output_files.append(output_file)

        except Exception as e:

            return (
                False,
                output_files,
                f"FFmpeg error: {e}"
            )

    return True, output_files, None


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
# GITHUB ACTIONS
# ============================================================

def run_github_auto_poster(workflow_file):

    github_token = get_secret("GITHUB_TOKEN")

    if not github_token:
        return False, "GITHUB_TOKEN missing in Streamlit Secrets"

    workflow_url = (
        "https://api.github.com/repos/"
        f"{GITHUB_OWNER}/"
        f"{GITHUB_REPO}/"
        f"actions/workflows/"
        f"{workflow_file}/dispatches"
    )

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {github_token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json"
    }

    payload = {
        "ref": GITHUB_BRANCH
    }

    try:

        response = requests.post(
            workflow_url,
            headers=headers,
            json=payload,
            timeout=60
        )

        if response.status_code == 204:

            return (
                True,
                f"GitHub workflow '{workflow_file}' "
                f"successfully started."
            )

        try:
            error_data = response.json()
        except Exception:
            error_data = response.text

        return False, (
            f"GitHub workflow failed "
            f"(HTTP {response.status_code}): "
            f"{error_data}"
        )

    except Exception as e:

        return False, f"GitHub request error: {e}"


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:

    st.header("⚙️ Settings")

    # --------------------------------------------------------
    # Posting Destination
    # --------------------------------------------------------

    st.subheader("📁 Posting Destination")

    posting_destination = st.radio(
        "Select Facebook Page",
        [
            "Smart Deals India",
            "Movies and web series"
        ],
        index=0
    )

    # Dynamic configuration
    selected_pending_folder = PENDING_FOLDERS[
        posting_destination
    ]

    selected_github_workflow = GITHUB_WORKFLOWS[
        posting_destination
    ]

    st.info(
        f"📂 OneDrive Folder\n\n"
        f"`{selected_pending_folder}`"
    )

    st.info(
        f"⚙️ GitHub Workflow\n\n"
        f"`{selected_github_workflow}`"
    )

    st.divider()

    # --------------------------------------------------------
    # Video Settings
    # --------------------------------------------------------

    st.subheader("🎞️ Video Settings")

    clip_duration = st.selectbox(
        "Clip Duration (seconds)",
        [15, 30, 45, 60, 90, 120],
        index=3
    )

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

    watermark_text = st.text_input(
        "Watermark Text",
        value=""
    )

    st.divider()

    # --------------------------------------------------------
    # GitHub Auto Poster
    # --------------------------------------------------------

    st.subheader("🚀 Facebook Auto Poster")

    if st.button(
        "▶️ Run Facebook Auto Poster",
        use_container_width=True
    ):

        with st.spinner(
            f"Starting {posting_destination} workflow..."
        ):

            success, message = run_github_auto_poster(
                selected_github_workflow
            )

        if success:

            st.success(message)

            st.info(
                f"📁 `{selected_pending_folder}` se "
                f"next pending video process hoga."
            )

        else:

            st.error(message)


# ============================================================
# MAIN AREA
# ============================================================

st.markdown(
    f"### 📌 Current Destination: `{posting_destination}`"
)

st.write(
    f"Videos will be uploaded to "
    f"**OneDrive → {selected_pending_folder}**"
)

st.divider()


# ============================================================
# VIDEO UPLOAD
# ============================================================

uploaded_file = st.file_uploader(
    "🎥 Upload Video",
    type=[
        "mp4",
        "mov",
        "mkv",
        "avi",
        "webm"
    ]
)


# ============================================================
# PROCESS UPLOADED VIDEO
# ============================================================

if uploaded_file:

    temp_input_path = os.path.join(
        UPLOAD_DIR,
        uploaded_file.name
    )

    # Always write current uploaded file
    with open(temp_input_path, "wb") as file_handle:
        file_handle.write(uploaded_file.getbuffer())

    st.success(
        f"Video loaded: `{uploaded_file.name}`"
    )

    st.divider()

    # --------------------------------------------------------
    # Video information
    # --------------------------------------------------------

    duration = get_video_duration(
        temp_input_path
    )

    if duration:

        st.info(
            f"⏱️ Video Duration: "
            f"{duration:.2f} seconds"
        )

    # --------------------------------------------------------
    # Start Processing
    # --------------------------------------------------------

    if st.button(
        "✂️ Split Video & Upload to OneDrive",
        type="primary",
        use_container_width=True
    ):

        # ----------------------------------------------------
        # Clean previous output
        # ----------------------------------------------------

        if os.path.exists(OUTPUT_DIR):

            shutil.rmtree(
                OUTPUT_DIR,
                ignore_errors=True
            )

        os.makedirs(
            OUTPUT_DIR,
            exist_ok=True
        )

        # ----------------------------------------------------
        # Split video
        # ----------------------------------------------------

        with st.spinner(
            "✂️ Video splitting in progress..."
        ):

            success, clip_files, error = split_video(
                temp_input_path,
                OUTPUT_DIR,
                clip_duration,
                aspect_ratio,
                watermark_text
            )

        if not success:

            st.error(
                f"Video splitting failed:\n\n{error}"
            )

            st.stop()

        if not clip_files:

            st.error(
                "No video clips were generated."
            )

            st.stop()

        st.success(
            f"✅ {len(clip_files)} video clips created."
        )

        # ----------------------------------------------------
        # Show generated clips
        # ----------------------------------------------------

        st.subheader("🎬 Generated Clips")

        for clip in clip_files:

            st.write(
                f"📹 `{os.path.basename(clip)}`"
            )

        # ----------------------------------------------------
        # Get Graph token
        # ----------------------------------------------------

        with st.spinner(
            "🔐 Connecting to OneDrive..."
        ):

            graph_token, token_error = (
                get_application_access_token()
            )

        if not graph_token:

            st.error(token_error)

            st.stop()

        st.success(
            "✅ Microsoft Graph authentication successful."
        )

        # ----------------------------------------------------
        # Ensure selected OneDrive folder
        # ----------------------------------------------------

        with st.spinner(
            f"📁 Checking OneDrive folder "
            f"`{selected_pending_folder}`..."
        ):

            folder_ok, folder_data = (
                ensure_pending_folder(
                    graph_token,
                    selected_pending_folder
                )
            )

        if not folder_ok:

            st.error(
                f"OneDrive folder error:\n\n"
                f"{folder_data}"
            )

            st.stop()

        st.success(
            f"✅ OneDrive folder ready: "
            f"`{selected_pending_folder}`"
        )

        # ----------------------------------------------------
        # Upload clips
        # ----------------------------------------------------

        st.subheader(
            "☁️ Uploading Videos to OneDrive"
        )

        upload_errors = []

        progress_bar = st.progress(0)

        total_files = len(clip_files)

        for index, clip_file in enumerate(
            clip_files,
            start=1
        ):

            remote_name = os.path.basename(
                clip_file
            )

            st.write(
                f"⬆️ Uploading "
                f"`{remote_name}`..."
            )

            upload_success, upload_message = (
                upload_large_file_to_onedrive(
                    graph_token,
                    clip_file,
                    selected_pending_folder,
                    remote_name
                )
            )

            if upload_success:

                st.success(
                    f"✅ {remote_name}"
                )

            else:

                st.error(
                    f"❌ {remote_name}: "
                    f"{upload_message}"
                )

                upload_errors.append(
                    remote_name
                )

            progress_bar.progress(
                index / total_files
            )

        # ----------------------------------------------------
        # Metadata
        # ----------------------------------------------------

        source_video_name = (
            os.path.basename(
                uploaded_file.name
            )
        )

        metadata = create_metadata(
            source_video=source_video_name,
            total_clips=len(clip_files),
            clip_duration=clip_duration,
            aspect_ratio=aspect_ratio,
            watermark_text=watermark_text
        )

        metadata_filename = (
            os.path.splitext(
                source_video_name
            )[0]
            + "_metadata.json"
        )

        metadata_path = os.path.join(
            OUTPUT_DIR,
            metadata_filename
        )

        with open(
            metadata_path,
            "w",
            encoding="utf-8"
        ) as metadata_file:

            json.dump(
                metadata,
                metadata_file,
                indent=2,
                ensure_ascii=False
            )

        st.write(
            f"📝 Uploading metadata "
            f"`{metadata_filename}`..."
        )

        metadata_success, metadata_message = (
            upload_large_file_to_onedrive(
                graph_token,
                metadata_path,
                selected_pending_folder,
                metadata_filename
            )
        )

        if metadata_success:

            st.success(
                "✅ Metadata uploaded successfully."
            )

        else:

            st.error(
                f"❌ Metadata upload failed: "
                f"{metadata_message}"
            )

        # ----------------------------------------------------
        # Final result
        # ----------------------------------------------------

        if upload_errors:

            st.warning(
                f"{len(upload_errors)} file(s) "
                f"failed to upload."
            )

        else:

            st.success(
                "🎉 All video clips uploaded successfully!"
            )

        # ----------------------------------------------------
        # Destination summary
        # ----------------------------------------------------

        st.divider()

        st.subheader(
            "📦 Posting Queue"
        )

        st.write(
            f"**Destination:** "
            f"{posting_destination}"
        )

        st.write(
            f"**OneDrive Folder:** "
            f"`{selected_pending_folder}`"
        )

        st.write(
            f"**GitHub Workflow:** "
            f"`{selected_github_workflow}`"
        )

        st.info(
            "ℹ️ Video upload ke baad GitHub workflow "
            "automatically start nahi hota. "
            "Sidebar ka **Run Facebook Auto Poster** "
            "button use karke workflow manually start "
            "kar sakte ho. Scheduled GitHub workflow "
            "apne cron ke according bhi run karega."
        )
