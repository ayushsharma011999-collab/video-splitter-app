from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.logger import logger
from shared.metadata import utc_now
from shared.storage import OneDriveStorage

CONFIG = json.loads(
    (ROOT / "config" / "config.json").read_text(encoding="utf-8")
)

ONEDRIVE_ROOT = CONFIG["onedrive"]["root_folder"]
PROCESSING_FOLDER = CONFIG["onedrive"]["processing_folder"]
CREATIVE = CONFIG["creative"]

AZURE_CLIENT_ID = os.getenv("AZURE_CLIENT_ID")
AZURE_TENANT_ID = os.getenv("AZURE_TENANT_ID")
AZURE_CLIENT_SECRET = os.getenv("AZURE_CLIENT_SECRET")
ONEDRIVE_USER = os.getenv("ONEDRIVE_USER")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv(
    "OPENAI_MODEL",
    CREATIVE.get("model", "gpt-5.6-luna"),
)


def get_graph_token() -> str:
    required = {
        "AZURE_CLIENT_ID": AZURE_CLIENT_ID,
        "AZURE_TENANT_ID": AZURE_TENANT_ID,
        "AZURE_CLIENT_SECRET": AZURE_CLIENT_SECRET,
        "ONEDRIVE_USER": ONEDRIVE_USER,
    }

    missing = [key for key, value in required.items() if not value]

    if missing:
        raise RuntimeError(
            "Missing environment variables: "
            + ", ".join(missing)
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
        raise RuntimeError(
            f"Microsoft token request failed: "
            f"{response.status_code} {response.text}"
        )

    token = response.json().get("access_token")

    if not token:
        raise RuntimeError(
            "Microsoft token response did not contain access_token."
        )

    return token


def find_ready_job(
    storage: OneDriveStorage,
    processing_root_id: str,
) -> tuple[dict[str, Any], dict[str, Any]] | None:

    candidates = []

    for folder in storage.list_children(processing_root_id):
        if "folder" not in folder:
            continue

        claim_item = storage.find_child(folder["id"], "claim.json")
        metadata_item = storage.find_child(folder["id"], "metadata.json")

        if not claim_item or not metadata_item:
            continue

        try:
            claim = json.loads(
                storage.download_bytes(claim_item["id"]).decode("utf-8-sig")
            )
        except Exception as exc:
            logger.warning(
                "Could not read claim.json in %s: %s",
                folder.get("name", "unknown"),
                exc,
            )
            continue

        if claim.get("status") != "READY_FOR_CREATIVE":
            continue

        candidates.append(
            (
                claim.get("completed_at")
                or claim.get("claimed_at")
                or "",
                folder,
                claim,
            )
        )

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0])
    _, folder, claim = candidates[0]
    return folder, claim


def read_metadata(
    storage: OneDriveStorage,
    job_folder_id: str,
) -> dict[str, Any]:

    item = storage.find_child(job_folder_id, "metadata.json")

    if not item:
        raise RuntimeError("metadata.json not found.")

    return json.loads(
        storage.download_bytes(item["id"]).decode("utf-8-sig")
    )


def build_prompt(metadata: dict[str, Any]) -> str:
    metadata_text = json.dumps(
        metadata,
        ensure_ascii=False,
        indent=2,
    )

    return (
        "You are Agent 2, Creative Director for a short-video studio.\n"
        "Create social-media copy only from verified metadata.\n"
        "Do not invent plot events, characters, dialogue, locations, or facts.\n"
        "The current pipeline has technical metadata only; there is no transcript "
        "or scene summary yet. Never pretend to know the plot.\n"
        "Use natural Hindi.\n"
        "Return ONLY valid JSON with these fields:\n"
        '{"title":"...","hooks":["...","...","..."],'
        "'caption":"...","description":"...",'
        '"seo_keywords":["...","...","..."],'
        '"hashtags":["#shorts","#reels","#viral"],'
        '"content_context_status":"METADATA_ONLY"}\n'
        "Title under 80 characters. Each hook under 100 characters. "
        "Caption under 500 characters.\n"
        "Verified metadata:\n"
        + metadata_text
    )


def extract_text(payload: dict[str, Any]) -> str:
    direct = payload.get("output_text")

    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    pieces = []

    for output in payload.get("output", []):
        if not isinstance(output, dict):
            continue

        for content in output.get("content", []):
            if not isinstance(content, dict):
                continue

            value = content.get("text")

            if isinstance(value, str):
                pieces.append(value)
            elif isinstance(value, dict):
                nested = value.get("value")
                if isinstance(nested, str):
                    pieces.append(nested)

    text = "\n".join(pieces).strip()

    if not text:
        raise RuntimeError("OpenAI response contained no text.")

    return text


