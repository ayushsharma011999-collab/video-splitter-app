from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.logger import logger
from shared.metadata import create_job_id, fraction_to_float, utc_now
from shared.storage import OneDriveStorage


CONFIG_PATH = ROOT / "config" / "config.json"
CONFIG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

ONEDRIVE_ROOT = CONFIG["onedrive"]["root_folder"]
INPUT_FOLDER = CONFIG["onedrive"]["input_folder"]
PROCESSING_FOLDER = CONFIG["onedrive"]["processing_folder"]
COMPLETED_FOLDER = CONFIG["onedrive"]["completed_folder"]
FAILED_FOLDER = CONFIG["onedrive"]["failed_folder"]

ALLOWED_EXTENSIONS = {
    value.lower()
    for value in CONFIG["ingest"]["allowed_extensions"]
}

DISPATCH_AGENT_2 = bool(CONFIG["ingest"].get("dispatch_agent_2", False))

AZURE_CLIENT_ID = os.getenv("AZURE_CLIENT_ID")
AZURE_TENANT_ID = os.getenv("AZURE_TENANT_ID")
AZURE_CLIENT_SECRET = os.getenv("AZURE_CLIENT_SECRET")
ONEDRIVE_USER = os.getenv("ONEDRIVE_USER")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY")


def get_graph_token() -> str:
    required = {
        "AZURE_CLIENT_ID": AZURE_CLIENT_ID,
        "AZURE_TENANT_ID": AZURE_TENANT_ID,
        "AZURE_CLIENT_SECRET": AZURE_CLIENT_SECRET,
        "ONEDRIVE_USER": ONEDRIVE_USER,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(
            "Missing required environment variables: " + ", ".join(missing)
        )

    response = requests.post(
        f"https://login.microsoftonline.com/{AZURE_TENANT_ID}/oauth2/v2.0/token",
        data={
            "client_id": AZURE_CLIENT_ID,
            "client_secret": AZURE_CLIENT_SECRET,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        },
        timeout=60,
    )

    if not response.ok:
        try:
            details = response.json()
        except Exception:
            details = response.text
        raise RuntimeError(f"Microsoft token request failed: {details}")

    token = response.json().get("access_token")
    if not token:
        raise RuntimeError("Microsoft token response did not contain access_token.")

    return token


def probe_video(video_path: Path) -> dict[str, Any]:
    process = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(video_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    if process.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {process.stderr.strip()}")

    try:
        payload = json.loads(process.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("ffprobe returned invalid JSON.") from exc

    streams = payload.get("streams", [])
    video_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "video"),
        None,
    )
    audio_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "audio"),
        None,
    )

    if not video_stream:
        raise RuntimeError("No video stream was found.")

    try:
        duration = float(payload.get("format", {}).get("duration", 0))
    except (TypeError, ValueError):
        duration = 0.0

    if duration <= 0:
        raise RuntimeError("Video duration could not be determined.")

    fps = fraction_to_float(video_stream.get("r_frame_rate"))

    return {
        "duration_seconds": round(duration, 3),
        "format_name": payload.get("format", {}).get("format_name"),
        "video": {
            "codec": video_stream.get("codec_name"),
            "width": video_stream.get("width"),
            "height": video_stream.get("height"),
            "fps": round(fps, 3) if fps is not None else None,
            "pix_fmt": video_stream.get("pix_fmt"),
        },
        "audio": {
            "present": audio_stream is not None,
            "codec": audio_stream.get("codec_name") if audio_stream else None,
            "sample_rate": audio_stream.get("sample_rate") if audio_stream else None,
            "channels": audio_stream.get("channels") if audio_stream else None,
        },
    }


