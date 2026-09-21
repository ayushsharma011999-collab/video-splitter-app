import os
import io
import math
import time
import shutil
import zipfile
import tempfile
import subprocess
from pathlib import Path
from urllib.parse import quote

import requests
import streamlit as st


# ============================================================
# APP CONFIG
# ============================================================

st.set_page_config(
    page_title="Video Splitter & Cloud Studio",
    page_icon="🎬",
    layout="wide",
)

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"
ONEDRIVE_USER = "my@011999.onmicrosoft.com"
CHUNK_SIZE = 5 * 1024 * 1024  # 5MB chunks (Multiple of 320 KiB)

DEFAULT_REPO = "ayushsharma011999-collab/video-splitter-app"

# दोनों फोल्डर्स और उनके GitHub Workflows
DESTINATION_CONFIG = {
    "Smart Deals India": {
        "folder": "Pending_Posts",
        "workflow": "main.yml",
    },
    "Movies and web series": {
        "folder": "Movies_Pending_Posts",
        "workflow": "facebook-auto-poster-movies.yml",
    },
}


# ============================================================
# SESSION STATE
# ============================================================

DEFAULT_STATE = {
    "onedrive_connected": False,
    "onedrive_drive_id": None,
    "onedrive_folder_id": None,
    "onedrive_folder_name": None,

    "job_dir": None,
    "clips": [],
    "source_name": None,

    "download_zip": None,
    "download_zip_name": None,

    "split_complete": False,
    "upload_complete": False,
}

for key, value in DEFAULT_STATE.items():
    if key not in st.session_state:
        st.session_state[key] = value


# ============================================================
# HELPERS & FFMPEG
# ============================================================

def get_secret(name, default=None):
    try:
        value = st.secrets[name]
        if value is not None and str(value).strip():
            return str(value).strip()
    except Exception:
        pass
    val = os.getenv(name)
    return val.strip() if val else default


def safe_filename(name):
    name = Path(name).name
    for char in '<>:"/\\|?*':
        name = name.replace(char, "_")
    return name.strip().strip(".") or "video"


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
        raise RuntimeError("FFmpeg/FFprobe PATH me nahi mila.") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("FFmpeg process timeout ho gaya.") from exc

    if result.returncode != 0:
        error_text = result.stderr.strip()
        if len(error_text) > 3000:
            error_text = error_text[-3000:]
        raise RuntimeError(error_text or "FFmpeg error.")

    return result


def get_video_duration(video_path):
    command = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path,
    ]
    result = run_command(command, timeout=120)
    try:
        duration = float(result.stdout.strip())
    except ValueError as exc:
        raise RuntimeError("Video duration read nahi ho payi.") from exc

    if duration <= 0:
        raise RuntimeError("Invalid video duration.")
    return duration


def split_video(input_path, output_dir, clip_duration, progress_callback=None):
    os.makedirs(output_dir, exist_ok=True)
    duration = get_video_duration(input_path)
    clip_duration = float(clip_duration)
    total_parts = max(1, math.ceil(duration / clip_duration))

    clips = []

    for part_number in range(1, total_parts + 1):
        start_time = (part_number - 1) * clip_duration
        remaining = duration - start_time
        current_duration = min(clip_duration, remaining)

        if current_duration <= 0:
            break

        output_path = os.path.join(output_dir, f"clip_{part_number:03d}.mp4")

        # Fast & clean split
        command = [
            "ffmpeg",
            "-y",
            "-ss", f"{start_time:.3f}",
            "-avoid_negative_ts", "make_zero",
            "-i", input_path,
            "-t", f"{current_duration:.3f}",
            "-map", "0:v:0",
            "-map", "0:a?",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "22",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "128k",
            "-sn",
            "-movflags", "+faststart",
            output_path,
        ]

        try:
            run_command(command, timeout=3600)
        except Exception as exc:
            raise RuntimeError(f"Error creating PART {part_number}/{total_parts}:\n{exc}") from exc

        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            raise RuntimeError(f"Part {part_number} generate nahi hua.")

        clips.append(output_path)

        if progress_callback:
            progress_callback(part_number / total_parts)

    return clips


