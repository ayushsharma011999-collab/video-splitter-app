from __future__ import annotations

import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import List, Optional, Tuple

import streamlit as st

ALLOWED_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
MAX_UPLOAD_BYTES = 1024 * 1024 * 1024  # 1 GiB default upload safety cap


def _which(cmd: str) -> Optional[str]:
    return shutil.which(cmd)


def _check_ffmpeg_available() -> Tuple[bool, List[str]]:
    missing = []
    if not _which("ffmpeg"):
        missing.append("ffmpeg")
    if not _which("ffprobe"):
        missing.append("ffprobe")
    return (len(missing) == 0, missing)


def _ffprobe_duration(path: Path) -> Optional[float]:
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0 or not proc.stdout:
            return None
        return float(proc.stdout.strip())
    except Exception:
        return None


def _split_by_seconds(source: Path, start: float, end: Optional[float], output: Path) -> None:
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(start)]
    if end is not None:
        args += ["-to", str(end)]
    args += ["-i", str(source), "-c", "copy", str(output)]
    subprocess.run(args, check=True)


def _split_equal_parts(source: Path, parts: int, out_dir: Path) -> List[Path]:
    duration = _ffprobe_duration(source)
    if not duration or duration <= 0:
        raise RuntimeError("Could not determine video duration.")
    part_duration = duration / parts
    outputs = []
    for i in range(parts):
        start = i * part_duration
        end = min(duration, (i + 1) * part_duration)
        out_path = out_dir / f"part_{i+1:03d}{source.suffix}"
        _split_by_seconds(source, start, end, out_path)
        outputs.append(out_path)
    return outputs


def _split_by_duration_seconds(source: Path, seconds: float, out_dir: Path) -> List[Path]:
    duration = _ffprobe_duration(source)
    if not duration or duration <= 0:
        raise RuntimeError("Could not determine video duration.")
    outputs = []
    i = 0
    start = 0.0
    while start < duration:
        end = min(duration, start + seconds)
        out_path = out_dir / f"segment_{i+1:03d}{source.suffix}"
        _split_by_seconds(source, start, end, out_path)
        outputs.append(out_path)
        i += 1
        start = end
    return outputs


def _parse_timestamps(text: str) -> List[Tuple[float, float]]:
    """
    Parse timestamp lines. Accepts formats like:
    00:00:10-00:01:00
    10-60
    00:10-00:20
    """
    def to_seconds(s: str) -> float:
        parts = s.strip().split(":")
        parts = [float(p) for p in parts]
        if len(parts) == 1:
            return parts[0]
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
        if len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
        raise ValueError("Invalid timestamp")
    ranges = []
    for line in text.strip().splitlines():
        if not line.strip():
            continue
        if "-" not in line:
            raise ValueError(f"Invalid range line: {line!r}")
        a, b = line.split("-", 1)
        start = to_seconds(a)
        end = to_seconds(b)
        if end <= start:
            raise ValueError(f"End must be greater than start in: {line!r}")
        ranges.append((start, end))
    return ranges


def _zip_paths(paths: List[Path], zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in paths:
            zf.write(p, arcname=p.name)


def render_splitter() -> None:
    st.title("Video Splitter")
    ff_ok, missing = _check_ffmpeg_available()
    if not ff_ok:
        st.error(
            "FFmpeg/FFprobe not found on PATH: " + ", ".join(missing) + ".\n\n"
            "This app uses FFmpeg for splitting video files. See README for installation instructions."
        )
        return

    uploaded = st.file_uploader("Upload a video file", type=[e.lstrip(".") for e in ALLOWED_EXTENSIONS])
    if not uploaded:
        st.info("Upload a video to get started.")
        return

    if uploaded.size is not None and uploaded.size > MAX_UPLOAD_BYTES:
        st.error(f"Uploaded file is too large ({uploaded.size} bytes). Max allowed is {MAX_UPLOAD_BYTES} bytes.")
        return

    # Save uploaded file securely
    try:
        tmp_dir = Path(tempfile.mkdtemp(prefix="video_split_"))
        src_path = tmp_dir / uploaded.name
        with open(src_path, "wb") as f:
            f.write(uploaded.read())
    except Exception as exc:
        st.error(f"Could not save uploaded file: {exc}")
        return

    st.success(f"Saved upload to temporary file: {src_path.name}")
    # Options
    mode = st.radio("Split mode", ["Equal parts (count)", "Split by duration (seconds)", "Split by timestamps"])
    outputs: List[Path] = []
    try:
        if mode == "Equal parts (count)":
            count = st.number_input("Number of parts", min_value=2, max_value=200, value=2)
            if st.button("Split now"):
                with st.spinner("Splitting..."):
                    outputs = _split_equal_parts(src_path, int(count), tmp_dir)
        elif mode == "Split by duration (seconds)":
            seconds = st.number_input("Seconds per segment", min_value=1.0, value=60.0, format="%.1f")
            if st.button("Split now"):
                with st.spinner("Splitting..."):
                    outputs = _split_by_duration_seconds(src_path, float(seconds), tmp_dir)
        else:  # timestamps
            st.markdown("Enter ranges, one per line, in formats like `00:00:10-00:01:00` or `10-60`.")
            text = st.text_area("Timestamps", height=150, placeholder="00:00:10-00:01:00\n00:02:00-00:03:10")
            if st.button("Split now"):
                ranges = _parse_timestamps(text)
                with st.spinner("Splitting..."):
                    for i, (start, end) in enumerate(ranges):
                        out = tmp_dir / f"range_{i+1:03d}{src_path.suffix}"
                        _split_by_seconds(src_path, start, end, out)
                        outputs.append(out)
    except subprocess.CalledProcessError as exc:
        st.error(f"FFmpeg failed during splitting: {exc}")
    except Exception as exc:
        st.error(f"Error during splitting: {exc}")

    if outputs:
        st.success(f"Created {len(outputs)} segment(s).")
        # Make a zip for download
        zip_path = tmp_dir / f"{src_path.stem}_segments.zip"
        _zip_paths(outputs, zip_path)
        with open(zip_path, "rb") as f:
            st.download_button("Download segments (zip)", data=f.read(), file_name=zip_path.name)
    # Cleanup guidance: files are in tmp_dir; the OS will clean them eventually but we can delete them now
    if st.button("Clean temporary files"):
        try:
            shutil.rmtree(tmp_dir)
            st.success("Temporary files removed.")
        except Exception as exc:
            st.warning(f"Could not remove temporary files: {exc}")
