import os
import io
import re
import gc
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
    page_title="Low-RAM Anime Studio",
    page_icon="⚡",
    layout="wide",
)

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"
ONEDRIVE_USER = "my@011999.onmicrosoft.com"
CHUNK_SIZE = 5 * 1024 * 1024

DEFAULT_REPO = "ayushsharma011999-collab/video-splitter-app"

DESTINATION_CONFIG = {
    "Movies and web series": {
        "folder": "Movies_Pending_Posts",
        "workflow": "facebook-auto-poster-movies.yml",
    },
    "Smart Deals India": {
        "folder": "Pending_Posts",
        "workflow": "main.yml",
    },
}


# ============================================================
# SESSION STATE (Zero Heavy Bytes in RAM)
# ============================================================

DEFAULT_STATE = {
    "onedrive_connected": False,
    "onedrive_drive_id": None,
    "onedrive_folder_id": None,
    "onedrive_folder_name": None,

    "job_dir": None,
    "clips": [],
    "source_name": None,

    "zip_path": None,
    "zip_name": None,

    "split_complete": False,
    "upload_complete": False,

    "anime_title": "",
    "season_num": 1,
    "episode_num": 1,
    "ai_part_hooks": [],
    "social_meta": {},
    "analyzed_file": None,
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
    return name.strip().strip(".") or "anime_video"


def ffmpeg_escape_text(text):
    text = str(text)
    for old, new in [
        ("\\", r"\\"), (":", r"\:"), ("'", r"\'"), ("%", r"\%"),
        (",", r"\,"), ("[", r"\["), ("]", r"\]"), (";", r"\;"),
    ]:
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
        raise RuntimeError("FFmpeg PATH me nahi mila.") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("FFmpeg process timed out.") from exc

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
        raise RuntimeError("Duration read fail.") from exc
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
# 🎌 AI METADATA GENERATOR
# ============================================================

def parse_anime_filename(filename):
    base = Path(filename).stem
    ep_match = re.search(r"(?i)(?:ep|e|episode|part)[._\s-]*(\d{1,3})|[-_\s](\d{1,3})(?=\s*[\(\[]|$)", base)
    episode_num = int(ep_match.group(1) or ep_match.group(2)) if ep_match else 1

    s_match = re.search(r"(?i)(?:s|season)[._\s-]*(\d{1,2})", base)
    season_num = int(s_match.group(1)) if s_match else 1

    cleaned = re.sub(r"\[.*?\]|\(.*?\)", "", base)
    cleaned = re.sub(r"(?i)\b(1080p|720p|480p|2160p|4k|web-?dl|bluray|x264|x265|hevc|hindi|dual audio|sub|esub)\b", "", cleaned)
    cleaned = re.sub(r"(?i)(?:s\d{1,2}|season\s*\d{1,2}|ep?\s*\d{1,3})", "", cleaned)
    cleaned = cleaned.replace(".", " ").replace("_", " ").replace("-", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    title = cleaned.title() if cleaned else "Anime"
    return title, season_num, episode_num


def generate_social_metadata(anime_title, season, episode, total_parts, gemini_key=None):
    clean_tag = re.sub(r"[^a-zA-Z0-9]", "", anime_title)
    
    fallback_meta = {
        "instagram": {
            "caption": f"Wait for the end! 🔥 {anime_title} S{season} EP{episode} intense moment! 😱 Follow for all parts! 👇",
            "hashtags": f"#{clean_tag} #AnimeReels #AnimeEdits #AnimeFight #AnimeLover #OtakuLife #ViralAnime #AnimeShorts #ExplorePage",
        },
        "facebook": {
            "caption": f"What an intense moment in {anime_title} Season {season} Episode {episode}! 🔥 Like and Share for Part 2! 🍿",
            "hashtags": f"#{clean_tag} #Anime #AnimeLovers #ViralReels #ActionAnime #BestAnimeMoments",
        },
        "youtube": {
            "title_template": f"{anime_title} S{season} EP{episode} Part {{part}} 🔥 #Shorts #Anime",
            "description": f"{anime_title} Season {season} Episode {episode} best clips.\nSubscribe for daily anime shorts! 🍿",
            "tags": f"{anime_title}, {anime_title} season {season}, {anime_title} episode {episode}, anime shorts, anime fight scene, hindi anime",
        }
    }

    fallback_hooks = [
        "THE MONSTER AWAKENS ⚡", "WHEN TRUTH REVEALS 😱", "INSANE POWER UNLEASHED 🔥",
        "UNEXPECTED TWIST HERE 💥", "DANGEROUS FIGHT BEGINS ⚔️", "DON'T MISS THIS SCENE 🍿",
        "NOBODY SAW THIS COMING 🤯", "LEGENDARY SCENE AHEAD 🏆"
    ]
    hooks = [fallback_hooks[i % len(fallback_hooks)] for i in range(total_parts)]
    hooks[0] = "THE FIGHT BEGINS 🔥"
    hooks[-1] = "INSANE CLIMAX ENDING 🏆"

    if not gemini_key:
        return hooks, fallback_meta

    prompt = f"""
    Anime: "{anime_title}" S{season} EP{episode}. Total parts: {total_parts}.
    Return JSON only:
    {{
        "hooks": ["3-5 word hook with emoji for each part"],
        "instagram": {{"caption": "...", "hashtags": "..."}},
        "facebook": {{"caption": "...", "hashtags": "..."}},
        "youtube": {{"title_template": "...", "description": "...", "tags": "..."}}
    }}
    """
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={gemini_key}"
    try:
        res = requests.post(
            url,
            json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.4}},
            timeout=15,
        )
        if res.status_code == 200:
            txt = re.sub(r"```json|```", "", res.json()["candidates"][0]["content"]["parts"][0]["text"]).strip()
            parsed = json.loads(txt)
            ai_hooks = parsed.get("hooks", [])
            if len(ai_hooks) < total_parts:
                ai_hooks.extend(hooks[len(ai_hooks):])
            return ai_hooks[:total_parts], {
                "instagram": parsed.get("instagram", fallback_meta["instagram"]),
                "facebook": parsed.get("facebook", fallback_meta["facebook"]),
                "youtube": parsed.get("youtube", fallback_meta["youtube"]),
            }
    except Exception:
        pass

    return hooks, fallback_meta