def create_zip(clips, source_name):
    zip_buffer = io.BytesIO()
    zip_base = Path(source_name).stem
    zip_name = f"{safe_filename(zip_base)}_all_clips.zip"

    with zipfile.ZipFile(zip_buffer, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as zip_file:
        for clip in clips:
            zip_file.write(clip, arcname=os.path.basename(clip))

    zip_buffer.seek(0)
    return zip_name, zip_buffer.getvalue()


# ============================================================
# ONEDRIVE / GRAPH API
# ============================================================

def get_application_access_token():
    tenant_id = get_secret("AZURE_TENANT_ID")
    client_id = get_secret("AZURE_CLIENT_ID")
    client_secret = get_secret("AZURE_CLIENT_SECRET")

    if not tenant_id or not client_id or not client_secret:
        raise RuntimeError("Azure Secrets (AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET) missing hain.")

    token_url = f"https://login.microsoftonline.com/{quote(tenant_id, safe='')}/oauth2/v2.0/token"
    res = requests.post(
        token_url,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": GRAPH_SCOPE,
            "grant_type": "client_credentials",
        },
        timeout=60,
    )
    if res.status_code != 200:
        raise RuntimeError(f"Microsoft Token Error ({res.status_code}):\n{res.text[:1000]}")

    token = res.json().get("access_token")
    if not token:
        raise RuntimeError("Token receive nahi hua.")
    return token


def graph_headers(token):
    return {"Authorization": f"Bearer {token}"}


def get_drive(token):
    url = f"{GRAPH_BASE_URL}/users/{quote(ONEDRIVE_USER, safe='')}/drive"
    res = requests.get(url, headers=graph_headers(token), timeout=60)
    if res.status_code != 200:
        raise RuntimeError(f"OneDrive Access Error ({res.status_code}):\n{res.text[:1000]}")
    return res.json()


def get_folder(token, drive_id, folder_name):
    url = f"{GRAPH_BASE_URL}/drives/{quote(drive_id, safe='')}/root:/{quote(folder_name, safe='')}"
    res = requests.get(url, headers=graph_headers(token), timeout=60)
    if res.status_code == 200:
        return res.json()
    if res.status_code == 404:
        return None
    raise RuntimeError(f"Folder check failed ({res.status_code}):\n{res.text[:1000]}")


def create_folder(token, drive_id, folder_name):
    url = f"{GRAPH_BASE_URL}/drives/{quote(drive_id, safe='')}/root/children"
    res = requests.post(
        url,
        headers={**graph_headers(token), "Content-Type": "application/json"},
        json={"name": folder_name, "folder": {}, "@microsoft.graph.conflictBehavior": "fail"},
        timeout=60,
    )
    if res.status_code in (200, 201):
        return res.json()
    if res.status_code == 409:
        existing = get_folder(token, drive_id, folder_name)
        if existing:
            return existing
    raise RuntimeError(f"Folder creation failed ({res.status_code}):\n{res.text[:1000]}")


def connect_onedrive(folder_name):
    token = get_application_access_token()
    drive = get_drive(token)
    folder = get_folder(token, drive["id"], folder_name)
    if folder is None:
        folder = create_folder(token, drive["id"], folder_name)

    st.session_state.onedrive_connected = True
    st.session_state.onedrive_drive_id = drive["id"]
    st.session_state.onedrive_folder_id = folder["id"]
    st.session_state.onedrive_folder_name = folder_name
    return token, drive, folder


def upload_small_file(token, drive_id, folder_id, file_path):
    filename = os.path.basename(file_path)
    url = (
        f"{GRAPH_BASE_URL}/drives/{quote(drive_id, safe='')}/items/"
        f"{quote(folder_id, safe='')}:/{quote(filename, safe='')}:/content"
    )
    with open(file_path, "rb") as fh:
        res = requests.put(url, headers={**graph_headers(token), "Content-Type": "video/mp4"}, data=fh, timeout=300)
    if res.status_code not in (200, 201):
        raise RuntimeError(f"Upload failed ({res.status_code}): {res.text[:1000]}")
    return res.json()


