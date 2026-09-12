import os
import time
import subprocess
import zipfile
import shutil
from pathlib import Path

import requests
import streamlit as st
import imageio_ffmpeg


# ============================================================
# STREAMLIT CONFIG
# ============================================================

st.set_page_config(
    page_title="FB Reels Splitter & Auto-Poster",
    page_icon="🎥",
    layout="wide",
)


# ============================================================
# FFmpeg SETUP
# ============================================================

def get_ffmpeg_path():
    """
    Get FFmpeg executable from imageio-ffmpeg.
    Falls back to system ffmpeg if available.
    """

    try:
        ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()

        if ffmpeg_path and os.path.exists(ffmpeg_path):
            return ffmpeg_path

    except Exception:
        pass

    system_ffmpeg = shutil.which("ffmpeg")

    if system_ffmpeg:
        return system_ffmpeg

    return None


FFMPEG_PATH = get_ffmpeg_path()


# ============================================================
# PAGE HEADER
# ============================================================

st.title("🎥 Auto Video Splitter & Facebook Reels Poster")

st.write(
    "Upload MP4, MKV, AVI or MOV video. "
    "The app will split it into clips, convert them to 9:16 vertical format "
    "and optionally publish them as Facebook Reels."
)


# ============================================================
# FFmpeg STATUS
# ============================================================

if FFMPEG_PATH:
    st.success(f"✅ FFmpeg ready: `{FFMPEG_PATH}`")
else:
    st.error(
        "❌ FFmpeg executable nahi mila. "
        "Please make sure `imageio-ffmpeg` is installed in requirements.txt."
    )


# ============================================================
# SIDEBAR
# ============================================================

st.sidebar.header("🔑 Facebook Credentials")

fb_page_id = st.sidebar.text_input(
    "Facebook Page ID",
    type="password",
    help="Apna Facebook Page ID enter karein.",
)

fb_access_token = st.sidebar.text_input(
    "Page Access Token",
    type="password",
    help="Apna Facebook Page Access Token enter karein.",
)


st.sidebar.header("⏱️ Posting Delay")

delay_minutes = st.sidebar.number_input(
    "Reels ke beech delay (minutes)",
    min_value=0,
    max_value=1440,
    value=5,
    step=1,
    help="0 = reels sequentially bina delay ke post hongi.",
)


st.sidebar.header("🔄 Retry Settings")

max_retries = st.sidebar.number_input(
    "Maximum retries",
    min_value=1,
    max_value=5,
    value=3,
    step=1,
)


# ============================================================
# SESSION STATE
# ============================================================

if "clips" not in st.session_state:
    st.session_state.clips = []

if "posted_count" not in st.session_state:
    st.session_state.posted_count = 0

if "is_stopped_due_to_error" not in st.session_state:
    st.session_state.is_stopped_due_to_error = False

if "processing_complete" not in st.session_state:
    st.session_state.processing_complete = False


# ============================================================
# MAIN SETTINGS
# ============================================================

st.subheader("1️⃣ Upload & Split Settings")


uploaded_file = st.file_uploader(
    "Upload Video",
    type=[
        "mp4",
        "mkv",
        "avi",
        "mov",
        "webm",
        "m4v",
    ],
    help="Supported formats: MP4, MKV, AVI, MOV, WEBM, M4V",
)


col1, col2, col3 = st.columns(3)


with col1:

    time_unit = st.selectbox(
        "Time Unit",
        ["Seconds", "Minutes"],
        index=0,
    )


with col2:

    if time_unit == "Seconds":

        duration_input = st.number_input(
            "Clip Duration (Seconds)",
            min_value=1,
            max_value=600,
            value=30,
            step=5,
        )

        chunk_duration = int(duration_input)

    else:

        duration_input = st.number_input(
            "Clip Duration (Minutes)",
            min_value=1,
            max_value=10,
            value=1,
            step=1,
        )

        chunk_duration = int(duration_input * 60)


with col3:

    custom_caption_prefix = st.text_input(
        "Reels Caption",
        value="Follow My Page for More Reels!",
    )


# ============================================================
# HELPER: SAFE FILENAMES
# ============================================================

def safe_filename(filename):

    filename = os.path.basename(filename)

    allowed = (
        "abcdefghijklmnopqrstuvwxyz"
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "0123456789"
        "._-"
    )

    filename = "".join(
        char if char in allowed else "_"
        for char in filename
    )

    return filename


# ============================================================
# HELPER: RUN FFMPEG
# ============================================================