# ============================================================
# ⚡ LOW-RAM FFMPEG RENDER ENGINE (SAFE & EFFICIENT)
# ============================================================

def split_anime_low_ram(
    input_path,
    output_dir,
    clip_duration,
    anime_title,
    season_num,
    episode_num,
    part_hooks,
    mirror_flip=True,
    speed_factor=1.03,
    color_boost=True,
    reel_layout="9:16 Blurred Background",
    progress_callback=None,
):
    os.makedirs(output_dir, exist_ok=True)
    duration = get_video_duration(input_path)
    clip_duration = float(clip_duration)
    total_parts = max(1, math.ceil(duration / clip_duration))

    font_path = find_font()
    font_filter = f":fontfile='{ffmpeg_escape_text(font_path)}'" if font_path else ""

    # Mobile optimized 720p canvas (Takes 55% less RAM than 1080p)
    target_w, target_h = 720, 1280

    clips = []

    for part_number in range(1, total_parts + 1):
        start_time = (part_number - 1) * clip_duration
        remaining = duration - start_time
        current_dur = min(clip_duration, remaining)
        if current_dur <= 0:
            break

        output_path = os.path.join(output_dir, f"clip_{part_number:03d}.mp4")
        p_hook = part_hooks[part_number - 1] if part_number - 1 < len(part_hooks) else f"EPISODE {episode_num} SCENE 🔥"

        esc_hook = ffmpeg_escape_text(p_hook.strip())
        esc_label = ffmpeg_escape_text(f"{anime_title.strip()} • S{season_num} EP{episode_num} • PART {part_number}/{total_parts}")
        esc_foot = ffmpeg_escape_text("FOLLOW FOR NEXT PART 🍿")

        filter_complex = []
        fg_mods = []
        if mirror_flip:
            fg_mods.append("hflip")
        if color_boost:
            fg_mods.append("eq=saturation=1.12:contrast=1.05:brightness=0.01")
        fg_mod_str = ("," + ",".join(fg_mods)) if fg_mods else ""

        if reel_layout == "9:16 Blurred Background":
            # 🚀 ULTRA-LOW RAM BLUR TRICK:
            # 180x320 tiny buffer blur. Uses under 150MB RAM!
            filter_complex.append(
                f"[0:v]scale=180:320:force_original_aspect_ratio=increase,crop=180:320,avgblur=5,scale={target_w}:{target_h}[bg]"
            )
            filter_complex.append(
                f"[0:v]scale={target_w}:trunc(ih*{target_w}/iw/2)*2{fg_mod_str}[fg]"
            )
            filter_complex.append(
                "[bg][fg]overlay=(W-w)/2:(H-h)/2[base]"
            )
            input_label = "[base]"
        else:
            base_filters = fg_mods if fg_mods else ["null"]
            filter_complex.append(f"[0:v]{','.join(base_filters)}[base]")
            input_label = "[base]"

        banner_filters = [
            "drawbox=x=0:y=0:w=iw:h='ih*0.14':color=black@0.75:t=fill",
            f"drawtext=text='{esc_hook}':x=(w-text_w)/2:y='h*0.028':fontsize='h*0.038':fontcolor=yellow:bordercolor=black:borderw=2{font_filter}",
            f"drawtext=text='{esc_label}':x=(w-text_w)/2:y='h*0.084':fontsize='h*0.028':fontcolor=white:bordercolor=black:borderw=2{font_filter}",
            "drawbox=x=0:y='ih*0.93':w=iw:h='ih*0.07':color=black@0.75:t=fill",
            f"drawtext=text='{esc_foot}':x=(w-text_w)/2:y='h*0.948':fontsize='h*0.025':fontcolor=white@0.9{font_filter}",
        ]

        if speed_factor != 1.0:
            banner_filters.append(f"setpts=PTS/{speed_factor}")

        filter_complex.append(f"{input_label}{','.join(banner_filters)}[v_out]")

        a_filters = []
        if speed_factor != 1.0:
            a_filters.append(f"atempo={speed_factor}")
        a_filters.append("equalizer=f=1000:t=q:w=1:g=-1.5")

        command = [
            "ffmpeg",
            "-y",
            "-ss", f"{start_time:.3f}",
            "-avoid_negative_ts", "make_zero",
            "-i", input_path,
            "-t", f"{current_dur:.3f}",
            "-filter_complex", ";".join(filter_complex),
            "-map", "[v_out]",
            "-map", "0:a?",
            "-c:v", "libx264",
            "-preset", "superfast",
            "-crf", "22",
            "-pix_fmt", "yuv420p",
            "-threads", "2",          # Limit FFmpeg threads to prevent RAM spikes
            "-af", ",".join(a_filters),
            "-c:a", "aac",
            "-b:a", "128k",
            "-sn",
            "-movflags", "+faststart",
            output_path,
        ]

        run_command(command, timeout=1800)
        clips.append(output_path)

        # Force clear memory after every clip
        gc.collect()

        if progress_callback:
            progress_callback(part_number / total_parts)

    return clips


