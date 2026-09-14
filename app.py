import os
import json
import re
import shutil
import subprocess
from urllib.parse import urlparse, parse_qs

import requests
import streamlit as st
import msal


# ============================================================
# STREAMLIT CONFIG
# ============================================================

st.set_page_config(
    page_title="Pro Video Studio | Hybrid OneDrive",
    page_icon="🎬",
    layout="wide"
)

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

.status-box {
    background-color: #161b22;
    border: 1px solid #30363d;
    padding: 15px;
    border-radius: 10px;
    margin-bottom: 15px;
}

.metric-value {
    font-size: 24px;
    font-weight: bold;
    color: #58a6ff;
}

.stButton button {
    width: 100%;
    border-radius: 8px;
    font-weight: 600;
}

</style>
""", unsafe_allow_html=True)


# ============================================================
# FFMPEG
# ============================================================

FFMPEG = shutil.which("ffmpeg")

if not FFMPEG:
    st.error(
        "❌ FFmpeg nahi mila. GitHub repository mein "
        "`packages.txt` file check karein."
    )
    st.stop()


# ============================================================
# SESSION STATE
# ============================================================

if "input_file" not in st.session_state:
    st.session_state.input_file = None

if "clips" not in st.session_state:
    st.session_state.clips = []

if "duration" not in st.session_state:
    st.session_state.duration = 0

if "onedrive_access_token" not in st.session_state:
    st.session_state.onedrive_access_token = None

if "onedrive_refresh_token" not in st.session_state:
    st.session_state.onedrive_refresh_token = None


# ============================================================
# TOKEN FILE
# ============================================================

TOKEN_FILE = "ms_token.json"


def save_refresh_token(refresh_token):

    if not refresh_token:
        return

    try:
        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {"refresh_token": refresh_token},
                f,
                indent=4
            )
    except Exception as e:
        st.warning(f"Token save nahi ho paya: {e}")


def load_refresh_token():

    if not os.path.exists(TOKEN_FILE):
        return None

    try:
        with open(TOKEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        return data.get("refresh_token")

    except Exception:
        return None


def delete_saved_token():

    if os.path.exists(TOKEN_FILE):
        try:
            os.remove(TOKEN_FILE)
        except Exception:
            pass

    st.session_state.onedrive_access_token = None
    st.session_state.onedrive_refresh_token = None


# ============================================================
# MICROSOFT / ONEDRIVE CONFIG
# ============================================================

SCOPES = [
    "Files.ReadWrite.All",
    
]

REDIRECT_URI = "http://localhost"


def get_msal_app(client_id, tenant_id, client_secret):

    authority = (
        f"https://login.microsoftonline.com/{tenant_id}"
    )

    return msal.ConfidentialClientApplication(
        client_id=client_id,
        authority=authority,
        client_credential=client_secret
    )


# ============================================================
# CREATE ONEDRIVE FOLDER
# ============================================================

def ensure_onedrive_folder(access_token, folder_name):

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    check_url = (
        "https://graph.microsoft.com/v1.0/"
        f"me/drive/root:/{folder_name}"
    )

    response = requests.get(
        check_url,
        headers=headers,
        timeout=30
    )

    if response.status_code == 200:
        return True, "Folder already exists"

    create_url = (
        "https://graph.microsoft.com/v1.0/"
        "me/drive/root/children"
    )

    payload = {
        "name": folder_name,
        "folder": {},
        "@microsoft.graph.conflictBehavior": "fail"
    }

    response = requests.post(
        create_url,
        headers=headers,
        json=payload,
        timeout=30
    )

    if response.status_code in [200, 201]:
        return True, "Folder created"

    # Folder may have been created meanwhile
    if response.status_code == 409:
        return True, "Folder already exists"

    return False, response.text


# ============================================================
# ONEDRIVE LARGE FILE UPLOAD
# ============================================================

def upload_large_file_to_onedrive(
    file_path,
    access_token,
    folder_name="Pending_Posts"
):

    if not os.path.exists(file_path):
        return False, "Local file nahi mili."

    folder_ok, folder_msg = ensure_onedrive_folder(
        access_token,
        folder_name
    )

    if not folder_ok:
        return False, f"Folder error: {folder_msg}"

    file_name = os.path.basename(file_path)

    session_url = (
        "https://graph.microsoft.com/v1.0/"
        f"me/drive/root:/{folder_name}/{file_name}:"
        "/createUploadSession"
    )

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    payload = {
        "item": {
            "@microsoft.graph.conflictBehavior": "rename",
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
        return False, str(e)

    if response.status_code not in [200, 201]:

        return False, (
            f"Upload session create nahi hua: "
            f"{response.status_code} - {response.text}"
        )

    upload_url = response.json().get("uploadUrl")

    if not upload_url:
        return False, "Upload URL nahi mila."

    file_size = os.path.getsize(file_path)

    # 5 MB — 320 KiB ka exact multiple
    chunk_size = 5 * 1024 * 1024

    start = 0

    try:

        with open(file_path, "rb") as f:

            while start < file_size:

                f.seek(start)

                chunk = f.read(chunk_size)

                if not chunk:
                    break

                end = start + len(chunk) - 1

                upload_headers = {
                    "Content-Length": str(len(chunk)),
                    "Content-Range":
                        f"bytes {start}-{end}/{file_size}"
                }

                upload_response = requests.put(
                    upload_url,
                    headers=upload_headers,
                    data=chunk,
                    timeout=120
                )

                if upload_response.status_code not in [
                    200,
                    201,
                    202
                ]:

                    return False, (
                        "Upload failed: "
                        f"{upload_response.status_code} "
                        f"{upload_response.text}"
                    )

                start = end + 1

    except Exception as e:

        return False, f"Upload exception: {e}"

    return True, "Upload successful"


# ============================================================
# VIDEO DURATION
# ============================================================

def get_video_duration(video_path):

    cmd = [
        FFMPEG,
        "-i",
        video_path
    ]

    result = subprocess.run(
        cmd,
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
    clip_duration=60,
    aspect_ratio="9:16",
    watermark_text=""
):

    os.makedirs(
        output_dir,
        exist_ok=True
    )

    duration = get_video_duration(video_path)

    if duration <= 0:
        raise Exception(
            "Video duration read nahi ho paayi."
        )

    total_clips = int(duration // clip_duration)

    if duration % clip_duration > 0:
        total_clips += 1

    clips = []

    # --------------------------------------------------------
    # VIDEO SIZE
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


    safe_watermark = ""

    if watermark_text:

        safe_watermark = (
            watermark_text
            .replace("'", "")
            .replace(":", "")
        )


    progress_bar = st.progress(0)

    status_text = st.empty()


    # --------------------------------------------------------
    # SPLIT LOOP
    # --------------------------------------------------------

    for i in range(total_clips):

        start_time = i * clip_duration

        output_file = os.path.join(
            output_dir,
            f"Reel_Part_{i + 1}.mp4"
        )

        part_text = (
            f"Part {i + 1}/{total_clips}"
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


        cmd = [

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
            cmd,
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
            clips.append(output_file)


        progress_bar.progress(
            (i + 1) / total_clips
        )


        status_text.text(
            f"⚡ Processing Clip "
            f"{i + 1}/{total_clips}"
        )


    status_text.text(
        "✅ Video processing complete!"
    )

    return clips


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

Streamlit + OneDrive + GitHub Actions Hybrid System

</p>

</div>
""", unsafe_allow_html=True)


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:

    st.markdown(
        "## ☁️ Microsoft / Azure"
    )

    # --------------------------------------------------------
    # Use Streamlit Secrets if available
    # Otherwise user can enter manually
    # --------------------------------------------------------

    try:

        default_client_id = st.secrets.get(
            "AZURE_CLIENT_ID",
            ""
        )

        default_tenant_id = st.secrets.get(
            "AZURE_TENANT_ID",
            ""
        )

        default_client_secret = st.secrets.get(
            "AZURE_CLIENT_SECRET",
            ""
        )

    except Exception:

        default_client_id = ""
        default_tenant_id = ""
        default_client_secret = ""


    client_id = st.text_input(
        "Azure Client ID",
        value=default_client_id
    )

    tenant_id = st.text_input(
        "Azure Tenant ID",
        value=default_tenant_id
    )

    client_secret = st.text_input(
        "Azure Client Secret",
        value=default_client_secret,
        type="password"
    )


    st.markdown("---")


    st.markdown(
        "## 📘 Facebook Post Settings"
    )


    fb_page_id = st.text_input(
        "Facebook Page ID"
    )


    fb_post_type = st.selectbox(

        "Post Type",

        [
            "Facebook Reel (Short)",
            "Normal Page Video Post"
        ]

    )


    default_caption = st.text_area(

        "Default Caption",

        value=(
            "Check out this amazing clip! 🔥"
        )

    )


    st.info(
        "Facebook Access Token ab OneDrive metadata "
        "mein save nahi hoga. Phase 3 mein isko "
        "GitHub Secrets mein rakhenge."
    )


    st.markdown("---")


    st.markdown(
        "## 🎛️ Video Settings"
    )


    uploaded_file = st.file_uploader(

        "📁 Upload Video",

        type=[
            "mp4",
            "mov",
            "avi",
            "mkv"
        ]

    )


    clip_duration = st.slider(

        "Clip Duration (Seconds)",

        min_value=15,
        max_value=120,
        value=60,
        step=15

    )


    aspect_ratio = st.selectbox(

        "Output Format",

        [
            "9:16 (Vertical / Reels)",
            "16:9 (Landscape)",
            "1:1 (Square)"
        ]

    )


    watermark_text = st.text_input(

        "🏷️ Watermark",

        placeholder="@YourPage"

    )


