import os
import io
import re
import json
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
    page_title="AI Video Studio & Splitter",
    page_icon="🎬",
    layout="wide",
)

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"
ONEDRIVE_USER = "my@011999.onmicrosoft.com"
CHUNK_SIZE = 5 * 1024 * 1024  # 5MB chunks

DEFAULT_REPO = "ayushsharma011999-collab/video-splitter-app"

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

    # AI Frame Data
    "ai_clean_title": "",
    "ai_hook": "",
    "ai_analyzed": False,
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
    val = os.getenv(name)
    return val.strip() if val else default


def safe_filename(name):
    name = Path(name).name
    for char in '<>:"/\\|?*':
        name = name.replace(char, "_")
    return name.strip().strip(".") or "video"


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


def find_font():
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        "C:\\Windows\\Fonts\\arialbd.ttf",
        "C:\\Windows\\Fonts\\arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ]
    for font_path in candidates:
        if os.path.exists(font_path):
            return font_path
    return None


# ============================================================
# 🤖 AI ENGINE: TITLE SEARCH & FRAME HOOK GENERATOR
# ============================================================

def smart_offline_cleaner(filename):
    """अगर API Key न हो तो यह Regex से फ़ाइल का नाम साफ़ करता है"""
    base = Path(filename).stem
    # Remove release tags like 1080p, WEB-DL, x264, HDRip etc.
    cleaned = re.sub(
        r"(?i)\b(1080p|720p|480p|2160p|4k|web-?dl|bluray|hdrip|x264|x265|hevc|hindi|english|dual audio|aac|sub|esub)\b",
        "",
        base,
    )
    cleaned = cleaned.replace(".", " ").replace("_", " ").replace("-", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    title = cleaned.title() if cleaned else "BLOCKBUSTER SCENE"
    hook = "MUST WATCH SCENE 🔥"
    return title, hook


def ai_analyze_video_filename(filename, gemini_api_key=None):
    """Google Gemini AI से फ़ाइल का नाम एनालाइज़ करके वायरल फ़्रेम तैयार करना"""
    if not gemini_api_key:
        return smart_offline_cleaner(filename)

    prompt = f"""
    You are an expert viral movie clip editor for Facebook Reels.
    Analyze this uploaded video filename: "{filename}"

    Task:
    1. Identify the official Movie or Web Series title (and release year if applicable).
    2. Create a super catchy, high-CTR viral hook line (maximum 4-5 words, in English or Hinglish with 1 emoji) that makes people stop scrolling. Example: "UNSTOPPABLE CLIMAX SCENE 🔥", "WAIT FOR THE TWIST 😱", "LEGENDARY ENTRY SCENE 💥".

    Return ONLY a raw JSON object with NO extra text or markdown formatting:
    {{
        "clean_title": "Official Title Here",
        "viral_hook": "Viral Hook Line With Emoji"
    }}
    """

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={gemini_api_key}"

    try:
        res = requests.post(
            url,
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.3},
            },
            timeout=15,
        )
        if res.status_code == 200:
            data = res.json()
            raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
            # Clean JSON formatting backticks
            raw_text = re.sub(r"```json|```", "", raw_text).strip()
            parsed = json.loads(raw_text)
            return parsed.get("clean_title", "MOVIE CLIP"), parsed.get("viral_hook", "CLIMAX SCENE 🔥")
    except Exception:
        pass

    # Fallback to local cleaner if network/API drops
    return smart_offline_cleaner(filename)


# ============================================================
# 🎨 VIDEO SPLITTER + AI FRAME & DYNAMIC PART ENGINE
# ============================================================

