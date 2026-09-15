import os
import json
import re
import shutil
import subprocess

import requests
import streamlit as st


# ============================================================
# STREAMLIT CONFIG
# ============================================================

st.set_page_config(
    page_title="Pro Video Studio | OneDrive",
    page_icon="🎬",
    layout="wide"
)


# ============================================================
# UI STYLE
# ============================================================

st.markdown("""
<style>

.main {
    background-color: #0e1117;
}

.dashboard-card {
    background-color: #161b22;
    border: 1px solid #30363d;
    padding: 20px;
    border-radius: 12px;
    margin-bottom: 20px;
}

.stButton button {
    width: 100%;
    border-radius: 8px;
    font-weight: 600;
    padding: 0.6rem 1rem;
}

</style>
""", unsafe_allow_html=True)


# ============================================================
# CONFIG
# ============================================================

ONEDRIVE_USER = "my@011999.onmicrosoft.com"

PENDING_FOLDER = "Pending_Posts"

GITHUB_OWNER = "ayushsharma011999-collab"
GITHUB_REPO = "video-splitter-app"
GITHUB_WORKFLOW = "main.yml"
GITHUB_BRANCH = "main"


# ============================================================
# FFMPEG
# ============================================================

FFMPEG = shutil.which("ffmpeg")

if not FFMPEG:
    st.error(
        "❌ FFmpeg nahi mila. "
        "Please packages.txt mein ffmpeg check karein."
    )
    st.stop()


# ============================================================
# SESSION STATE
# ============================================================

if "input_file" not in st.session_state:
    st.session_state.input_file = None

if "duration" not in st.session_state:
    st.session_state.duration = 0

if "clips" not in st.session_state:
    st.session_state.clips = []


# ============================================================
# MICROSOFT GRAPH TOKEN
# ============================================================

def get_graph_token():
    """
    Microsoft Entra App credentials se
    Microsoft Graph application token obtain karta hai.
    """

    try:
        tenant_id = st.secrets.get(
            "AZURE_TENANT_ID",
            ""
        )

        client_id = st.secrets.get(
            "AZURE_CLIENT_ID",
            ""
        )

        client_secret = st.secrets.get(
            "AZURE_CLIENT_SECRET",
            ""
        )

    except Exception:
        return None, "Azure secrets read nahi ho paaye."

    if not tenant_id:
        return None, "AZURE_TENANT_ID missing hai."

    if not client_id:
        return None, "AZURE_CLIENT_ID missing hai."

    if not client_secret:
        return None, "AZURE_CLIENT_SECRET missing hai."

    token_url = (
        f"https://login.microsoftonline.com/"
        f"{tenant_id}/oauth2/v2.0/token"
    )

    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": "https://graph.microsoft.com/.default",
        "grant_type": "client_credentials"
    }

    try:

        response = requests.post(
            token_url,
            data=payload,
            timeout=30
        )

    except Exception as e:

        return None, (
            f"Microsoft token request failed: {e}"
        )

    if response.status_code != 200:

        try:
            error_data = response.json()
        except Exception:
            error_data = response.text

        return None, (
            "Microsoft Graph token failed.\n\n"
            f"HTTP {response.status_code}\n"
            f"{error_data}"
        )

    data = response.json()

    token = data.get("access_token")

    if not token:
        return None, (
            "Microsoft Graph access token response mein nahi mila."
        )

    return token, None


# ============================================================
# ONEDRIVE FOLDER
# ============================================================

def ensure_pending_folder(graph_token):

    headers = {
        "Authorization": f"Bearer {graph_token}"
    }

    check_url = (
        "https://graph.microsoft.com/v1.0/"
        f"users/{ONEDRIVE_USER}/drive/root:"
        f"/{PENDING_FOLDER}"
    )

    try:

        response = requests.get(
            check_url,
            headers=headers,
            timeout=30
        )

    except Exception as e:

        return False, f"Folder check failed: {e}"

    if response.status_code == 200:
        return True, "Pending_Posts folder already exists."

    if response.status_code != 404:

        return False, (
            f"Folder check failed: "
            f"HTTP {response.status_code}\n"
            f"{response.text}"
        )

    # --------------------------------------------------------
    # CREATE FOLDER
    # --------------------------------------------------------

    create_url = (
        "https://graph.microsoft.com/v1.0/"
        f"users/{ONEDRIVE_USER}/drive/root/children"
    )

    payload = {
        "name": PENDING_FOLDER,
        "folder": {},
        "@microsoft.graph.conflictBehavior": "fail"
    }

    try:

        response = requests.post(
            create_url,
            headers={
                "Authorization": f"Bearer {graph_token}",
                "Content-Type": "application/json"
            },
            json=payload,
            timeout=30
        )

    except Exception as e:

        return False, (
            f"Folder creation failed: {e}"
        )

    if response.status_code in [200, 201]:

        return True, "Pending_Posts folder created."

    if response.status_code == 409:

        return True, "Pending_Posts folder already exists."

    return False, (
        f"Folder creation failed: "
        f"HTTP {response.status_code}\n"
        f"{response.text}"
    )