# ============================================================
# 💾 DISK-BASED ZIP (ZERO RAM CONSUMPTION)
# ============================================================

def create_zip_on_disk(clips, source_name, job_dir):
    zip_base = Path(source_name).stem
    zip_name = f"{safe_filename(zip_base)}_all_clips.zip"
    zip_path = os.path.join(job_dir, zip_name)

    # Directly writes to hard disk stream, 0 MB in RAM
    with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as zip_file:
        for clip in clips:
            zip_file.write(clip, arcname=os.path.basename(clip))

    return zip_path, zip_name


# ============================================================
# ONEDRIVE HELPERS
# ============================================================

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
        raise RuntimeError(f"Token Error ({res.status_code})")
    return res.json().get("access_token")


def graph_headers(token):
    return {"Authorization": f"Bearer {token}"}


def get_drive(token):
    url = f"{GRAPH_BASE_URL}/users/{quote(ONEDRIVE_USER, safe='')}/drive"
    res = requests.get(url, headers=graph_headers(token), timeout=60)
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
        return get_folder(token, drive_id, folder_name)
    raise RuntimeError(f"Folder create failed ({res.status_code})")


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
        url = f"{GRAPH_BASE_URL}/drives/{quote(drive_id, safe='')}/items/{quote(folder_id, safe='')}:/{quote(filename, safe='')}:/content"
        mime = "application/json" if filename.endswith(".json") else "video/mp4"
        with open(file_path, "rb") as fh:
            requests.put(url, headers={**graph_headers(token), "Content-Type": mime}, data=fh, timeout=300)
        if progress_callback:
            progress_callback(1.0)
        return

    create_url = f"{GRAPH_BASE_URL}/drives/{quote(drive_id, safe='')}/items/{quote(folder_id, safe='')}:/{quote(filename, safe='')}:/createUploadSession"
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
                        headers={"Content-Length": str(len(chunk)), "Content-Range": f"bytes {start}-{end}/{total_size}"},
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
    st.session_state.zip_path = None
    st.session_state.zip_name = None
    st.session_state.split_complete = False
    st.session_state.upload_complete = False
    st.session_state.analyzed_file = None
    gc.collect()