def get_oldest_episode(
    storage: OneDriveStorage,
    input_id: str,
) -> dict[str, Any] | None:
    candidates = []

    for item in storage.list_children(input_id):
        if "file" not in item:
            continue

        name = item.get("name", "")
        suffix = Path(name).suffix.lower()

        if suffix not in ALLOWED_EXTENSIONS:
            continue

        size = int(item.get("size") or 0)
        if size <= 0:
            logger.warning("Skipping zero-size file: %s", name)
            continue

        candidates.append(item)

    if not candidates:
        return None

    candidates.sort(
        key=lambda item: (
            item.get("createdDateTime")
            or item.get("lastModifiedDateTime")
            or "",
            item.get("name", "").lower(),
        )
    )

    return candidates[0]


def print_download_progress(progress: float) -> None:
    logger.info("OneDrive download: %.0f%%", progress)


def process_job(
    storage: OneDriveStorage,
    job_folder: dict[str, Any],
    claim: dict[str, Any],
) -> dict[str, Any]:
    job_id = claim["job_id"]
    original_name = claim["original_filename"]
    source_item_id = claim["source_item_id"]

    source_extension = Path(original_name).suffix.lower()
    if source_extension not in ALLOWED_EXTENSIONS:
        raise RuntimeError(f"Unsupported video extension: {source_extension}")

    source_name = f"source{source_extension}"
    source_item = storage.get_item(source_item_id)

    parent_id = source_item.get("parentReference", {}).get("id")
    if parent_id != job_folder["id"] or source_item.get("name") != source_name:
        source_item = storage.move_item(
            source_item_id,
            job_folder["id"],
            new_name=source_name,
        )

    remote_size = int(source_item.get("size") or 0)
    if remote_size <= 0:
        raise RuntimeError("Source file has zero size.")

    with tempfile.TemporaryDirectory(prefix=f"agent1_{job_id}_") as temp_dir:
        temp_path = Path(temp_dir) / source_name

        free_space = shutil.disk_usage(temp_dir).free
        required = int(remote_size * 1.10) + 256 * 1024 * 1024

        if free_space < required:
            raise RuntimeError(
                "Not enough local runner disk space. "
                f"Required≈{required / 1024**3:.2f} GB, "
                f"available≈{free_space / 1024**3:.2f} GB."
            )

        logger.info(
            "Downloading %s (%.2f GB)...",
            source_name,
            remote_size / 1024**3,
        )

        sha256 = storage.download_file(
            source_item_id,
            temp_path,
            expected_size=remote_size,
            progress_callback=print_download_progress,
        )

        video_info = probe_video(temp_path)

    ingested_at = utc_now()
    processing_path = (
        f"{ONEDRIVE_ROOT}/{PROCESSING_FOLDER}/{job_id}"
    )
    source_path = f"{processing_path}/{source_name}"

    metadata = {
        "agent": "agent_1_ingest",
        "agent_version": "1.0.0",
        "job_id": job_id,
        "status": "READY_FOR_CREATIVE",
        "source": "onedrive",
        "original_filename": original_name,
        "source_filename": source_name,
        "created_at": claim.get("claimed_at"),
        "ingested_at": ingested_at,
        "sha256": sha256,
        "original_size_bytes": remote_size,
        "video": video_info,
        "onedrive": {
            "user": ONEDRIVE_USER,
            "processing_path": processing_path,
            "source_path": source_path,
            "job_folder_id": job_folder["id"],
            "source_item_id": source_item_id,
        },
    }

    storage.upload_json(job_folder["id"], "metadata.json", metadata)

    claim.update(
        {
            "status": "READY_FOR_CREATIVE",
            "completed_at": ingested_at,
        }
    )
    storage.upload_json(job_folder["id"], "claim.json", claim)

    return metadata