# ============================================================
# ONEDRIVE LOGIN
# ============================================================

if (
    client_id
    and tenant_id
    and client_secret
):

    st.markdown(
        "### ☁️ OneDrive Connection"
    )

    try:

        msal_app = get_msal_app(
            client_id,
            tenant_id,
            client_secret
        )


        # ----------------------------------------------------
        # TRY SAVED REFRESH TOKEN
        # ----------------------------------------------------

        saved_token = load_refresh_token()


        if (
            not st.session_state.onedrive_access_token
            and saved_token
        ):

            with st.spinner(
                "Connecting to saved Microsoft session..."
            ):

                result = (
                    msal_app.acquire_token_by_refresh_token(
                        saved_token,
                        scopes=SCOPES
                    )
                )


            if "access_token" in result:

                st.session_state.onedrive_access_token = (
                    result["access_token"]
                )

                new_refresh_token = result.get(
                    "refresh_token",
                    saved_token
                )

                st.session_state.onedrive_refresh_token = (
                    new_refresh_token
                )

                save_refresh_token(
                    new_refresh_token
                )


        # ----------------------------------------------------
        # FIRST LOGIN
        # ----------------------------------------------------

        if not st.session_state.onedrive_access_token:

            st.warning(
                "⚠️ OneDrive abhi connected nahi hai."
            )


            auth_url = (
                msal_app
                .get_authorization_request_url(
                    scopes=SCOPES,
                    redirect_uri=REDIRECT_URI
                )
            )


            st.markdown(
                f"### [🔗 Microsoft / OneDrive Login]"
                f"({auth_url})"
            )


            st.info(
                "Login complete hone ke baad browser "
                "`http://localhost/?code=...` par jayega. "
                "Address bar ka poora URL copy karke niche paste karein."
            )


            redirected_url = st.text_input(

                "Microsoft redirect URL paste karein",

                placeholder=(
                    "http://localhost/?code=..."
                )

            )


            if st.button(
                "🔐 Verify & Connect OneDrive",
                type="primary"
            ):

                if not redirected_url:

                    st.error(
                        "Redirect URL paste karein."
                    )

                else:

                    try:

                        parsed_url = urlparse(
                            redirected_url
                        )

                        query = parse_qs(
                            parsed_url.query
                        )


                        if "code" not in query:

                            st.error(
                                "URL mein authorization code "
                                "nahi mila."
                            )

                        else:

                            authorization_code = (
                                query["code"][0]
                            )


                            result = (
                                msal_app
                                .acquire_token_by_authorization_code(

                                    authorization_code,

                                    scopes=SCOPES,

                                    redirect_uri=REDIRECT_URI

                                )
                            )


                            if "access_token" in result:

                                st.session_state.onedrive_access_token = (
                                    result["access_token"]
                                )


                                refresh_token = result.get(
                                    "refresh_token"
                                )


                                st.session_state.onedrive_refresh_token = (
                                    refresh_token
                                )


                                if refresh_token:

                                    save_refresh_token(
                                        refresh_token
                                    )


                                st.success(
                                    "✅ OneDrive connected successfully!"
                                )


                                st.rerun()


                            else:

                                st.error(
                                    "Microsoft Login Failed:\n\n"
                                    + result.get(
                                        "error_description",
                                        str(result)
                                    )
                                )


                    except Exception as e:

                        st.error(
                            f"Authentication Error: {e}"
                        )


        else:

            col1, col2 = st.columns(
                [3, 1]
            )


            with col1:

                st.success(
                    "✅ OneDrive Connected"
                )


            with col2:

                if st.button(
                    "Disconnect"
                ):

                    delete_saved_token()

                    st.rerun()


    except Exception as e:

        st.error(
            f"Microsoft configuration error: {e}"
        )