# ============================================================
# ONEDRIVE LARGE FILE UPLOAD
# ============================================================

def upload_large_file_to_onedrive(
    file_path,
    graph_token,
    file_name=None
):

    if not os.path.exists(file_path):

        return False, (
            "Local file nahi mili."
        )

    if file_name is None:

        file_name = os.path.basename(file_path)

    # --------------------------------------------------------
    # CREATE UPLOAD SESSION
    # --------------------------------------------------------

    session_url = (
        "https://graph.microsoft.com/v1.0/"
        f"users/{ONEDRIVE_USER}/drive/root:"
        f"/{PENDING_FOLDER}/{file_name}:"
        "/createUploadSession"
    )

    headers = {
        "Authorization": f"Bearer {graph_token}",
        "Content-Type": "application/json"
    }

    payload = {
        "item": {
            "@microsoft.graph.conflictBehavior": "replace",
            "name": file_name
        }
    }

    try:

        response = requests.post(
            session_url,
            headers=headers,
            json=payload,
            timeout=30
        )

    except Exception as e:

        return False, (
            f"Upload session failed: {e}"
        )

    if response.status_code not in [200, 201]:

        return False, (
            "Upload session create nahi hua.\n"
            f"HTTP {response.status_code}\n"
            f"{response.text}"
        )

    upload_url = response.json().get(
        "uploadUrl"
    )

    if not upload_url:

        return False, (
            "Microsoft Graph upload URL nahi mila."
        )

    # --------------------------------------------------------
    # CHUNK UPLOAD
    # --------------------------------------------------------

    file_size = os.path.getsize(file_path)

    # 5 MiB
    chunk_size = 5 * 1024 * 1024

    start = 0

    try:

        with open(file_path, "rb") as file:

            while start < file_size:

                file.seek(start)

                chunk = file.read(
                    chunk_size
                )

                if not chunk:
                    break

                end = (
                    start
                    + len(chunk)
                    - 1
                )

                upload_headers = {
                    "Content-Length": str(
                        len(chunk)
                    ),
                    "Content-Range": (
                        f"bytes {start}-{end}/"
                        f"{file_size}"
                    )
                }

                upload_response = requests.put(
                    upload_url,
                    headers=upload_headers,
                    data=chunk,
                    timeout=180
                )

                if upload_response.status_code not in [
                    200,
                    201,
                    202
                ]:

                    return False, (
                        "Chunk upload failed.\n"
                        f"HTTP {upload_response.status_code}\n"
                        f"{upload_response.text}"
                    )

                start = end + 1

    except Exception as e:

        return False, (
            f"Upload exception: {e}"
        )

    return True, "Upload successful."


# ============================================================
# VIDEO DURATION
# ============================================================

def get_video_duration(video_path):

    command = [
        FFMPEG,
        "-i",
        video_path
    ]

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    match = re.search(
        r"Duration:\s*(\d+):(\d+):([\d.]+)",
        result.stderr
    )

    if not match:
        return 0

    hours = int(match.group(1))
    minutes = int(match.group(2))
    seconds = float(match.group(3))

    return (
        hours * 3600
        + minutes * 60
        + seconds
    )


# ============================================================
# VIDEO SPLITTER
# ============================================================

