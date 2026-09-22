from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.logger import logger
from shared.metadata import utc_now
from shared.storage import OneDriveStorage

CONFIG = json.loads((ROOT / "config" / "config.json").read_text(encoding="utf-8"))
ONEDRIVE_ROOT = CONFIG["onedrive"]["root_folder"]
PROCESSING_FOLDER = CONFIG["onedrive"]["processing_folder"]
CREATIVE = CONFIG["creative"]

AZURE_CLIENT_ID = os.getenv("AZURE_CLIENT_ID")
AZURE_TENANT_ID = os.getenv("AZURE_TENANT_ID")
AZURE_CLIENT_SECRET = os.getenv("AZURE_CLIENT_SECRET")
ONEDRIVE_USER = os.getenv("ONEDRIVE_USER")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", CREATIVE.get("model", "gpt-5.6-luna"))
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY")

def get_graph_token() -> str:
    required = {
        "AZURE_CLIENT_ID": AZURE_CLIENT_ID,
        "AZURE_TENANT_ID": AZURE_TENANT_ID,
        "AZURE_CLIENT_SECRET": AZURE_CLIENT_SECRET,
        "ONEDRIVE_USER": ONEDRIVE_USER,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise RuntimeError("Missing environment variables: " + ", ".join(missing))
    response = requests.post(
        f"https://login.microsoftonline.com/{AZURE_TENANT_ID}/oauth2/v2.0/token",
        data={"client_id": AZURE_CLIENT_ID, "client_secret": AZURE_CLIENT_SECRET,
              "scope": "https://graph.microsoft.com/.default",
              "grant_type": "client_credentials"},
        timeout=60,
    )
    if not response.ok:
        raise RuntimeError(f"Microsoft token request failed: {response.status_code} {response.text}")
    token = response.json().get("access_token")
    if not token:
        raise RuntimeError("Microsoft token response did not contain access_token.")
    return token

def read_json(storage: OneDriveStorage, folder_id: str, filename: str) -> dict[str, Any] | None:
    item = storage.find_child(folder_id, filename)
    if not item:
        return None
    return json.loads(storage.download_bytes(item["id"]).decode("utf-8-sig"))

def find_ready_job(storage: OneDriveStorage, processing_id: str):
    jobs = []
    for folder in storage.list_children(processing_id):
        if "folder" not in folder:
            continue
        claim = read_json(storage, folder["id"], "claim.json")
        metadata = read_json(storage, folder["id"], "metadata.json")
        if not claim or not metadata or claim.get("status") != "READY_FOR_CREATIVE":
            continue
        existing = read_json(storage, folder["id"], "creative.json")
        stamp = claim.get("completed_at") or claim.get("claimed_at") or ""
        jobs.append((stamp, folder, claim, metadata, existing))
    if not jobs:
        return None
    jobs.sort(key=lambda x: x[0])
    return jobs[0][1:]

def build_prompt(metadata: dict[str, Any]) -> str:
    return (
        "You are Agent 2, Creative Director for a short-video studio.\n"
        "Create social copy from verified input only.\n"
        "Never invent plot, characters, dialogue, locations, facts, ratings, or quotes.\n"
        "If only technical metadata and filename are present, use neutral episode/video wording.\n"
        "Write natural Hindi. Return ONLY valid JSON.\n"
        "Required keys: title, hooks, caption, description, seo_keywords, hashtags, content_context_status.\n"
        "title <= 80 chars; exactly 3 hooks <= 100 chars; caption <= 500 chars; "
        "5-8 SEO keywords; 5-10 hashtags starting with #.\n"
        "content_context_status must be METADATA_ONLY or EDITORIAL_CONTEXT_AVAILABLE.\n\n"
        "Verified input:\n" + json.dumps(metadata, ensure_ascii=False, indent=2)
    )

def extract_text(payload: dict[str, Any]) -> str:
    text = payload.get("output_text")
    if isinstance(text, str) and text.strip():
        return text.strip()
    parts = []
    for output in payload.get("output", []):
        for item in output.get("content", []):
            value = item.get("text") if isinstance(item, dict) else None
            if isinstance(value, str):
                parts.append(value)
            elif isinstance(value, dict) and isinstance(value.get("value"), str):
                parts.append(value["value"])
    result = "\n".join(parts).strip()
    if not result:
        raise RuntimeError("OpenAI response contained no text.")
    return result

def parse_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    data = json.loads(text)
    if not isinstance(data, dict):
        raise RuntimeError("Creative output must be a JSON object.")
    return data

def clean_text(value: Any, field: str, limit: int) -> str:
    if not isinstance(value, str):
        raise RuntimeError(f"{field} must be a string.")
    value = " ".join(value.split()).strip()
    if not value:
        raise RuntimeError(f"{field} is empty.")
    return value[:limit]

def validate(data: dict[str, Any]) -> dict[str, Any]:
    required = {"title", "hooks", "caption", "description", "seo_keywords", "hashtags", "content_context_status"}
    missing = required - set(data)
    if missing:
        raise RuntimeError("Missing creative fields: " + ", ".join(sorted(missing)))
    hooks = data["hooks"]
    keywords = data["seo_keywords"]
    hashtags = data["hashtags"]
    if not isinstance(hooks, list) or len(hooks) != 3:
        raise RuntimeError("hooks must contain exactly 3 items.")
    if not isinstance(keywords, list) or not 5 <= len(keywords) <= 8:
        raise RuntimeError("seo_keywords must contain 5-8 items.")
    if not isinstance(hashtags, list) or not 5 <= len(hashtags) <= 10:
        raise RuntimeError("hashtags must contain 5-10 items.")
    status = data["content_context_status"]
    if status not in {"METADATA_ONLY", "EDITORIAL_CONTEXT_AVAILABLE"}:
        raise RuntimeError("Invalid content_context_status.")
    clean_hash = []
    for item in hashtags:
        tag = clean_text(item, "hashtag", 50).replace(" ", "")
        clean_hash.append(tag if tag.startswith("#") else "#" + tag)
    return {
        "title": clean_text(data["title"], "title", 80),
        "hooks": [clean_text(x, "hook", 100) for x in hooks],
        "caption": clean_text(data["caption"], "caption", 500),
        "description": clean_text(data["description"], "description", 1000),
        "seo_keywords": [clean_text(x, "seo_keyword", 60) for x in keywords],
        "hashtags": clean_hash,
        "content_context_status": status,
    }

def generate(metadata: dict[str, Any]) -> dict[str, Any]:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not configured.")
    payload = {"model": OPENAI_MODEL, "input": build_prompt(metadata),
               "max_output_tokens": int(CREATIVE.get("max_output_tokens", 1400))}
    last_error = None
    for attempt in range(3):
        try:
            response = requests.post(
                "https://api.openai.com/v1/responses",
                headers={"Authorization": "Bearer " + OPENAI_API_KEY, "Content-Type": "application/json"},
                json=payload, timeout=180,
            )
            if response.status_code in {429, 500, 502, 503, 504}:
                time.sleep(min(2 ** attempt, 10))
                continue
            if not response.ok:
                raise RuntimeError(f"OpenAI request failed: {response.status_code} {response.text}")
            return validate(parse_json(extract_text(response.json())))
        except requests.RequestException as exc:
            last_error = exc
            time.sleep(min(2 ** attempt, 10))
    raise RuntimeError(f"OpenAI request failed after retries: {last_error}")

def dispatch_agent_3(creative: dict[str, Any]) -> None:
    if not bool(CREATIVE.get("dispatch_agent_3", False)):
        logger.info("Agent 3 dispatch is disabled for now.")
        return
    if not GITHUB_TOKEN or not GITHUB_REPOSITORY:
        raise RuntimeError("GITHUB_TOKEN/GITHUB_REPOSITORY is required for Agent 3 dispatch.")
    response = requests.post(
        f"https://api.github.com/repos/{GITHUB_REPOSITORY}/dispatches",
        headers={"Authorization": "Bearer " + GITHUB_TOKEN, "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
        json={"event_type": "agent_2_ready", "client_payload": {"job_id": creative["job_id"], "status": creative["status"]}},
        timeout=60,
    )
    if response.status_code != 204:
        raise RuntimeError(f"Agent 3 dispatch failed: {response.status_code} {response.text}")

def mark_failure(storage: OneDriveStorage, folder: dict[str, Any], claim: dict[str, Any], error: Exception) -> None:
    claim.update({"status": "CREATIVE_FAILED", "creative_failed_at": utc_now(), "creative_error": str(error)})
    storage.upload_json(folder["id"], "claim.json", claim)
    storage.upload_json(folder["id"], "creative_error.json", {
        "agent": "agent_2_creative", "job_id": claim.get("job_id"),
        "status": "CREATIVE_FAILED", "failed_at": utc_now(), "error": str(error)
    })

def main() -> None:
    logger.info("==========================================")
    logger.info("Agent 2 - CREATIVE DIRECTOR")
    logger.info("==========================================")
    token = get_graph_token()
    storage = OneDriveStorage(token, ONEDRIVE_USER)
    root_id = storage.get_root()["id"]
    studio = storage.ensure_folder(root_id, ONEDRIVE_ROOT)
    processing = storage.ensure_folder(studio["id"], PROCESSING_FOLDER)
    found = find_ready_job(storage, processing["id"])
    if not found:
        logger.info("No READY_FOR_CREATIVE jobs found.")
        return
    folder, claim, metadata, existing = found
    job_id = claim["job_id"]
    try:
        creative = validate(existing) if existing else generate(metadata)
        creative = dict(creative)
        creative.update({"agent": "agent_2_creative", "agent_version": "2.0.0", "job_id": job_id, "generated_at": creative.get("generated_at", utc_now()), "source_filename": metadata.get("original_filename"), "source_metadata_status": metadata.get("status")})
        storage.upload_json(folder["id"], "creative.json", creative)
        claim.update({"status": "CREATIVE_READY", "creative_completed_at": utc_now()})
        storage.upload_json(folder["id"], "claim.json", claim)
        dispatch_agent_3({**creative, "status": "CREATIVE_READY"})
        logger.info("AGENT 2 SUCCESS: %s", job_id)
    except Exception as exc:
        logger.exception("Agent 2 failed for %s", job_id)
        try:
            mark_failure(storage, folder, claim, exc)
        except Exception as final_error:
            logger.error("Could not write creative failure state: %s", final_error)
        raise

if __name__ == "__main__":
    main()