def run_ffmpeg(command):

    if not FFMPEG_PATH:
        return {
            "success": False,
            "error": "FFmpeg executable not found.",
        }

    try:

        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )

        stderr = result.stderr or ""
        stdout = result.stdout or ""

        if result.returncode != 0:

            return {
                "success": False,
                "returncode": result.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "error": stderr[-5000:] or "FFmpeg failed.",
            }

        return {
            "success": True,
            "returncode": result.returncode,
            "stdout": stdout,
            "stderr": stderr,
        }

    except FileNotFoundError:

        return {
            "success": False,
            "error": "FFmpeg executable could not be started.",
        }

    except Exception as e:

        return {
            "success": False,
            "error": str(e),
        }


# ============================================================
# GET VIDEO DURATION
# ============================================================

def get_video_duration(video_path):

    if not FFMPEG_PATH:
        return None

    try:

        ffprobe_path = imageio_ffmpeg.get_ffmpeg_exe()

        command = [
            ffprobe_path,
            "-i",
            video_path,
        ]

        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )

        output = result.stderr or ""

        import re

        match = re.search(
            r"Duration:\s*(\d+):(\d+):([\d.]+)",
            output,
        )

        if not match:
            return None

        hours = int(match.group(1))
        minutes = int(match.group(2))
        seconds = float(match.group(3))

        return (
            hours * 3600
            + minutes * 60
            + seconds
        )

    except Exception:
        return None


# ============================================================
# FACEBOOK REELS API
# ============================================================

def post_reel_to_facebook(
    video_path,
    caption,
    page_id,
    access_token,
):

    try:

        # ----------------------------------------------------
        # STEP 1: INITIALIZE
        # ----------------------------------------------------

        init_url = (
            f"https://graph.facebook.com/v18.0/"
            f"{page_id}/video_reels"
        )

        init_payload = {
            "upload_phase": "start",
            "access_token": access_token,
        }

        init_response = requests.post(
            init_url,
            data=init_payload,
            timeout=60,
        )

        try:
            init_json = init_response.json()
        except Exception:
            init_json = {}

        if init_response.status_code >= 400:

            error = (
                init_json
                .get("error", {})
                .get(
                    "message",
                    f"Facebook API error: HTTP {init_response.status_code}",
                )
            )

            return {
                "success": False,
                "error": error,
            }

        if "video_id" not in init_json:

            error = (
                init_json
                .get("error", {})
                .get(
                    "message",
                    "Facebook Reel upload session initialize nahi hua.",
                )
            )

            return {
                "success": False,
                "error": error,
            }

        video_id = init_json["video_id"]

        upload_url = init_json.get("upload_url")

        if not upload_url:

            upload_url = (
                f"https://rupload.facebook.com/"
                f"video-reels/{video_id}"
            )


        # ----------------------------------------------------
        # STEP 2: UPLOAD VIDEO
        # ----------------------------------------------------

        if not os.path.exists(video_path):

            return {
                "success": False,
                "error": f"Video file not found: {video_path}",
            }

        file_size = os.path.getsize(video_path)

        headers = {
            "Authorization": f"OAuth {access_token}",
            "offset": "0",
            "file_size": str(file_size),
        }

        with open(video_path, "rb") as video_file:

            upload_response = requests.post(
                upload_url,
                headers=headers,
                data=video_file,
                timeout=900,
            )


        try:
            upload_json = upload_response.json()
        except Exception:

            upload_json = {
                "raw_response": upload_response.text
            }


        if upload_response.status_code >= 400:

            error = (
                upload_json
                .get("error", {})
                .get(
                    "message",
                    f"Facebook upload error: HTTP {upload_response.status_code}",
                )
            )

            return {
                "success": False,
                "error": error,
            }


        if not upload_json.get("success", False):

            error = (
                upload_json
                .get("error", {})
                .get(
                    "message",
                    "Reel video upload failed.",
                )
            )

            return {
                "success": False,
                "error": error,
            }


        # ----------------------------------------------------
        # STEP 3: PUBLISH
        # ----------------------------------------------------

        publish_url = (
            f"https://graph.facebook.com/v18.0/"
            f"{page_id}/video_reels"
        )

        publish_payload = {
            "upload_phase": "finish",
            "video_id": video_id,
            "video_state": "PUBLISHED",
            "description": caption,
            "access_token": access_token,
        }

        publish_response = requests.post(
            publish_url,
            data=publish_payload,
            timeout=60,
        )

        try:
            publish_json = publish_response.json()
        except Exception:
            publish_json = {}


        if publish_response.status_code >= 400:

            error = (
                publish_json
                .get("error", {})
                .get(
                    "message",
                    f"Facebook publish error: HTTP {publish_response.status_code}",
                )
            )

            return {
                "success": False,
                "error": error,
            }


        if (
            publish_json.get("success")
            or "id" in publish_json
            or publish_json.get("video_state") == "PUBLISHED"
        ):

            return {
                "success": True,
                "id": publish_json.get(
                    "id",
                    video_id,
                ),
            }


        error = (
            publish_json
            .get("error", {})
            .get(
                "message",
                "Facebook Reel publish nahi hua.",
            )
        )

        return {
            "success": False,
            "error": error,
        }


    except requests.exceptions.Timeout:

        return {
            "success": False,
            "error": "Facebook request timeout ho gaya.",
        }


    except requests.exceptions.RequestException as e:

        return {
            "success": False,
            "error": f"Network error: {str(e)}",
        }


    except Exception as e:

        return {
            "success": False,
            "error": str(e),
        }


