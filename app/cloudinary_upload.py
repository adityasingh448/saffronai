from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import time

import requests

from app.config import settings


@dataclass(frozen=True)
class CloudinaryUploadResult:
    public_id: str
    secure_url: str
    resource_type: str
    bytes: int


def can_use_cloudinary() -> bool:
    return bool(settings.cloudinary_cloud_name and settings.cloudinary_api_key and settings.cloudinary_api_secret)


def upload_video_to_cloudinary(video_path: Path, job_id: str, title: str = "") -> CloudinaryUploadResult:
    if not can_use_cloudinary():
        raise RuntimeError("Cloudinary is not configured.")
    if not video_path.exists() or video_path.stat().st_size <= 0:
        raise RuntimeError("Cloudinary upload skipped because the video file is missing.")

    cloud_name = settings.cloudinary_cloud_name or ""
    endpoint = f"https://api.cloudinary.com/v1_1/{cloud_name}/video/upload"
    public_id = _public_id(job_id, title)
    folder = settings.cloudinary_folder.strip().strip("/")

    with video_path.open("rb") as video_file:
        response = requests.post(
            endpoint,
            auth=(settings.cloudinary_api_key or "", settings.cloudinary_api_secret or ""),
            data={
                "folder": folder,
                "public_id": public_id,
                "overwrite": "true",
                "resource_type": "video",
                "tags": "saffron-ai,report-walkthrough",
            },
            files={"file": (video_path.name, video_file, "video/mp4")},
            timeout=600,
        )

    if response.status_code >= 400:
        raise RuntimeError(f"Cloudinary upload failed ({response.status_code}): {_safe_cloudinary_detail(response)}")

    payload = response.json()
    secure_url = str(payload.get("secure_url") or payload.get("url") or "").strip()
    if not secure_url:
        raise RuntimeError("Cloudinary upload finished without returning a secure URL.")

    return CloudinaryUploadResult(
        public_id=str(payload.get("public_id") or public_id),
        secure_url=secure_url,
        resource_type=str(payload.get("resource_type") or "video"),
        bytes=int(payload.get("bytes") or video_path.stat().st_size),
    )


def _public_id(job_id: str, title: str) -> str:
    title_slug = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")
    title_slug = title_slug[:48].strip("-") or "walkthrough"
    timestamp = int(time.time())
    return f"{title_slug}-{job_id}-{timestamp}"


def _safe_cloudinary_detail(response: requests.Response) -> str:
    try:
        detail = response.json()
    except ValueError:
        detail = response.text
    text = str(detail)
    for secret in (settings.cloudinary_api_secret, settings.cloudinary_api_key):
        if secret:
            text = text.replace(secret, "[hidden]")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:700] or "No error details returned."
