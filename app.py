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
    page_title="AI Video Studio & Multi-Hook Splitter",
    page_icon="🎬",
    layout="wide",
)

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"
ONEDRIVE_USER = "my@011999.onmicrosoft.com"
CHUNK_SIZE = 5 * 1024 * 1024

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

    # AI Data
    "ai_clean_title": "",
    "ai_part_hooks": [],
    "ai_analyzed_for": None,  # (filename, total_parts)
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
# 🤖 AI MULTI-HOOK ENGINE (HAR PART KE LIYE ALAG HOOK)
# ============================================================

def get_fallback_hooks(title, total_parts):
    """अगर API Key न हो तो कहानी के हिसाब से अलग-अलग हुक तैयार करना"""
    pool = [
        "MASS ENTRY SCENE 🔥",
        "WHEN THE TRUTH REVEALS 😱",
        "UNEXPECTED TWIST HERE 💥",
        "INTENSE CONFRONTATION ⚡",
        "NOBODY EXPECTED THIS 🤯",
        "THE MASTER PLAN UNVEILED 🎯",
        "DANGEROUS FIGHT BEGINS ⚔️",
        "DON'T MISS THIS SCENE 🍿",
        "SUSPENSE AT ITS PEAK 👁️",
        "HIGH VOLTAGE ACTION 💣",
        "WHEN TABLES TURNED 🔄",
        "EPIC CLIMAX SCENE 🏆",
    ]
    hooks = []
    for i in range(total_parts):
        if i == 0:
            hooks.append("MASS ENTRY SCENE 🔥")
        elif i == total_parts - 1:
            hooks.append("EPIC CLIMAX SCENE 🏆")
        else:
            hooks.append(pool[(i - 1) % len(pool)])
    return hooks


def ai_generate_multi_part_hooks(filename, total_parts, gemini_api_key=None):
    """Google Gemini AI से हर पार्ट के लिए अलग-अलग हुक जनरेट करना"""
    base = Path(filename).stem
    cleaned = re.sub(
        r"(?i)\b(1080p|720p|480p|2160p|4k|web-?dl|bluray|hdrip|x264|x265|hevc|hindi|english|dual audio|aac|sub|esub)\b",
        "",
        base,
    )
    cleaned = cleaned.replace(".", " ").replace("_", " ").replace("-", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    fallback_title = cleaned.title() if cleaned else "MOVIE CLIP"

    if not gemini_api_key:
        return fallback_title, get_fallback_hooks(fallback_title, total_parts)

    prompt = f"""
    You are an expert viral movie/series clip editor for Facebook Reels.
    Analyze this uploaded video filename: "{filename}"
    Total clips to create: {total_parts}.

    Tasks:
    1. Identify the official Movie or Web Series title.
    2. Generate exactly {total_parts} UNIQUE, highly engaging viral hooks (one hook for each part, from Part 1 to Part {total_parts}).
       - Each hook must be 3-5 words with 1 emoji (e.g. "MASS ENTRY SCENE 🔥", "WAIT FOR THE TWIST 😱", "CLIMAX FIGHT SCENE ⚔️").
       - Maintain story progression (Part 1 = Entry/Setup, Middle Parts = Action/Suspense/Twists, Final Part = Climax/Ending).

    Return ONLY a raw JSON object (NO markdown backticks):
    {{
        "clean_title": "Official Title",
        "hooks": [
            "Part 1 Hook",
            "Part 2 Hook"
        ]
    }}
    """

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={gemini_api_key}"

    try:
        res = requests.post(
            url,
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.4},
            },
            timeout=18,
        )
        if res.status_code == 200:
            data = res.json()
            raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
            raw_text = re.sub(r"```json|```", "", raw_text).strip()
            parsed = json.loads(raw_text)
            title = parsed.get("clean_title", fallback_title)
            hooks = parsed.get("hooks", [])

            # Make sure we have exactly total_parts hooks
            if len(hooks) < total_parts:
                fallbacks = get_fallback_hooks(title, total_parts)
                hooks.extend(fallbacks[len(hooks):])
            return title, hooks[:total_parts]
    except Exception:
        pass

    return fallback_title, get_fallback_hooks(fallback_title, total_parts)


# ============================================================
# 🎨 VIDEO SPLITTER (WITH UNIQUE HOOK ON EACH PART)
# ============================================================