def upload_large_file(token, drive_id, folder_id, file_path, progress_callback=None):
    filename = os.path.basename(file_path)
    create_url = (
        f"{GRAPH_BASE_URL}/drives/{quote(drive_id, safe='')}/items/"
        f"{quote(folder_id, safe='')}:/{quote(filename, safe='')}:/createUploadSession"
    )
    session_res = requests.post(
        create_url,
        headers={**graph_headers(token), "Content-Type": "application/json"},
        json={"item": {"@microsoft.graph.conflictBehavior": "replace", "name": filename}},
        timeout=60,
    )
    if session_res.status_code not in (200, 201):
        raise RuntimeError(f"Session failed ({session_res.status_code}): {session_res.text[:1000]}")

    upload_url = session_res.json().get("uploadUrl")
    if not upload_url:
        raise RuntimeError("No uploadUrl returned.")

    total_size = os.path.getsize(file_path)
    uploaded = 0

    with open(file_path, "rb") as fh:
        while uploaded < total_size:
            chunk = fh.read(CHUNK_SIZE)
            if not chunk:
                break
            start = uploaded
            end = uploaded + len(chunk) - 1

            success = False
            for attempt in range(3):
                try:
                    res = requests.put(
                        upload_url,
                        headers={
                            "Content-Length": str(len(chunk)),
                            "Content-Range": f"bytes {start}-{end}/{total_size}",
                        },
                        data=chunk,
                        timeout=300,
                    )
                    if res.status_code in (200, 201, 202):
                        success = True
                        break
                except requests.RequestException:
                    time.sleep(2 ** attempt)

            if not success:
                raise RuntimeError(f"Upload failed at bytes {start}-{end}")

            uploaded = end + 1
            if progress_callback:
                progress_callback(uploaded / total_size)

    return {"name": filename, "size": total_size}


def upload_file_to_onedrive(token, drive_id, folder_id, file_path, progress_callback=None):
    file_size = os.path.getsize(file_path)
    if file_size <= 4 * 1024 * 1024:
        if progress_callback:
            progress_callback(0.5)
        res = upload_small_file(token, drive_id, folder_id, file_path)
        if progress_callback:
            progress_callback(1.0)
        return res
    return upload_large_file(token, drive_id, folder_id, file_path, progress_callback)


# ============================================================
# GITHUB ACTIONS DISPATCH HELPER
# ============================================================

def dispatch_github_workflow(workflow_file):
    github_token = get_secret("GITHUB_TOKEN")
    repo = get_secret("GITHUB_REPO", DEFAULT_REPO)

    if not github_token:
        raise RuntimeError("GITHUB_TOKEN secrets me configure nahi hai.")

    url = f"https://api.github.com/repos/{repo}/actions/workflows/{quote(workflow_file, safe='')}/dispatches"

    response = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {github_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json={"ref": "main"},
        timeout=60,
    )

    if response.status_code != 204:
        raise RuntimeError(f"Workflow dispatch failed ({response.status_code}):\n{response.text[:1000]}")
    return True


# ============================================================
# CLEANUP
# ============================================================

def cleanup_previous_job():
    old_job = st.session_state.get("job_dir")
    if old_job and os.path.exists(old_job):
        shutil.rmtree(old_job, ignore_errors=True)

    st.session_state.job_dir = None
    st.session_state.clips = []
    st.session_state.source_name = None
    st.session_state.download_zip = None
    st.session_state.download_zip_name = None
    st.session_state.split_complete = False
    st.session_state.upload_complete = False


# ============================================================
# UI: HEADER & SIDEBAR
# ============================================================

st.title("✂️ Video Splitter & Cloud Studio")
st.caption("Split Video • Local Download • OneDrive Upload • GitHub Manual Triggers")