# ============================================================
# RETRY FUNCTION
# ============================================================

def post_reel_with_retry(
    video_path,
    caption,
    page_id,
    access_token,
    max_retries=3,
):

    last_result = None

    for attempt in range(
        1,
        int(max_retries) + 1,
    ):

        result = post_reel_to_facebook(
            video_path,
            caption,
            page_id,
            access_token,
        )

        result["attempt"] = attempt

        if result.get("success"):

            return result

        last_result = result

        if attempt < int(max_retries):

            time.sleep(5)


    return last_result


# ============================================================
# VIDEO PROCESSING
# ============================================================

def process_video(
    input_path,
    output_dir,
    clip_duration,
):

    if not FFMPEG_PATH:

        return {
            "success": False,
            "clips": [],
            "error": "FFmpeg executable nahi mila.",
        }


    os.makedirs(
        output_dir,
        exist_ok=True,
    )


    # Remove previous clips

    for old_file in Path(output_dir).glob("*.mp4"):

        try:
            old_file.unlink()
        except Exception:
            pass


    duration = get_video_duration(
        input_path
    )


    clips = []

    part_num = 1
    start_time = 0


    # --------------------------------------------------------
    # If duration unavailable, process until FFmpeg fails.
    # Otherwise use exact number of clips.
    # --------------------------------------------------------

    if duration is not None:

        total_parts = max(
            1,
            int(
                (duration + clip_duration - 0.001)
                // clip_duration
            ),
        )

    else:

        total_parts = 999999


    progress = st.progress(0)


    while part_num <= total_parts:

        output_filename = (
            f"Reel_Part_{part_num:03d}.mp4"
        )

        output_filepath = os.path.join(
            output_dir,
            output_filename,
        )


        # ----------------------------------------------------
        # Vertical 9:16 crop
        # ----------------------------------------------------

        video_filter = (
            "scale=1080:1920:"
            "force_original_aspect_ratio=increase,"
            "crop=1080:1920:"
            "exact=1,"
            "setsar=1"
        )


        command = [

            FFMPEG_PATH,

            "-y",

            "-hide_banner",

            "-loglevel",
            "error",

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
            "veryfast",

            "-crf",
            "23",

            "-c:a",
            "aac",

            "-b:a",
            "128k",

            "-ar",
            "44100",

            "-pix_fmt",
            "yuv420p",

            "-movflags",
            "+faststart",

            output_filepath,
        ]


        result = run_ffmpeg(command)


        if not result["success"]:

            if os.path.exists(output_filepath):

                try:
                    os.remove(output_filepath)
                except Exception:
                    pass

            return {
                "success": False,
                "clips": clips,
                "error": result.get(
                    "error",
                    "FFmpeg processing failed.",
                ),
                "stderr": result.get(
                    "stderr",
                    "",
                ),
            }


        # ----------------------------------------------------
        # Validate output
        # ----------------------------------------------------

        if (
            not os.path.exists(output_filepath)
            or os.path.getsize(output_filepath) < 1000
        ):

            if os.path.exists(output_filepath):

                try:
                    os.remove(output_filepath)
                except Exception:
                    pass

            break


        clips.append(output_filepath)


        # ----------------------------------------------------
        # Progress
        # ----------------------------------------------------

        if duration and duration > 0:

            progress_value = min(
                1.0,
                (
                    start_time
                    + clip_duration
                ) / duration,
            )

            progress.progress(
                progress_value
            )


        start_time += clip_duration
        part_num += 1


        # Safety break

        if duration is not None:

            if start_time >= duration:

                break


    progress.progress(1.0)


    if not clips:

        return {
            "success": False,
            "clips": [],
            "error": "Koi video clip generate nahi hui.",
        }


    return {
        "success": True,
        "clips": clips,
        "error": None,
    }