def split_video_with_ai_frame(
    input_path,
    output_dir,
    clip_duration,
    clean_title,
    viral_hook,
    enable_frame=True,
    progress_callback=None,
):
    os.makedirs(output_dir, exist_ok=True)
    duration = get_video_duration(input_path)
    clip_duration = float(clip_duration)
    total_parts = max(1, math.ceil(duration / clip_duration))

    font_path = find_font()
    font_filter_str = f":fontfile='{ffmpeg_escape_text(font_path)}'" if font_path else ""

    clips = []

    for part_number in range(1, total_parts + 1):
        start_time = (part_number - 1) * clip_duration
        remaining = duration - start_time
        current_duration = min(clip_duration, remaining)

        if current_duration <= 0:
            break

        output_path = os.path.join(output_dir, f"clip_{part_number:03d}.mp4")

        # ----------------------------------------------------
        # AI FRAME & DYNAMIC PART FILTERS
        # ----------------------------------------------------
        v_filters = []

        if enable_frame:
            # 1. Top Bar Background (Dark translucent header)
            v_filters.append("drawbox=x=0:y=0:w=iw:h='ih*0.14':color=black@0.75:t=fill")

            # 2. Line 1: AI Viral Hook (Top Center, Yellow, Eye-catching)
            esc_hook = ffmpeg_escape_text(viral_hook.strip())
            v_filters.append(
                f"drawtext=text='{esc_hook}':x=(w-text_w)/2:y='h*0.025':"
                f"fontsize='h*0.038':fontcolor=yellow:bordercolor=black:borderw=2{font_filter_str}"
            )

            # 3. Line 2: Dynamic PART & Movie Title
            part_label = f"{clean_title.strip()}  •  PART {part_number}/{total_parts}"
            esc_part = ffmpeg_escape_text(part_label)
            v_filters.append(
                f"drawtext=text='{esc_part}':x=(w-text_w)/2:y='h*0.082':"
                f"fontsize='h*0.030':fontcolor=white:bordercolor=black:borderw=2{font_filter_str}"
            )

            # 4. Bottom Retention Bar
            v_filters.append("drawbox=x=0:y='ih*0.93':w=iw:h='ih*0.07':color=black@0.75:t=fill")
            esc_foot = ffmpeg_escape_text("FOLLOW FOR NEXT PART 🍿")
            v_filters.append(
                f"drawtext=text='{esc_foot}':x=(w-text_w)/2:y='h*0.948':"
                f"fontsize='h*0.025':fontcolor=white@0.9{font_filter_str}"
            )

        # Smooth Audio & Video Fade
        fade_dur = min(0.35, current_duration / 3)
        fade_out_st = max(0, current_duration - fade_dur)
        v_filters.append(f"fade=t=in:st=0:d={fade_dur:.2f}")
        v_filters.append(f"fade=t=out:st={fade_out_st:.2f}:d={fade_dur:.2f}")

        # Ensure Even Dimensions for H.264
        v_filters.append("pad='ceil(iw/2)*2':'ceil(ih/2)*2'")

        command = [
            "ffmpeg",
            "-y",
            "-ss", f"{start_time:.3f}",
            "-avoid_negative_ts", "make_zero",
            "-i", input_path,
            "-t", f"{current_duration:.3f}",
            "-map", "0:v:0",
            "-map", "0:a?",
            "-vf", ",".join(v_filters),
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "22",
            "-pix_fmt", "yuv420p",
            "-af", f"afade=t=in:st=0:d={fade_dur:.2f},afade=t=out:st={fade_out_st:.2f}:d={fade_dur:.2f}",
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


# ============================================================
# ZIP & ONEDRIVE HELPERS
# ============================================================

def create_zip(clips, source_name):
    zip_buffer = io.BytesIO()
    zip_base = Path(source_name).stem
    zip_name = f"{safe_filename(zip_base)}_all_clips.zip"

    with zipfile.ZipFile(zip_buffer, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as zip_file:
        for clip in clips:
            zip_file.write(clip, arcname=os.path.basename(clip))

    zip_buffer.seek(0)
    return zip_name, zip_buffer.getvalue()


def get_application_access_token():
    tenant_id = get_secret("AZURE_TENANT_ID")
    client_id = get_secret("AZURE_CLIENT_ID")
    client_secret = get_secret("AZURE_CLIENT_SECRET")

    if not tenant_id or not client_id or not client_secret:
        raise RuntimeError("Azure Secrets missing hain.")

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
        raise RuntimeError(f"Microsoft Token Error ({res.status_code})")

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
        raise RuntimeError(f"OneDrive Access Error ({res.status_code})")
    return res.json()


def get_folder(token, drive_id, folder_name):
    url = f"{GRAPH_BASE_URL}/drives/{quote(drive_id, safe='')}/root:/{quote(folder_name, safe='')}"
    res = requests.get(url, headers=graph_headers(token), timeout=60)
    return res.json() if res.status_code == 200 else None


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
    raise RuntimeError(f"Folder create failed: {res.text[:500]}")


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


def upload_file_to_onedrive(token, drive_id, folder_id, file_path, progress_callback=None):
    filename = os.path.basename(file_path)
    total_size = os.path.getsize(file_path)

    # Small file
    if total_size <= 4 * 1024 * 1024:
        url = (
            f"{GRAPH_BASE_URL}/drives/{quote(drive_id, safe='')}/items/"
            f"{quote(folder_id, safe='')}:/{quote(filename, safe='')}:/content"
        )
        with open(file_path, "rb") as fh:
            requests.put(url, headers={**graph_headers(token), "Content-Type": "video/mp4"}, data=fh, timeout=300)
        if progress_callback:
            progress_callback(1.0)
        return

    # Large upload session
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
    upload_url = session_res.json().get("uploadUrl")
    uploaded = 0

    with open(file_path, "rb") as fh:
        while uploaded < total_size:
            chunk = fh.read(CHUNK_SIZE)
            if not chunk:
                break
            start = uploaded
            end = uploaded + len(chunk) - 1

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
                        break
                except requests.RequestException:
                    time.sleep(2 ** attempt)

            uploaded = end + 1
            if progress_callback:
                progress_callback(uploaded / total_size)


def dispatch_github_workflow(workflow_file):
    github_token = get_secret("GITHUB_TOKEN")
    repo = get_secret("GITHUB_REPO", DEFAULT_REPO)

    if not github_token:
        raise RuntimeError("GITHUB_TOKEN secrets me nahi mila.")

    url = f"https://api.github.com/repos/{repo}/actions/workflows/{quote(workflow_file, safe='')}/dispatches"
    res = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {github_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json={"ref": "main"},
        timeout=60,
    )
    if res.status_code != 204:
        raise RuntimeError(f"Workflow dispatch failed ({res.status_code})")
    return True


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
    st.session_state.ai_analyzed = False


# ============================================================
# UI: HEADER & SIDEBAR
# ============================================================

st.title("🎬 AI Video Studio & Auto-Frame Splitter")
st.caption("AI Title Detector • Dynamic Part Frames • Local Download • OneDrive • GitHub Triggers")

with st.sidebar:
    st.header("🤖 AI Settings")
    gemini_key = st.text_input(
        "Google Gemini API Key (Optional)",
        value=get_secret("GEMINI_API_KEY", ""),
        type="password",
        help="अगर API Key नहीं है तो भी चिंता न करें, Smart Local Engine काम करेगा!",
    )

    st.divider()

    st.header("⚙️ Split Settings")
    clip_duration = st.number_input("Clip Duration (Seconds)", min_value=5, max_value=600, value=30, step=5)
    enable_ai_frame = st.checkbox("🎨 Apply AI Viral Frame & Dynamic Parts", value=True)

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


# ============================================================
# MAIN: VIDEO UPLOAD & AI DETECTION
# ============================================================

uploaded_video = st.file_uploader(
    "📤 Upload Video File",
    type=["mp4", "mov", "mkv", "avi", "webm", "m4v"],
)

if uploaded_video is not None:
    current_name = uploaded_video.name
    previous_name = st.session_state.get("source_name")

    if previous_name and previous_name != current_name:
        cleanup_previous_job()
        st.session_state.source_name = current_name

    # --------------------------------------------------------
    # AI Automatic Name & Hook Detection
    # --------------------------------------------------------
    if not st.session_state.ai_analyzed:
        with st.spinner("🤖 AI फ़ाइल का नाम पढ़कर इंटरनेट से टाइटल और वायरल हुक तैयार कर रहा है..."):
            clean_title, viral_hook = ai_analyze_video_filename(current_name, gemini_key)
            st.session_state.ai_clean_title = clean_title
            st.session_state.ai_hook = viral_hook
            st.session_state.ai_analyzed = True

    # Editable AI Preview Banner
    with st.container(border=True):
        st.subheader("✨ AI Frame Preview (आप इसे बदल भी सकते हैं)")
        col_t1, col_t2 = st.columns(2)
        with col_t1:
            clean_title_input = st.text_input("🎬 Movie / Series Name:", value=st.session_state.ai_clean_title)
        with col_t2:
            viral_hook_input = st.text_input("🔥 Viral Hook Line (Top Banner):", value=st.session_state.ai_hook)

        st.caption(f"💡 Frame Format Example: **{viral_hook_input}** | **{clean_title_input} • PART 1/10**")

    # --------------------------------------------------------
    # SPLIT BUTTON
    # --------------------------------------------------------
    if st.button("✂️ Split Video with AI Frame", type="primary", use_container_width=True):
        job_dir = tempfile.mkdtemp(prefix="ai_video_split_")
        input_filename = safe_filename(uploaded_video.name)
        input_path = os.path.join(job_dir, input_filename)
        clips_dir = os.path.join(job_dir, "clips")
        os.makedirs(clips_dir, exist_ok=True)

        st.session_state.job_dir = job_dir

        try:
            with st.spinner("Saving video to disk..."):
                with open(input_path, "wb") as f:
                    shutil.copyfileobj(uploaded_video, f)

            duration = get_video_duration(input_path)
            total_parts = max(1, math.ceil(duration / float(clip_duration)))

            col_m1, col_m2 = st.columns(2)
            col_m1.metric("Video Length", f"{duration:.1f}s")
            col_m2.metric("Total Clips", total_parts)

            split_prog = st.progress(0, text="Generating clips with AI Frames...")

            def update_p(v):
                split_prog.progress(max(0.0, min(1.0, float(v))), text=f"Rendering Parts: {int(v * 100)}%")

            clips = split_video_with_ai_frame(
                input_path=input_path,
                output_dir=clips_dir,
                clip_duration=clip_duration,
                clean_title=clean_title_input,
                viral_hook=viral_hook_input,
                enable_frame=enable_ai_frame,
                progress_callback=update_p,
            )

            split_prog.progress(1.0, text="All Parts Generated with AI Frames!")
            st.session_state.clips = clips
            st.session_state.split_complete = True

            zip_name, zip_bytes = create_zip(clips, uploaded_video.name)
            st.session_state.download_zip = zip_bytes
            st.session_state.download_zip_name = zip_name

        except Exception as exc:
            st.error("❌ Video Split failed.")
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
            label="📦 Download All Parts (ZIP)",
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
                st.error("❌ OneDrive upload failed.")
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