def parse_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()

    if cleaned.startswith("```"):
        lines = cleaned.splitlines()

        if lines and lines[0].startswith("```"):
            lines = lines[1:]

        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]

        cleaned = "\n".join(lines).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Creative Director received invalid JSON. "
            + cleaned[:600]
        ) from exc


def generate_creative(metadata: dict[str, Any]) -> dict[str, Any]:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not configured.")

    response = requests.post(
        "https://api.openai.com/v1/responses",
        headers={
            "Authorization": "Bearer " + OPENAI_API_KEY,
            "Content-Type": "application/json",
        },
        json={
            "model": OPENAI_MODEL,
            "input": build_prompt(metadata),
            "max_output_tokens": int(
                CREATIVE.get("max_output_tokens", 1400)
            ),
        },
        timeout=180,
    )

    if not response.ok:
        raise RuntimeError(
            f"OpenAI request failed: "
            f"{response.status_code} {response.text}"
        )

    result = parse_json(
        extract_text(response.json())
    )

    required = {
        "title",
        "hooks",
        "caption",
        "description",
        "seo_keywords",
        "hashtags",
        "content_context_status",
    }

    missing = required - set(result.keys())

    if missing:
        raise RuntimeError(
            "Creative JSON missing fields: "
            + ", ".join(sorted(missing))
        )

    return result


def main() -> None:
    logger.info("==========================================")
    logger.info($¹ÐÈ´IQ%Y%IQ=H¤(±½È¹¥¹¼ ôôôôôôôôôôôôôôôôôôôôôôôôôôôôôôôôôôôôôôôôôô¤((ÍÑ½Éô=¹É¥ÙMÑ½É (Ñ}ÉÁ¡}Ñ½­¸ ¤°(=9I%Y}UMH°(¤((É½½Ñ}¥ôÍÑ½É¹Ñ}É½½Ð ¥l¥t(ÍÑÕ¥½}É½½ÐôÍÑ½É¹¹ÍÕÉ}½±È (É½½Ñ}¥°(=9I%Y}I==P°(¤((ÁÉ½ÍÍ¥¹}É½½ÐôÍÑ½É¹¹ÍÕÉ}½±È (ÍÑÕ¥½}É½½Ñl¥t°(AI=MM%9}=1H°(¤((Éäô¥¹}Éå}©½ (ÍÑ½É°(ÁÉ½ÍÍ¥¹}É½½Ñl¥t°(¤((¥¹½ÐÉäè(±½È¹¥¹¼ 9¼Ie}=I}IQ%Y©½½Õ¹¸¤(ÉÑÕÉ¸((©½}½±È°±¥´ôÉä(©½}¥ô±¥µl©½}¥t((±½È¹¥¹¼ M±Ñ©½èÌ°©½}¥¤((µÑÑôÉ}µÑÑ (ÍÑ½É°(©½}½±Él¥t°(¤((ÉÑ¥Ùô¹ÉÑ}ÉÑ¥Ù¡µÑÑ¤((ÉÑ¥Ù¹ÕÁÑ (ì(¹Ðè¹Ñ|É}ÉÑ¥Ù°(¹Ñ}ÙÉÍ¥½¸èÄ¸À¸À°(©½}¥è©½}¥°(¹ÉÑ}ÐèÕÑ}¹½Ü ¤°(µ½°è=A9%}5=0°(ô(¤((ÍÑ½É¹ÕÁ±½}©Í½¸ (©½}½±Él¥t°(ÉÑ¥Ù¹©Í½¸°(ÉÑ¥Ù°(¤((±¥´¹ÕÁÑ (ì(ÍÑÑÕÌèIQ%Y}Id°(ÉÑ¥Ù}½µÁ±Ñ}ÐèÕÑ}¹½Ü ¤°(ô(¤((ÍÑ½É¹ÕÁ±½}©Í½¸ (©½}½±Él¥t°(±¥´¹©Í½¸°(±¥´°(¤((±½È¹¥¹¼ 9PÈMUMLèÌ°©½}¥¤(()¥}}¹µ}|ôô}}µ¥¹}|è(µ¥¸ ¤(