def split_video_with_unique_hooks(
    input_path,
    output_dir,
    clip_duration,
    clean_title,
    part_hooks,
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
        # UNIQUE HOOK FOR THIS CURRENT PART
        # ----------------------------------------------------
        part_index = part_number - 1
        current_hook = (
            part_hooks[part_index]
            if part_index < len(part_hooks)
            else f"PART {part_number} SCENE 🔥"
        )

        v_filters = []

        if enable_frame:
            # 1. Top Header Background
            v_filters.append("drawbox=x=0:y=0:w=iw:h='ih*0.14':color=black@0.75:t=fill")

            # 2. Line 1: Unique Viral Hook for this specific part
            esc_hook = ffmpeg_escape_text(current_hook.strip())
            v_filters.append(
                f"drawtext=text='{esc_hook}':x=(w-text_w)/2:y='h*0.025':"
                f"fontsize='h*0.038':fontcolor=yellow:bordercolor=black:borderw=2{font_filter_str}"
            )

            # 3. Line 2: Movie Title + Dynamic Part Number
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
    st.session_state.ai_analyzed_for = None


# ============================================================
# UI: HEADER & SIDEBAR
# ============================================================

st.title("🎬 AI Video Studio & Multi-Hook Splitter")
st.caption("Auto Storyline Hooks for Every Part • Local Download • OneDrive • GitHub Triggers")

with st.sidebar:
    st.header("🤖 AI Settings")
    gemini_key = st.text_input(
        "Google Gemini API Key (Optional)",
        value=get_secret("GEMINI_API_KEY", ""),
        type="password",
        help="API Key डालेंगे तो AI स्टोरी के अनुसार बेस्ट हुक जनरेट करेगा। नहीं तो स्मार्ट ऑफ़लाइन इंजन चलेगा।",
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
# MAIN: UPLOAD & MULTI-PART HOOK CONFIGURATION
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

    # Save temp file once to measure duration and compute total parts
    if not st.session_state.job_dir:
        temp_dir = tempfile.mkdtemp(prefix="ai_video_split_")
        input_file_path = os.path.join(temp_dir, safe_filename(current_name))
        with st.spinner("Reading video duration..."):
            with open(input_file_path, "wb") as f:
                shutil.copyfileobj(uploaded_video, f)
        st.session_state.job_dir = temp_dir
    else:
        input_file_path = os.path.join(st.session_state.job_dir, safe_filename(current_name))

    video_duration = get_video_duration(input_file_path)
    total_parts = max(1, math.ceil(video_duration / float(clip_duration)))

    # --------------------------------------------------------
    # AI MULTI-HOOK GENERATOR FOR ALL PARTS
    # --------------------------------------------------------
    analysis_key = (current_name, total_parts)
    if st.session_state.ai_analyzed_for != analysis_key:
        with st.spinner(f"🤖 AI {total_parts} पार्ट्स के लिए अलग-अलग वायरल हुक जनरेट कर रहा है..."):
            title, hooks = ai_generate_multi_part_hooks(current_name, total_parts, gemini_key)
            st.session_state.ai_clean_title = title
            st.session_state.ai_part_hooks = hooks
            st.session_state.ai_analyzed_for = analysis_key

    # --------------------------------------------------------
    # INTERACTIVE EDITABLE HOOKS UI
    # --------------------------------------------------------
    col_m1, col_m2 = st.columns(2)
    col_m1.metric("Video Length", f"{video_duration:.1f}s")
    col_m2.metric("Total Clips To Generate", total_parts)

    with st.container(border=True):
        st.subheader("🎬 Movie / Web Series Name")
        clean_title_input = st.text_input("Title (हर क्लिप पर दिखेगा):", value=st.session_state.ai_clean_title)

        st.subheader("🔥 Hooks for Each Part (हर पार्ट का अपना अलग हुक)")
        st.caption("AI ने हर पार्ट के लिए अलग हुक तय किया है। आप चाहें तो नीचे किसी भी पार्ट का हुक एडिट कर सकते हैं:")

        updated_hooks = []
        # Display 2 columns for neat layout
        c1, c2 = st.columns(2)
        for i in range(total_parts):
            col = c1 if i % 2 == 0 else c2
            current_val = (
                st.session_state.ai_part_hooks[i]
                if i < len(st.session_state.ai_part_hooks)
                else f"PART {i+1} SCENE 🔥"
            )
            val = col.text_input(f"📍 Part {i+1} Hook:", value=current_val, key=f"hook_input_{i}")
            updated_hooks.append(val)

        st.session_state.ai_part_hooks = updated_hooks

    # --------------------------------------------------------
    # SPLIT EXECUTION BUTTON
    # --------------------------------------------------------
    if st.button("✂️ Split Video with Unique Hooks on Each Part", type="primary", use_container_width=True):
        clips_dir = os.path.join(st.session_state.job_dir, "clips")
        os.makedirs(clips_dir, exist_ok=True)

        try:
            split_prog = st.progress(0, text="Generating clips with unique part hooks...")

            def update_p(v):
                split_prog.progress(max(0.0, min(1.0, float(v))), text=f"Rendering Parts: {int(v * 100)}%")

            clips = split_video_with_unique_hooks(
                input_path=input_file_path,
                output_dir=clips_dir,
                clip_duration=clip_duration,
                clean_title=clean_title_input,
                part_hooks=st.session_state.ai_part_hooks,
                enable_frame=enable_ai_frame,
                progress_callback=update_p,
            )

            split_prog.progress(1.0, text="All Unique-Hook Parts Generated!")
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
            label="📦 Download All Parts Together (ZIP)",
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
        part_hook_used = (
            st.session_state.ai_part_hooks[idx - 1]
            if idx - 1 < len(st.session_state.ai_part_hooks)
            else ""
        )

        with st.container(border=True):
            col_info, col_btn = st.columns([3, 1])
            with col_info:
                st.write(f"**Part {idx}** — `{part_hook_used}` ({c_size:.2f} MB)")
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