with st.sidebar:
    st.header("⚙️ Split Settings")

    clip_duration = st.number_input(
        "Clip Duration (Seconds)",
        min_value=5,
        max_value=600,
        value=30,
        step=5,
    )

    st.divider()

    st.subheader("☁️ OneDrive Destination")
    selected_dest_label = st.selectbox("Select Target", list(DESTINATION_CONFIG.keys()))
    selected_dest_folder = DESTINATION_CONFIG[selected_dest_label]["folder"]

    if st.button("🔌 Connect OneDrive", use_container_width=True):
        try:
            with st.spinner("Connecting..."):
                connect_onedrive(selected_dest_folder)
            st.success(f"Connected: {selected_dest_folder}")
        except Exception as exc:
            st.error(f"❌ {exc}")

    if st.session_state.onedrive_connected:
        st.success(f"✅ Active: {st.session_state.onedrive_folder_name}")
    else:
        st.info("OneDrive connect nahi hai")


# ============================================================
# MAIN: VIDEO UPLOAD & SPLIT
# ============================================================

uploaded_video = st.file_uploader(
    "📤 Upload Video",
    type=["mp4", "mov", "mkv", "avi", "webm", "m4v"],
)

if uploaded_video is not None:
    current_name = uploaded_video.name
    previous_name = st.session_state.get("source_name")

    if previous_name and previous_name != current_name and st.session_state.split_complete:
        cleanup_previous_job()

    st.success(f"File: **{uploaded_video.name}** ({uploaded_video.size / (1024 * 1024):.2f} MB)")

    if st.button("✂️ Split Video Now", type="primary", use_container_width=True):
        cleanup_previous_job()

        job_dir = tempfile.mkdtemp(prefix="video_split_")
        input_filename = safe_filename(uploaded_video.name)
        input_path = os.path.join(job_dir, input_filename)
        clips_dir = os.path.join(job_dir, "clips")
        os.makedirs(clips_dir, exist_ok=True)

        st.session_state.job_dir = job_dir
        st.session_state.source_name = uploaded_video.name

        try:
            with st.spinner("Saving video file..."):
                with open(input_path, "wb") as f:
                    shutil.copyfileobj(uploaded_video, f)

            duration = get_video_duration(input_path)
            total_parts = max(1, math.ceil(duration / float(clip_duration)))

            col1, col2 = st.columns(2)
            col1.metric("Video Length", f"{duration:.1f}s")
            col2.metric("Total Clips", total_parts)

            split_progress = st.progress(0, text="Splitting video...")

            def update_progress(v):
                split_progress.progress(max(0.0, min(1.0, float(v))), text=f"Splitting: {int(v * 100)}%")

            clips = split_video(
                input_path=input_path,
                output_dir=clips_dir,
                clip_duration=clip_duration,
                progress_callback=update_progress,
            )

            split_progress.progress(1.0, text="Splitting Complete!")
            st.session_state.clips = clips
            st.session_state.split_complete = True

            zip_name, zip_bytes = create_zip(clips, uploaded_video.name)
            st.session_state.download_zip = zip_bytes
            st.session_state.download_zip_name = zip_name

        except Exception as exc:
            st.error("❌ Video split fail ho gaya.")
            st.exception(exc)


# ============================================================
# 1. LOCAL STORAGE DOWNLOADS
# ============================================================