def split_video(
    video_path,
    output_dir,
    clip_duration,
    aspect_ratio,
    watermark_text
):

    os.makedirs(
        output_dir,
        exist_ok=True
    )

    duration = get_video_duration(
        video_path
    )

    if duration <= 0:

        raise Exception(
            "Video duration read nahi ho paayi."
        )

    total_clips = int(
        duration // clip_duration
    )

    if duration % clip_duration > 0:

        total_clips += 1

    # --------------------------------------------------------
    # OUTPUT SIZE
    # --------------------------------------------------------

    if "9:16" in aspect_ratio:

        vf_scale = (
            "scale=1080:1920:"
            "force_original_aspect_ratio=increase,"
            "crop=1080:1920"
        )

    elif "16:9" in aspect_ratio:

        vf_scale = (
            "scale=1920:1080:"
            "force_original_aspect_ratio=increase,"
            "crop=1920:1080"
        )

    elif "1:1" in aspect_ratio:

        vf_scale = (
            "scale=1080:1080:"
            "force_original_aspect_ratio=increase,"
            "crop=1080:1080"
        )

    else:

        vf_scale = (
            "scale=1080:1920:"
            "force_original_aspect_ratio=increase,"
            "crop=1080:1920"
        )

    # --------------------------------------------------------
    # WATERMARK SAFETY
    # --------------------------------------------------------

    safe_watermark = ""

    if watermark_text:

        safe_watermark = (
            watermark_text
            .replace("'", "")
            .replace(":", "")
            .replace("\\", "")
        )

    progress_bar = st.progress(0)

    status_text = st.empty()

    clips = []

    source_name = os.path.splitext(
        os.path.basename(video_path)
    )[0]

    # --------------------------------------------------------
    # SPLIT
    # --------------------------------------------------------

    for i in range(total_clips):

        start_time = (
            i * clip_duration
        )

        part_number = i + 1

        # IMPORTANT:
        # GitHub Actions isi naming pattern ko read karega.
        output_file = os.path.join(
            output_dir,
            f"{source_name}_clip_{part_number:03d}.mp4"
        )

        part_text = (
            f"Part {part_number}/{total_clips}"
        )

        filters = [
            (
                f"drawtext="
                f"text='{part_text}':"
                f"fontcolor=white:"
                f"fontsize=60:"
                f"box=1:"
                f"boxcolor=black@0.6:"
                f"boxborderw=10:"
                f"x=(w-text_w)/2:"
                f"y=50"
            )
        ]

        if safe_watermark:

            filters.append(
                (
                    f"drawtext="
                    f"text='{safe_watermark}':"
                    f"fontcolor=white:"
                    f"fontsize=48:"
                    f"box=1:"
                    f"boxcolor=black@0.5:"
                    f"boxborderw=10:"
                    f"x=w-tw-50:"
                    f"y=h-th-50"
                )
            )

        final_filter = (
            vf_scale
            + ","
            + ",".join(filters)
        )

        command = [
            FFMPEG,
            "-y",

            "-ss",
            str(start_time),

            "-i",
            video_path,

            "-t",
            str(clip_duration),

            "-vf",
            final_filter,

            "-c:v",
            "libx264",

            "-preset",
            "veryfast",

            "-crf",
            "23",

            "-c:a",
            "aac",

            "-b:a",
            "128k",

            "-movflags",
            "+faststart",

            output_file
        ]

        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        if result.returncode != 0:

            raise Exception(
                "FFmpeg error:\n\n"
                + result.stderr[-3000:]
            )

        if os.path.exists(output_file):

            clips.append(
                output_file
            )

        progress_bar.progress(
            (i + 1) / total_clips
        )

        status_text.text(
            f"⚡ Processing Clip "
            f"{i + 1}/{total_clips}"
        )

    status_text.success(
        "✅ Video processing complete!"
    )

    return clips


# ============================================================
# GITHUB ACTIONS
# ============================================================