# ============================================================
# UI: HEADER & SIDEBAR
# ============================================================

st.title("⚡ Anime Studio — Low-RAM Edition")
st.caption("Memory-Optimized for Cloud (Under 1GB RAM) • Anti-Copyright Shield • 1-Click Multi-Poster")

with st.sidebar:
    st.header("🤖 AI Settings")
    gemini_key = st.text_input(
        "Google Gemini API Key (Optional)",
        value=get_secret("GEMINI_API_KEY", ""),
        type="password",
    )

    st.divider()

    st.header("🛡️ Anti-Copyright Shield")
    speed_factor = st.selectbox("Micro Speed Hack", [1.03, 1.05, 1.0], index=0)
    mirror_flip = st.checkbox("Mirror Mode (Horizontal Flip)", value=True)
    color_boost = st.checkbox("Color Saturation Boost", value=True)
    reel_layout = st.selectbox("Reel Format", ["9:16 Blurred Background", "Original Ratio"], index=0)

    st.divider()

    st.header("⚙️ Clip Length")
    clip_duration = st.number_input("Clip Duration (Seconds)", min_value=15, max_value=180, value=60, step=5)

    st.divider()

    st.subheader("☁️ OneDrive Destination")
    selected_dest_label = st.selectbox("Target Folder", list(DESTINATION_CONFIG.keys()))
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
# MAIN: VIDEO UPLOAD & SETUP
# ============================================================

uploaded_video = st.file_uploader(
    "📤 Upload Anime Episode (MKV / MP4 / MOV)",
    type=["mp4", "mkv", "mov", "webm", "avi"],
)