if st.session_state.split_complete and st.session_state.clips:
    clips = st.session_state.clips
    st.divider()
    st.subheader("💾 Local Storage Download (PC / Mobile)")

    if st.session_state.download_zip:
        st.download_button(
            label="📦 Download All Clips Together (ZIP)",
            data=st.session_state.download_zip,
            file_name=st.session_state.download_zip_name,
            mime="application/zip",
            type="primary",
            use_container_width=True,
            key="zip_download",
        )

    st.write("")

    for idx, clip in enumerate(clips, start=1):
        if not os.path.exists(clip):
            continue

        c_size = os.path.getsize(clip) / (1024 * 1024)
        fname = os.path.basename(clip)

        with st.container(border=True):
            col_info, col_btn = st.columns([3, 1])
            with col_info:
                st.write(f"**Part {idx}** — `{fname}` ({c_size:.2f} MB)")
            with col_btn:
                with open(clip, "rb") as f:
                    clip_bytes = f.read()
                st.download_button(
                    label=f"⬇️ Download Part {idx}",
                    data=clip_bytes,
                    file_name=fname,
                    mime="video/mp4",
                    key=f"dl_{idx}_{st.session_state.source_name}",
                    use_container_width=True,
                )
            st.video(clip_bytes)


# ============================================================
# 2. ONEDRIVE UPLOAD
# ============================================================

if st.session_state.split_complete and st.session_state.clips:
    st.divider()
    st.subheader("☁️ OneDrive Upload")
    active_folder = st.session_state.onedrive_folder_name or selected_dest_folder
    st.write(f"Target Folder: **{active_folder}**")

    if not st.session_state.onedrive_connected:
        st.warning("⚠️ Pehle sidebar se OneDrive connect karein.")
    else:
        if st.button(f"☁️ Upload {len(st.session_state.clips)} Clips to OneDrive", use_container_width=True):
            upload_progress = st.progress(0, text="Uploading to OneDrive...")
            status_text = st.empty()

            try:
                token = get_application_access_token()
                drive_id = st.session_state.onedrive_drive_id
                folder_id = st.session_state.onedrive_folder_id
                total_files = len(st.session_state.clips)

                for idx, clip in enumerate(st.session_state.clips, start=1):
                    fname = os.path.basename(clip)
                    status_text.info(f"Uploading {fname} ({idx}/{total_files})...")

                    def make_cb(i=idx, tot=total_files):
                        return lambda v: upload_progress.progress(
                            (((i - 1) + v) / tot),
                            text=f"Uploading {fname} — {int(v * 100)}%"
                        )

                    upload_file_to_onedrive(
                        token=token,
                        drive_id=drive_id,
                        folder_id=folder_id,
                        file_path=clip,
                        progress_callback=make_cb(),
                    )

                upload_progress.progress(1.0, text="Upload Complete!")
                status_text.success(f"✅ Sabhi {total_files} clips successfully OneDrive me upload ho gayi!")
                st.session_state.upload_complete = True

            except Exception as exc:
                st.error("❌ OneDrive upload fail ho gaya.")
                st.exception(exc)


# ============================================================
# 3. GITHUB ACTIONS MANUAL TRIGGER (DONO BUTTONS)
# ============================================================

st.divider()
st.subheader("🚀 GitHub Actions Manual Run")
st.caption("OneDrive me clips jaane ke baad yahan se workflow manual trigger karein:")

col_gh1, col_gh2 = st.columns(2)

with col_gh1:
    if st.button("🚀 Run Smart Deals India (main.yml)", use_container_width=True):
        try:
            with st.spinner("Dispatching Smart Deals India workflow..."):
                dispatch_github_workflow("main.yml")
            st.success("✅ Smart Deals India (`main.yml`) workflow trigger ho gaya!")
        except Exception as exc:
            st.error(f"❌ Dispatch failed: {exc}")

with col_gh2:
    if st.button("🎬 Run Movies & Series (facebook-auto-poster-movies.yml)", use_container_width=True):
        try:
            with st.spinner("Dispatching Movies workflow..."):
                dispatch_github_workflow("facebook-auto-poster-movies.yml")
            st.success("✅ Movies & Series (`facebook-auto-poster-movies.yml`) workflow trigger ho gaya!")
        except Exception as exc:
            st.error(f"❌ Dispatch failed: {exc}")


# ============================================================
# RESET / CLEAR
# ============================================================

if st.session_state.split_complete and st.session_state.clips:
    st.divider()
    if st.button("🗑️ Clear / New Video", use_container_width=True):
        cleanup_previous_job()
        st.rerun()
