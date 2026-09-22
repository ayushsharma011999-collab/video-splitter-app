from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_slug(text: str, max_length: int = 60) -> str:
    text = Path(text).stem
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "_", text).strip("_")
    return (text or "episode")[:max_length]


def create_job_id(original_name: str, source_item_id: str) -> str:
    digest = hashlib.sha1(source_item_id.encode("utf-8")).hexdigest()[:10]
    return f"{safe_slug(original_name)}__{digest}"


def fraction_to_float(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(Fraction(value))
    except Exception:
        try:
            return float(value)
        except Exception:
            return None