else:

    st.warning(
        "👈 Azure Client ID, Tenant ID aur Client Secret "
        "enter karein."
    )


# ============================================================
# VIDEO UPLOAD TO TEMP
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
        ) as f:

            f.write(
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
# MAIN WORKSPACE
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


    st.markdown(
        "## 🚀 Render Workspace"
    )


    if not st.session_state.onedrive_access_token:

        st.info(
            "Video split karne se pehle "
            "OneDrive connect karein."
        )


    else:

        if st.button(
            "⚡ Start Splitting & Processing",
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


            try:

                with st.spinner(
                    "FFmpeg video clips generate kar raha hai..."
                ):

                    st.session_state.clips = (

                        split_video(

                            st.session_state.input_file,

                            output_dir,

                            clip_duration,

                            aspect_ratio,

                            watermark_text

                        )

                    )


                st.success(
                    f"✅ {len(st.session_state.clips)} clips generated!"
                )


            except Exception as e:

                st.error(
                    "❌ Processing failed"
                )

                st.code(
                    str(e)
                )


# ============================================================
# CLIPS
# ============================================================

if st.session_state.clips:

    st.markdown("---")

    st.markdown(
        "## 📦 Generated Clips"
    )


    clip_columns = st.columns(2)


    for index, clip in enumerate(
        st.session_state.clips
    ):

        with clip_columns[
            index % 2
        ]:

            st.markdown(
                f"### 🎞️ Part {index + 1}"
            )

            st.video(
                clip
            )

            with open(
                clip,
                "rb"
            ) as f:

                st.download_button(

                    label=(
                        f"⬇️ Download Part {index + 1}"
                    ),

                    data=f.read(),

                    file_name=os.path.basename(
                        clip
                    ),

                    mime="video/mp4",

                    key=f"download_{index}"

                )


# ============================================================
# SEND TO ONEDRIVE
# ============================================================

if (

    st.session_state.clips

    and st.session_state.onedrive_access_token

):

    st.markdown("---")


    st.markdown(
        "## ☁️ Send to Background Automation"
    )


    st.write(
        "Clips OneDrive ke `Pending_Posts` folder "
        "mein upload hongi."
    )


    if st.button(

        "☁️ Upload All Clips to OneDrive",

        type="primary",

        use_container_width=True

    ):

        if not fb_page_id:

            st.error(
                "Facebook Page ID enter karein."
            )


        else:

            total_items = (
                len(
                    st.session_state.clips
                )
                + 1
            )


            progress = st.progress(0)

            status = st.empty()

            upload_failed = False


            # ------------------------------------------------
            # UPLOAD CLIPS
            # ------------------------------------------------

            for index, clip in enumerate(

                st.session_state.clips

            ):

                status.text(

                    f"☁️ Uploading "
                    f"{os.path.basename(clip)}..."

                )


                success, message = (

                    upload_large_file_to_onedrive(

                        clip,

                        st.session_state.onedrive_access_token,

                        "Pending_Posts"

                    )

                )


                if not success:

                    st.error(
                        f"{os.path.basename(clip)}: "
                        f"{message}"
                    )

                    upload_failed = True


                progress.progress(

                    (index + 1)
                    / total_items

                )


            # ------------------------------------------------
            # METADATA
            # ------------------------------------------------

            if not upload_failed:

                metadata = {

                    "facebook_page_id":
                        fb_page_id,

                    "caption":
                        default_caption,

                    "post_type":
                        fb_post_type,

                    "total_clips":
                        len(
                            st.session_state.clips
                        ),

                    "clip_duration_seconds":
                        clip_duration

                }


                metadata_path = (
                    "/tmp/metadata.json"
                )


                with open(

                    metadata_path,

                    "w",

                    encoding="utf-8"

                ) as f:

                    json.dump(

                        metadata,

                        f,

                        indent=4,

                        ensure_ascii=False

                    )


                status.text(
                    "☁️ Uploading metadata.json..."
                )


                meta_success, meta_message = (

                    upload_large_file_to_onedrive(

                        metadata_path,

                        st.session_state.onedrive_access_token,

                        "Pending_Posts"

                    )

                )


                progress.progress(1.0)


                if meta_success:

                    status.empty()

                    st.success(
                        "🎉 Upload complete! "
                        "Clips OneDrive ke Pending_Posts "
                        "folder mein ready hain."
                    )


                    st.info(
                        "Ab next phase mein GitHub Actions "
                        "in clips ko automatically Facebook "
                        "par post karega."
                    )


                else:

                    st.error(
                        f"metadata.json upload failed: "
                        f"{meta_message}"
                    )


else:

    if not st.session_state.input_file:

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
        Sidebar se video upload karein.
        </p>

        </div>
        """, unsafe_allow_html=True)