def run_github_auto_poster():

    try:

        github_token = st.secrets.get(
            "GITHUB_TOKEN",
            ""
        )

    except Exception:

        github_token = ""

    if not github_token:

        return False, (
            "GITHUB_TOKEN Streamlit Secrets mein nahi mila."
        )

    workflow_url = (
        "https://api.github.com/repos/"
        f"{GITHUB_OWNER}/"
        f"{GITHUB_REPO}/"
        f"actions/workflows/"
        f"{GITHUB_WORKFLOW}/dispatches"
    )

    headers = {
        "Accept": (
            "application/vnd.github+json"
        ),
        "Authorization": (
            f"Bearer {github_token}"
        ),
        "X-GitHub-Api-Version": (
            "2022-11-28"
        )
    }

    payload = {
        "ref": GITHUB_BRANCH
    }

    try:

        response = requests.post(
            workflow_url,
            headers=headers,
            json=payload,
            timeout=30
        )

    except Exception as e:

        return False, (
            f"GitHub request failed: {e}"
        )

    # GitHub workflow_dispatch successful response
    # = HTTP 204
    if response.status_code == 204:

        return True, (
            "GitHub Facebook Auto Poster "
            "successfully started."
        )

    return False, (
        "GitHub Action start nahi hua.\n\n"
        f"HTTP Status: {response.status_code}\n"
        f"{response.text}"
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
# HEADER
# ============================================================

st.markdown("""
<div style="
    padding:10px 0;
    border-bottom:1px solid #30363d;
    margin-bottom:25px;
">

<h1 style="
    color:#c9d1d9;
    margin:0;
    font-size:30px;
">

🎬 Pro Video Studio

</h1>

<p style="
    color:#8b949e;
    margin-top:6px;
">

Video Splitter + OneDrive Pending Posts

</p>

</div>
""", unsafe_allow_html=True)


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:

    st.markdown(
        "## ⚙️ Settings"
    )

    st.markdown("---")

    st.markdown(
        "### 🎞️ Video Settings"
    )

    clip_duration = st.slider(
        "Clip Duration (seconds)",
        min_value=15,
        max_value=120,
        value=60,
        step=15
    )

    aspect_ratio = st.selectbox(
        "Aspect Ratio",
        [
            "9:16 (Vertical / Reels)",
            "16:9 (Landscape)",
            "1:1 (Square)"
        ]
    )

    watermark_text = st.text_input(
        "Watermark Text",
        placeholder="@YourPage"
    )

    st.markdown("---")

    st.markdown(
        "### ⚡ Facebook Auto Poster"
    )

    if st.button(
        "▶️ Run Facebook Auto Poster",
        type="primary",
        use_container_width=True
    ):

        with st.spinner(
            "GitHub Action start ho raha hai..."
        ):

            success, message = (
                run_github_auto_poster()
            )

        if success:

            st.success(
                "✅ " + message
            )

            st.info(
                "GitHub Actions ab next pending "
                "video process karega."
            )

        else:

            st.error(
                "❌ " + message
            )


# ============================================================
# VIDEO UPLOAD
# ============================================================

st.markdown(
    "## 📤 Upload Video"
)

uploaded_file = st.file_uploader(
    "Video file select karein",
    type=[
        "mp4",
        "mov",
        "mkv",
        "avi",
        "webm"
    ],
    help=(
        "Supported video formats: "
        "MP4, MOV, MKV, AVI, WEBM"
    )
)


# ============================================================
# SAVE UPLOADED VIDEO
# ============================================================

if uploaded_file is not None:

    os.makedirs(
        "/tmp/uploads",
        exist_ok=True
    )

    temp_input_path = os.path.join(
        "/tmp/uploads",
        uploaded_file.name
    )

    file_changed = (
        st.session_state.input_file
        != temp_input_path
        or
        not os.path.exists(
            temp_input_path
        )
    )

    if file_changed:

        with open(
            temp_input_path,
            "wb"
        ) as file:

            file.write(
                uploaded_file.getbuffer()
            )

        st.session_state.input_file = (
            temp_input_path
        )

        st.session_state.duration = (
            get_video_duration(
                temp_input_path
            )
        )

        st.session_state.clips = []


# ============================================================
# VIDEO INFORMATION
# ============================================================

if (
    st.session_state.input_file
    and os.path.exists(
        st.session_state.input_file
    )
):

    st.markdown("---")

    col1, col2, col3 = st.columns(3)

    with col1:

        st.metric(
            "Video",
            os.path.basename(
                st.session_state.input_file
            )
        )

    with col2:

        st.metric(
            "Duration",
            f"{st.session_state.duration:.1f} sec"
        )

    estimated_clips = int(
        st.session_state.duration
        // clip_duration
    )

    if (
        st.session_state.duration
        % clip_duration
    ):

        estimated_clips += 1

    with col3:

        st.metric(
            "Estimated Clips",
            estimated_clips
        )


# ============================================================
# PROCESS VIDEO
# ============================================================

if (
    st.session_state.input_file
    and os.path.exists(
        st.session_state.input_file
    )
):

    st.markdown("---")

    if st.button(
        "🚀 Split Video & Upload to OneDrive",
        type="primary",
        use_container_width=True
    ):

        output_dir = (
            "/tmp/reels"
        )

        if os.path.exists(
            output_dir
        ):

            shutil.rmtree(
                output_dir
            )

        os.makedirs(
            output_dir,
            exist_ok=True
        )

        # ----------------------------------------------------
        # GET GRAPH TOKEN
        # ----------------------------------------------------

        with st.spinner(
            "Microsoft Graph se connection ho raha hai..."
        ):

            graph_token, token_error = (
                get_graph_token()
            )

        if token_error:

            st.error(
                "❌ OneDrive connection failed."
            )

            st.code(
                token_error
            )

            st.stop()

        # ----------------------------------------------------
        # CHECK / CREATE PENDING FOLDER
        # ----------------------------------------------------

        with st.spinner(
            "Pending_Posts folder check ho raha hai..."
        ):

            folder_ok, folder_message = (
                ensure_pending_folder(
                    graph_token
                )
            )

        if not folder_ok:

            st.error(
                "❌ Pending_Posts folder problem."
            )

            st.code(
                folder_message
            )

            st.stop()

        # ----------------------------------------------------
        # SPLIT
        # ----------------------------------------------------

        try:

            with st.spinner(
                "FFmpeg video clips generate kar raha hai..."
            ):

                clips = split_video(
                    st.session_state.input_file,
                    output_dir,
                    clip_duration,
                    aspect_ratio,
                    watermark_text
                )

            st.session_state.clips = clips

        except Exception as e:

            st.error(
                "❌ Video processing failed."
            )

            st.code(
                str(e)
            )

            st.stop()

        if not clips:

            st.error(
                "❌ Koi clip generate nahi hui."
            )

            st.stop()

        st.success(
            f"✅ {len(clips)} clips generated!"
        )

        # ----------------------------------------------------
        # UPLOAD CLIPS
        # ----------------------------------------------------

        st.markdown(
            "### ☁️ Uploading to Pending_Posts"
        )

        progress = st.progress(0)

        status = st.empty()

        upload_failed = False

        for index, clip in enumerate(
            clips
        ):

            clip_name = os.path.basename(
                clip
            )

            status.text(
                f"☁️ Uploading "
                f"{clip_name}..."
            )

            success, message = (
                upload_large_file_to_onedrive(
                    clip,
                    graph_token,
                    clip_name
                )
            )

            if not success:

                st.error(
                    f"❌ {clip_name}: "
                    f"{message}"
                )

                upload_failed = True

                break

            progress.progress(
                (index + 1) / len(clips)
            )

        # ----------------------------------------------------
        # UPLOAD METADATA
        # ----------------------------------------------------

        if not upload_failed:

            source_video = (
                uploaded_file.name
            )

            metadata = create_metadata(
                source_video=source_video,
                total_clips=len(clips),
                clip_duration=clip_duration,
                aspect_ratio=aspect_ratio,
                watermark_text=watermark_text
            )

            metadata_path = (
                "/tmp/"
                + os.path.splitext(
                    source_video
                )[0]
                + "_metadata.json"
            )

            with open(
                metadata_path,
                "w",
                encoding="utf-8"
            ) as file:

                json.dump(
                    metadata,
                    file,
                    indent=4,
                    ensure_ascii=False
                )

            metadata_name = os.path.basename(
                metadata_path
            )

            status.text(
                f"☁️ Uploading "
                f"{metadata_name}..."
            )

            meta_success, meta_message = (
                upload_large_file_to_onedrive(
                    metadata_path,
                    graph_token,
                    metadata_name
                )
            )

            if not meta_success:

                st.error(
                    "❌ Metadata upload failed."
                )

                st.code(
                    meta_message
                )

            else:

                progress.progress(1.0)

                status.empty()

                st.success(
                    "🎉 Complete!"
                )

                st.info(
                    "Clips Pending_Posts mein upload "
                    "ho gayi hain. Ab GitHub Actions "
                    "unhe Facebook par post kar sakta hai."
                )


# ============================================================
# GENERATED CLIPS
# ============================================================

if st.session_state.clips:

    st.markdown("---")

    st.markdown(
        "## 🎞️ Generated Clips"
    )

    clip_columns = st.columns(2)

    for index, clip in enumerate(
        st.session_state.clips
    ):

        with clip_columns[
            index % 2
        ]:

            st.markdown(
                f"### Part {index + 1}"
            )

            st.video(
                clip
            )

            with open(
                clip,
                "rb"
            ) as file:

                st.download_button(
                    label=(
                        f"⬇️ Download Part "
                        f"{index + 1}"
                    ),
                    data=file.read(),
                    file_name=os.path.basename(
                        clip
                    ),
                    mime="video/mp4",
                    key=f"download_{index}"
                )


# ============================================================
# EMPTY STATE
# ============================================================

if uploaded_file is None:

    st.markdown("""
    <div style="
        text-align:center;
        padding:60px 20px;
        background-color:#161b22;
        border:1px dashed #30363d;
        border-radius:12px;
    ">

    <h3>📂 No Video Loaded</h3>

    <p style="color:#8b949e;">
    Upar se video upload karein.
    </p>

    </div>
    """, unsafe_allow_html=True)
