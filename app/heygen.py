from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time

import requests

from app.config import settings


@dataclass
class HeyGenAvatarResult:
    asset_id: str
    video_id: str
    video_url: str
    local_path: Path


def can_use_heygen() -> bool:
    return bool(settings.heygen_api_key and settings.heygen_avatar_id)


def create_heygen_avatar_video(audio_path: Path, job_dir: Path, title: str) -> HeyGenAvatarResult:
    if not can_use_heygen():
        raise ValueError("HEYGEN_API_KEY and HEYGEN_AVATAR_ID are required for HeyGen avatar mode.")

    audio_asset_id = _upload_asset(audio_path)
    video_id = _create_video(audio_asset_id, title, prefer_transparent=True)
    video_url = _poll_video(video_id)

    suffix = ".webm" if ".webm" in video_url.lower().split("?")[0] else ".mp4"
    local_path = job_dir / f"heygen-avatar{suffix}"
    _download(video_url, local_path)

    return HeyGenAvatarResult(
        asset_id=audio_asset_id,
        video_id=video_id,
        video_url=video_url,
        local_path=local_path,
    )


def _headers(content_type: str | None = "application/json") -> dict[str, str]:
    headers = {"x-api-key": settings.heygen_api_key or ""}
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def _upload_asset(path: Path) -> str:
    with path.open("rb") as file_handle:
        response = requests.post(
            "https://api.heygen.com/v3/assets",
            headers={"x-api-key": settings.heygen_api_key or ""},
            files={"file": (path.name, file_handle)},
            timeout=180,
        )
    response.raise_for_status()
    return response.json()["data"]["asset_id"]


def _create_video(audio_asset_id: str, title: str, prefer_transparent: bool) -> str:
    payload = {
        "type": "avatar",
        "avatar_id": settings.heygen_avatar_id,
        "title": title,
        "resolution": "720p",
        "aspect_ratio": "1:1",
        "audio_asset_id": audio_asset_id,
        "background": {"remove_background": True},
    }

    if prefer_transparent:
        payload["output_format"] = "webm"

    response = requests.post(
        "https://api.heygen.com/v3/videos",
        headers=_headers(),
        json=payload,
        timeout=90,
    )

    if response.status_code >= 400 and prefer_transparent:
        payload.pop("output_format", None)
        payload.pop("background", None)
        response = requests.post(
            "https://api.heygen.com/v3/videos",
            headers=_headers(),
            json=payload,
            timeout=90,
        )

    response.raise_for_status()
    return response.json()["data"]["video_id"]


def _poll_video(video_id: str, timeout_seconds: int = 1800) -> str:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        response = requests.get(
            f"https://api.heygen.com/v3/videos/{video_id}",
            headers=_headers(content_type=None),
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()["data"]
        status = data.get("status")

        if status == "completed" and data.get("video_url"):
            return data["video_url"]

        if status == "failed":
            raise RuntimeError(data.get("failure_message") or "HeyGen video generation failed.")

        time.sleep(10)

    raise TimeoutError("Timed out waiting for HeyGen avatar video.")


def _download(url: str, output_path: Path) -> None:
    with requests.get(url, stream=True, timeout=300) as response:
        response.raise_for_status()
        with output_path.open("wb") as output:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    output.write(chunk)