def dispatch_agent_2(metadata: dict[str, Any]) -> None:
    if not DISPATCH_AGENT_2:
        logger.info("Agent 2 dispatch is disabled for this test phase.")
        return

    if not GITHUB_TOKEN or not GITHUB_REPOSITORY:
        raise RuntimeError(
            "GITHUB_TOKEN/GITHUB_REPOSITORY is required for Agent 2 dispatch."
        )

    response = requests.post(
        f"https://api.github.com/repos/{GITHUB_REPOSITORY}/dispatches",
        headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json={
            "event_type": "agent_1_ready",
            "client_payload": {
                "job_id": metadata["job_id"],
                "status": metadata["status"],
                "processing_path": metadata["onedrive"]["processing_path"],
                "source_path": metadata["onedrive"]["source_path"],
            },
        },
        timeout=60,
    )

    if response.status_code != 204:
        raise RuntimeError(
            f"Agent 2 dispatch failed: {response.status_code} {response.text}"
        )


def move_failed_job(
    storage: OneDriveStorage,
    job_folder: dict[str, Any],
    failed_root: dict[str, Any],
    claim: dict[str, Any],
    error: Exception,
) -> None:
    failed_at = utc_now()
    error_text = str(error)

    claim.update(
        {
            "status": "FAILED",
            "failed_at": failed_at,
            "error": error_text,
        }
    )

    error_payload = {
        "agent": "agent_1_ingest",
        "job_id": claim.get("job_id"),
        "status": "FAILED",
        "failed_at": failed_at,
        "original_filename": claim.get("original_filename"),
        "error": error_text,
    }

    try:
        storage.upload_json(job_folder["id"], "claim.json", claim)
        storage.upload_json(job_folder["id"], "error.json", error_payload)
        storage.move_item(
            job_folder["id"],
            failed_root["id"],
            new_name=claim["job_id"],
        )
    except Exception as secondary_error:
        logger.error(
            "Could not finalize failed job: %s",
            secondary_error,
        )


def main() -> None:
    logger.info("==========================================")
    logger.info("Agent 1 - INGEST SCOUT")
    logger.info("==========================================")

    token = get_graph_token()
    storage = OneDriveStorage(token, ONEDRIVE_USER)

    root_id = storage.get_root()["id"]

    studio_root = storage.ensure_folder(root_id, ONEDRIVE_ROOT)
    input_root = storage.ensure_folder(studio_root["id"], INPUT_FOLDER)
    processing_root = storage.ensure_folder(
        studio_root["id"],
        PROCESSING_FOLDER,
    )
    storage.ensure_folder(
        studio_root["id"],
        COMPLETED_FOLDER,
    )
    failed_root = storage.ensure_folder(
        studio_root["id"],
        FAILED_FOLDER,
    )

    logger.info("OneDrive folders verified.")

    episode = get_oldest_episode(storage, input_root["id"])
    if not episode:
        logger.info("Input queue is empty. Nothing to ingest.")
        return

    original_name = episode["name"]
    source_item_id = episode["id"]
    job_id = create_job_id(original_name, source_item_id)

    logger.info("Selected episode: %s", original_name)
    logger.info("Job ID: %s", job_id)

    job_folder = storage.ensure_folder(
        processing_root["id"],
        job_id,
    )

    claim = {
        "agent": "agent_1_ingest",
        "status": "INGESTING",
        "job_id": job_id,
        "claimed_at": utc_now(),
        "source_item_id": source_item_id,
        "original_filename": original_name,
        "input_folder": INPUT_FOLDER,
        "processing_folder": PROCESSING_FOLDER,
    }

    try:
        storage.upload_json(
            job_folder["id"],
            "claim.json",
            claim,
        )

        metadata = process_job(
            storage,
            job_folder,
            claim,
        )

        dispatch_agent_2(metadata)

        logger.info("==========================================")
        logger.info("AGENT 1 SUCCESS")
        logger.info("Episode: %s", original_name)
        logger.info("Job: %s", job_id)
        logger.info("==========================================")

    except Exception as exc:
        logger.exception("Agent 1 failed for %s", job_id)
        move_failed_job(
            storage,
            job_folder,
            failed_root,
            claim,
            exc,
        )
        raise


if __name__ == "__main__":
    main()