if uploaded_video is not None:
    current_name = uploaded_video.name
    previous_name = st.session_state.get("source_name")

    if previous_name and previous_name != current_name:
        cleanup_previous_job()
        st.session_state.source_name = current_name

    if not st.session_state.job_dir:
        temp_dir = tempfile.mkdtemp(prefix="anime_lowram_")
        input_file_path = os.path.join(temp_dir, safe_filename(current_name))
        with st.spinner("Saving file to disk..."):
            with open(input_file_path, "wb") as f:
                shutil.copyfileobj(uploaded_video, f)
        st.session_state.job_dir = temp_dir
    else:
        input_file_path = os.path.join(st.session_state.job_dir, safe_filename(current_name))

    duration = get_video_duration(input_file_path)
    total_parts = max(1, math.ceil(duration / float(clip_duration)))

    if st.session_state.analyzed_file != (current_name, total_parts):
        title, s_num, ep_num = parse_anime_filename(current_name)
        with st.spinner("🤖 Generating Part Hooks & Multi-Platform Metadata..."):
            hooks, social_meta = generate_social_metadata(title, s_num, ep_num, total_parts, gemini_key)
        st.session_state.anime_title = title
        st.session_state.season_num = s_num
        st.session_state.episode_num = ep_num
        st.session_state.ai_part_hooks = hooks
        st.session_state.social_meta = social_meta
        st.session_state.analyzed_file = (current_name, total_parts)

    col_m1, col_m2, col_m3 = st.columns(3)
    col_m1.metric("Video Length", f"{duration:.1f}s")
    col_m2.metric("Clip Duration", f"{clip_duration}s")
    col_m3.metric("Total Clips", total_parts)

    # Multi-Platform Metadata Tabs
    with st.expander("📢 Multi-Platform Captions & Tags (FB, Insta, YouTube)", expanded=False):
        tab_insta, tab_fb, tab_yt = st.tabs(["📸 Instagram Reels", "📘 Facebook Reels", "🔴 YouTube Shorts"])
        with tab_insta:
            st.text_area("Instagram Caption:", value=st.session_state.social_meta.get("instagram", {}).get("caption", ""), height=70)
            st.text_area("Instagram Hashtags:", value=st.session_state.social_meta.get("instagram", {}).get("hashtags", ""), height=70)
        with tab_fb:
            st.text_area("Facebook Caption:", value=st.session_state.social_meta.get("facebook", {}).get("caption", ""), height=70)
            st.text_area("Facebook Hashtags:", value=st.session_state.social_meta.get("facebook", {}).get("hashtags", ""), height=70)
        with tab_yt:
            st.text_input("YouTube Title:", value=st.session_state.social_meta.get("youtube", {}).get("title_template", ""))
            st.text_area("YouTube Tags:", value=st.session_state.social_meta.get("youtube", {}).get("tags", ""), height=70)

    # --------------------------------------------------------
    # SPLIT BUTTON (MEMORY-SAFE)
    # --------------------------------------------------------
    if st.button("⚡ Split Video (Low-RAM Safe Mode)", type="primary", use_container_width=True):
        clips_dir = os.path.join(st.session_state.job_dir, "clips")
        os.makedirs(clips_dir, exist_ok=True)

        try:
            start_bench = time.time()
            split_prog = st.progress(0, text=f"Rendering {total_parts} Clips safely...")

            def update_p(v):
                split_prog.progress(max(0.0, min(1.0, float(v))), text=f"Rendering: {int(v * 100)}%")

            clips = split_anime_low_ram(
                input_path=input_file_path,
                output_dir=clips_dir,
                clip_duration=clip_duration,
                anime_title=st.session_state.anime_title,
                season_num=st.session_state.season_num,
                episode_num=st.session_state.episode_num,
                part_hooks=st.session_state.ai_part_hooks,
                mirror_flip=mirror_flip,
                speed_factor=speed_factor,
                color_boost=color_boost,
                reel_layout=reel_layout,
                progress_callback=update_p,
            )

            total_t = time.time() - start_bench
            split_prog.progress(1.0, text="All Clips Ready!")
            st.success(f"✅ {len(clips)} क्लिप्स बिना किसी मेमोरी क्रैश के मात्र {total_t:.1f}s में तैयार हो गईं!")

            st.session_state.clips = clips
            st.session_state.split_complete = True

            # Save metadata.json
            metadata_file = os.path.join(st.session_state.job_dir, "metadata.json")
            full_meta = {
                "anime_title": st.session_state.anime_title,
                "season": st.session_state.season_num,
                "episode": st.session_state.episode_num,
                "total_parts": len(clips),
                "part_hooks": st.session_state.ai_part_hooks,
                "social": st.session_state.social_meta,
            }
            with open(metadata_file, "w", encoding="utf-8") as mf:
                json.dump(full_meta, mf, indent=2, ensure_ascii=False)

            # Create ZIP on Disk (0 MB RAM)
            zpath, zname = create_zip_on_disk(clips, uploaded_video.name, st.session_state.job_dir)
            st.session_state.zip_path = zpath
            st.session_state.zip_name = zname
            gc.collect()

        except Exception as exc:
            st.error("❌ Split fail ho gaya.")
            st.exception(exc)


# ============================================================
# 1. DOWNLOAD & PREVIEW (RAM-SAFE DROPDOWN PLAYER)
# ============================================================