# ============================================================
# FILE UPLOAD
# ============================================================

if uploaded_file is not None:

    extension = (
        os.path.splitext(
            uploaded_file.name
        )[1]
        .lower()
    )

    if extension not in [
        ".mp4",
        ".mkv",
        ".avi",
        ".mov",
        ".webm",
        ".m4v",
    ]:

        extension = ".mp4"


    input_path = (
        "input_video"
        + extension
    )


    with open(
        input_path,
        "wb",
    ) as output_file:

        output_file.write(
            uploaded_file.getbuffer()
        )


    st.success(
        f"✅ Video upload ho gayi: "
        f"`{safe_filename(uploaded_file.name)}`"
    )


    # --------------------------------------------------------
    # Buttons
    # --------------------------------------------------------

    btn_col1, btn_col2 = st.columns(2)


    with btn_col1:

        start_btn = st.button(
            "🚀 Process Video",
            use_container_width=True,
        )


    with btn_col2:

        resume_btn = False

        if (
            st.session_state.is_stopped_due_to_error
            and
            st.session_state.posted_count
            <
            len(st.session_state.clips)
        ):

            resume_btn = st.button(
                "🔄 Fix Error & Resume",
                use_container_width=True,
            )


    # ========================================================
    # PROCESS BUTTON
    # ========================================================

    if start_btn:

        st.session_state.posted_count = 0
        st.session_state.is_stopped_due_to_error = False
        st.session_state.processing_complete = False
        st.session_state.clips = []


        if not FFMPEG_PATH:

            st.error(
                "❌ FFmpeg available nahi hai. "
                "requirements.txt me `imageio-ffmpeg` add karein."
            )

            st.stop()


        with st.spinner(
            "🎬 Video ko 9:16 Reels me convert kiya ja raha hai..."
        ):

            output_dir = "output_clips"


            process_result = process_video(
                input_path=input_path,
                output_dir=output_dir,
                clip_duration=chunk_duration,
            )


        if not process_result["success"]:

            st.error(
                "❌ Video processing failed."
            )

            st.code(
                process_result.get(
                    "error",
                    "Unknown FFmpeg error",
                )
            )

            if process_result.get("stderr"):

                with st.expander(
                    "FFmpeg Error Details"
                ):

                    st.code(
                        process_result["stderr"]
                    )

        else:

            st.session_state.clips = (
                process_result["clips"]
            )

            st.session_state.processing_complete = True

            st.success(
                f"✅ Total "
                f"**{len(st.session_state.clips)}** "
                f"Reels ready hain."
            )


    # ========================================================
    # POSTING
    # ========================================================

    if (
        (
            start_btn
            and
            st.session_state.processing_complete
        )
        or
        resume_btn
    ):

        clips = st.session_state.clips

        if not clips:

            st.error(
                "❌ Posting ke liye koi clips available nahi hain."
            )

            st.stop()


        # ----------------------------------------------------
        # Facebook credentials
        # ----------------------------------------------------

        if not fb_page_id:

            st.error(
                "❌ Facebook Page ID missing hai."
            )

            st.stop()


        if not fb_access_token:

            st.error(
                "❌ Facebook Access Token missing hai."
            )

            st.stop()


        total_clips = len(clips)


        st.subheader(
            "📊 Facebook Reels Posting Dashboard"
        )


        metric1, metric2, metric3, metric4 = st.columns(4)


        metric1.metric(
            "Total Reels",
            total_clips,
        )


        metric2.metric(
            "Posted",
            st.session_state.posted_count,
        )


        metric3.metric(
            "Remaining",
            total_clips
            - st.session_state.posted_count,
        )


        metric4.metric(
            "Status",
            "Posting..."
            if not st.session_state.is_stopped_due_to_error
            else "Halted",
        )


        progress_bar = st.progress(
            (
                st.session_state.posted_count
                /
                total_clips
            )
        )


        status_text = st.empty()

        log_container = st.container()


        st.session_state.is_stopped_due_to_error = False


        # ====================================================
        # POST EACH REEL
        # ====================================================

        for idx in range(
            st.session_state.posted_count,
            total_clips,
        ):

            clip_path = clips[idx]

            current_part = idx + 1


            caption = (
                f"{custom_caption_prefix} "
                f"- Part {current_part} "
                f"#reels #viral"
            )


            status_text.info(
                f"📤 Uploading Reel "
                f"{current_part}/{total_clips}..."
            )


            result = post_reel_with_retry(
                video_path=clip_path,
                caption=caption,
                page_id=fb_page_id,
                access_token=fb_access_token,
                max_retries=max_retries,
            )


            # ------------------------------------------------
            # SUCCESS
            # ------------------------------------------------

            if result.get("success"):

                st.session_state.posted_count += 1


                with log_container:

                    st.success(
                        f"✅ Reel "
                        f"**{current_part}/{total_clips}** "
                        f"published successfully."
                    )

                    st.caption(
                        f"Video ID: "
                        f"`{result.get('id', 'N/A')}` "
                        f"| Attempt: "
                        f"{result.get('attempt', 1)}"
                    )


                progress_bar.progress(
                    st.session_state.posted_count
                    /
                    total_clips
                )


                metric2.metric(
                    "Posted",
                    st.session_state.posted_count,
                )


                metric3.metric(
                    "Remaining",
                    total_clips
                    - st.session_state.posted_count,
                )


                # --------------------------------------------
                # DELAY
                # --------------------------------------------

                if (
                    st.session_state.posted_count
                    < total_clips
                    and
                    delay_minutes > 0
                ):

                    delay_seconds = int(
                        delay_minutes * 60
                    )


                    for seconds_left in range(
                        delay_seconds,
                        0,
                        -1,
                    ):

                        status_text.warning(
                            f"⏳ Next Reel "
                            f"({current_part + 1}/{total_clips}) "
                            f"in {seconds_left} seconds..."
                        )

                        time.sleep(1)


            # ------------------------------------------------
            # ERROR
            # ------------------------------------------------

            else:

                st.session_state.is_stopped_due_to_error = True


                metric4.metric(
                    "Status",
                    "⚠️ HALTED",
                )


                with log_container:

                    st.error(
                        f"❌ Reel "
                        f"**{current_part}/{total_clips}** "
                        f"failed."
                    )

                    st.code(
                        result.get(
                            "error",
                            "Unknown Facebook API error.",
                        )
                    )


                    st.warning(
                        "Posting sequence stop kar di gayi hai. "
                        "Facebook credentials/API error fix karke "
                        "'Fix Error & Resume' press karein."
                    )


                break


        # ====================================================
        # ALL DONE
        # ====================================================

        if (
            not st.session_state.is_stopped_due_to_error
            and
            st.session_state.posted_count
            ==
            total_clips
        ):

            status_text.success(
                "🎉 Saari Reels successfully publish ho gayi hain!"
            )


            metric4.metric(
                "Status",
                "✅ Completed",
            )


        # ====================================================
        # ZIP BACKUP
        # ====================================================

        zip_filename = (
            "all_reels_parts.zip"
        )


        with zipfile.ZipFile(
            zip_filename,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as zip_file:

            for clip in clips:

                if os.path.exists(clip):

                    zip_file.write(
                        clip,
                        arcname=os.path.basename(
                            clip
                        ),
                    )


        if os.path.exists(
            zip_filename
        ):

            with open(
                zip_filename,
                "rb",
            ) as zip_file:

                st.download_button(
                    label=(
                        "📥 Download All Reels ZIP"
                    ),
                    data=zip_file.read(),
                    file_name=(
                        "Facebook_Reels_Backup.zip"
                    ),
                    mime="application/zip",
                    use_container_width=True,
                )


        # ====================================================
        # INDIVIDUAL CLIPS
        # ====================================================

        st.subheader(
            "🎬 Generated Reels"
        )


        for clip_index, clip in enumerate(
            clips,
            start=1,
        ):

            if os.path.exists(clip):

                with st.expander(
                    f"Reel Part {clip_index}"
                ):

                    st.video(clip)

                    with open(
                        clip,
                        "rb",
                    ) as video_file:

                        st.download_button(
                            label=(
                                f"📥 Download "
                                f"Part {clip_index}"
                            ),
                            data=video_file.read(),
                            file_name=os.path.basename(
                                clip
                            ),
                            mime="video/mp4",
                            key=(
                                f"download_clip_"
                                f"{clip_index}"
                            ),
                        )
