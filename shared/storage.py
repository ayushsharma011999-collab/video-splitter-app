from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable

import requests

from shared.logger import logger

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


class GraphAPIError(RuntimeError):
    pass


class OneDriveStorage:
    def __init__(self, access_token: str, user: str):
        if not access_token:
            raise ValueError("Microsoft Graph access token is missing.")
        if not user:
            raise ValueError("OneDrive user is missing.")

        self.user = user
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        })
        self.base_user = f"{GRAPH_BASE}/users/{self.user}/drive"

    def request(
        self,
        method: str,
        url: str,
        *,
        retries: int = 5,
        stream: bool = False,
        **kwargs: Any,
    ) -> requests.Response:
        if not url.startswith("http"):
            url = f"{self.base_user}{url}"

        last_error = None
        for attempt in range(retries):
            try:
                response = self.session.request(
                    method,
                    url,
                    timeout=(30, 120),
                    stream=stream,
                    **kwargs,
                )

                if response.status_code in {429, 500, 502, 503, 504}:
                    retry_after = response.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after else 2 ** attempt
                    logger.warning(
                        "Graph returned %s. Retrying in %.1fs...",
                        response.status_code,
                        delay,
                    )
                    response.close()
                    time.sleep(delay)
                    continue

                if not response.ok:
                    try:
                        details = response.json()
                    except Exception:
                        details = response.text
                    response.close()
                    raise GraphAPIError(
                        f"Graph API error {response.status_code}: {details}"
                    )

                return response

            except requests.RequestException as exc:
                last_error = exc
                delay = 2 ** attempt
                logger.warning("Network error: %s. Retrying in %.1fs...", exc, delay)
                time.sleep(delay)

        raise GraphAPIError(
            f"Graph request failed after {retries} attempts: {last_error}"
        )

    def get_root(self) -> dict[str, Any]:
        response = self.request(
            "GET",
            "/root?$select=id,name,folder,parentReference",
        )
        try:
            return response.json()
        finally:
            response.close()

    def get_item(self, item_id: str) -> dict[str, Any]:
        response = self.request(
            "GET",
            f"/items/{item_id}?$select=id,name,size,createdDateTime,lastModifiedDateTime,parentReference,file,folder",
        )
        try:
            return response.json()
        finally:
            response.close()

    def download_bytes(self, item_id: str) -> bytes:
        """Download a small OneDrive file directly into memory.

        This is intended for small control/metadata files such as claim.json
        and metadata.json, not large video files.
        """
        response = self.request(
            "GET",
            f"{self.base_user}/items/{item_id}/content",
        )
        try:
            return response.content
        finally:
            response.close()

    def list_children(self, parent_id: str) -> list[dict[str, Any]]:
        url = (
            f"{self.base_user}/items/{parent_id}/children"
            "?$select=id,name,size,createdDateTime,lastModifiedDateTime,parentReference,file,folder"
        )
        results = []

        while url:
            response = self.request("GET", url)
            try:
                payload = response.json()
            finally:
                response.close()
            results.extend(payload.get("value", []))
            url = payload.get("@odata.nextLink")

        return results

    def find_child(self, parent_id: str, name: str) -> dict[str, Any] | None:
        for item in self.list_children(parent_id):
            if item.get("name") == name:
                return item
        return None

    def create_folder(self, parent_id: str, name: str) -> dict[str, Any]:
        payload = {
            "name": name,
            "folder": {},
            "@microsoft.graph.conflictBehavior": "fail",
        }
        response = self.request(
            "POST",
            f"/items/{parent_id}/children",
            json=payload,
        )
        try:
            return response.json()
        finally:
            response.close()

    def ensure_folder(self, parent_id: str, name: str) -> dict[str, Any]:
        existing = self.find_child(parent_id, name)
        if existing:
            if "folder" not in existing:
                raise GraphAPIError(
                    f"OneDrive item '{name}' exists but is not a folder."
                )
            return existing
        return self.create_folder(parent_id, name)

    def move_item(
        self,
        item_id: str,
        new_parent_id: str,
        new_name: str | None = None,
    ) -> dict[str, Any]:
        payload = {"parentReference": {"id": new_parent_id}}
        if new_name:
            payload["name"] = new_name

        response = self.request(
            "PATCH",
            f"/items/{item_id}",
            json=payload,
        )
        try:
            return response.json()
        finally:
            response.close()

    def download_file(
        self,
        item_id: str,
        destination: Path,
        expected_size: int | None = None,
        progress_callback: Callable[[float], None] | None = None,
    ) -> str:
        destination.parent.mkdir(parents=True, exist_ok=True)
        response = self.request(
            "GET",
            f"{self.base_user}/items/{item_id}/content",
            stream=True,
        )

        total = int(response.headers.get("Content-Length", 0)) or expected_size or 0
        downloaded = 0
        last_report = -1
        digest = hashlib.sha256()

        try:
            with destination.open("wb") as output:
                for chunk in response.iter_content(chunk_size=8 * 1024 * 1024):
                    if not chunk:
                        continue

                    output.write(chunk)
                    digest.update(chunk)
                    downloaded += len(chunk)

                    if total and progress_callback:
                        progress = downloaded * 100 / total
                        rounded = int(progress)
                        if rounded >= last_report + 5 or rounded == 100:
                            progress_callback(progress)
                            last_report = rounded
        finally:
            response.close()

        actual_size = destination.stat().st_size
        if expected_size is not None and actual_size != expected_size:
            raise GraphAPIError(
                f"Downloaded file size mismatch: expected={expected_size}, actual={actual_size}"
            )

        if progress_callback:
            progress_callback(100.0)

        return digest.hexdigest()

    def upload_bytes(
        self,
        parent_id: str,
        filename: str,
        content: bytes,
        content_type: str = "application/json",
    ) -> dict[str, Any]:
        response = self.request(
            "PUT",
            f"{self.base_user}/items/{parent_id}:/{filename}:/content",
            headers={"Content-Type": content_type},
            data=content,
        )
        try:
            return response.json()
        finally:
            response.close()

    def upload_json(
        self,
        parent_id: str,
        filename: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self.upload_bytes(
            parent_id,
            filename,
            json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
        )