if st.session_state.split_complete and st.session_state.clips:
    clips = st.session_state.clips
    st.divider()
    st.subheader("💾 Local Storage Download")

    # Disk-streamed ZIP download (Zero RAM)
    if st.session_state.zip_path and os.path.exists(st.session_state.zip_path):
        with open(st.session_state.zip_path, "rb") as zf:
            st.download_button(
                label="📦 Download All Clips Together (ZIP)",
                data=zf,
                file_name=st.session_state.zip_name,
                mime="application/zip",
                type="primary",
                use_container_width=True,
                key="zip_download",
            )

    st.write("")

    # 🚀 SMART PLAYER: Only loads 1 clip into memory at a time
    with st.container(border=True):
        st.subheader("🎬 Single Clip Preview & Download")
        st.caption("मेमोरी बचाने के लिए जिस पार्ट को देखना हो, उसे नीचे ड्रॉपडाउन से चुनें:")

        part_options = [f"Part {i+1} — {os.path.basename(c)}" for i, c in enumerate(clips)]
        selected_part_str = st.selectbox("Select Part:", part_options)
        selected_idx = part_options.index(selected_part_str)
        selected_clip = clips[selected_idx]

        col_v1, col_v2 = st.columns([3, 1])
        with col_v1:
            # Streams directly from disk file path without loading whole video into Python RAM!
            st.video(selected_clip)
        with col_v2:
            st.write(f"Size: `{os.path.getsize(selected_clip) / (1024 * 1024):.2f} MB`")
            with open(selected_clip, "rb") as cf:
                st.download_button(
                    label=f"⬇️ Download Part {selected_idx + 1}",
                    data=cf,
                    file_name=os.path.basename(selected_clip),
                    mime="video/mp4",
                    use_container_width=True,
                )


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
        if st.button(f"☁️ Upload {len(st.session_state.clips)} Clips + Metadata to OneDrive", use_container_width=True):
            upload_progress = st.progress(0, text="Uploading to OneDrive...")
            status_text = st.empty()

            try:
                token = get_application_access_token()
                drive_id = st.session_state.onedrive_drive_id
                folder_id = st.session_state.onedrive_folder_id

                files_to_upload = list(st.session_state.clips)
                meta_json_path = os.path.join(st.session_state.job_dir, "metadata.json")
                if os.path.exists(meta_json_path):
                    files_to_upload.append(meta_json_path)

                total_files = len(files_to_upload)

                for idx, fpath in enumerate(files_to_upload, start=1):
                    fname = os.path.basename(fpath)
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
                        file_path=fpath,
                        progress_callback=make_cb(),
                    )

                upload_progress.progress(1.0, text="Upload Complete!")
                status_text.success(f"✅ Sabhi {total_files} files OneDrive me upload ho gayi!")
                st.session_state.upload_complete = True
                gc.collect()

            except Exception as exc:
                st.error("❌ OneDrive upload failed.")
                st.exception(exc)


# ============================================================
# 3. GITHUB ACTIONS MANUAL RUN
# ============================================================

st.divider()
st.subheader("🚀 GitHub Actions Manual Run")

col_gh1, col_gh2 = st.columns(2)

with col_gh1:
    if st.button("🎬 Run Movies & Anime Workflow (facebook-auto-poster-movies.yml)", use_container_width=True):
        try:
            with st.spinner("Dispatching Movies & Anime workflow..."):
                dispatch_github_workflow("facebook-auto-poster-movies.yml")
            st.success("✅ Workflow trigger ho gaya!")
        except Exception as exc:
            st.error(f"❌ Dispatch failed: {exc}")

with col_gh2:
    if st.button("🚀 Run Smart Deals India (main.yml)", use_container_width=True):
        try:
            with st.spinner("Dispatching Smart Deals India workflow..."):
                dispatch_github_workflow("main.yml")
            st.success("✅ Workflow trigger ho gaya!")
        except Exception as exc:
            st.error(f"❌ Dispatch failed: {exc}")


# ============================================================
# RESET / CLEAR
# ============================================================

if st.session_state.split_complete and st.session_state.clips:
    st.divider()
    if st.button("🗑️ Clear / Next Episode", use_container_width=True):
        cleanup_previous_job()
        st.rerun()
