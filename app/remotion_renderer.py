from __future__ import annotations

from pathlib import Path
import json
import os
import re
import shutil
import subprocess
from typing import Iterable

from PIL import Image

from app.audio import audio_duration_seconds
from app.config import settings
from app.pdf_tools import HighlightBox
from app.script_writer import PageScript


def can_use_remotion() -> bool:
    return settings.remotion_template_dir.exists() and bool(_resolve_npm())


def compose_remotion_walkthrough_video(
    page_images: list[Path],
    page_scripts: list[PageScript],
    page_highlights: list[list[HighlightBox]] | None,
    audio_path: Path,
    output_path: Path,
    title: str,
    brand_name: str,
    avatar_mode: str,
    prospect_label: str,
    video_format: str = "horizontal",
) -> Path:
    if not page_images:
        raise ValueError("No PDF page images were provided for video rendering.")

    project_dir = settings.remotion_template_dir.resolve()
    if not project_dir.exists():
        raise RuntimeError("Remotion template project was not found.")

    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    public_job_dir = _prepare_public_job_dir(project_dir, output_path.parent.name)
    props_path = _prepare_props(
        public_job_dir=public_job_dir,
        job_key=public_job_dir.name,
        page_images=page_images,
        page_scripts=page_scripts,
        page_highlights=page_highlights or [[] for _ in page_images],
        audio_path=audio_path,
        title=title,
        brand_name=brand_name,
        avatar_mode=avatar_mode,
        prospect_label=prospect_label,
        video_format=video_format,
    )

    _ensure_node_dependencies(project_dir)
    _render_project(project_dir, props_path, output_path)

    if not output_path.exists() or output_path.stat().st_size <= 0:
        raise RuntimeError("Video renderer finished without producing a video file.")

    return output_path


def _prepare_public_job_dir(project_dir: Path, raw_job_key: str) -> Path:
    job_key = re.sub(r"[^a-zA-Z0-9_-]+", "-", raw_job_key or "job").strip("-") or "job"
    jobs_root = (project_dir / "public" / "jobs").resolve()
    jobs_root.mkdir(parents=True, exist_ok=True)
    job_dir = (jobs_root / job_key).resolve()
    if jobs_root != job_dir and jobs_root not in job_dir.parents:
        raise RuntimeError("Refusing to write Remotion assets outside the jobs directory.")
    if job_dir.exists():
        shutil.rmtree(job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    return job_dir


def _prepare_props(
    public_job_dir: Path,
    job_key: str,
    page_images: list[Path],
    page_scripts: list[PageScript],
    page_highlights: list[list[HighlightBox]],
    audio_path: Path,
    title: str,
    brand_name: str,
    avatar_mode: str,
    prospect_label: str,
    video_format: str,
) -> Path:
    copied_pages = []
    for index, source in enumerate(page_images, start=1):
        suffix = source.suffix or ".png"
        target_name = f"page-{index:02d}{suffix}"
        target = public_job_dir / target_name
        shutil.copy2(source, target)
        copied_pages.append((source, target_name))

    audio_suffix = audio_path.suffix or ".wav"
    audio_name = f"voiceover{audio_suffix}"
    shutil.copy2(audio_path, public_job_dir / audio_name)

    duration = max(audio_duration_seconds(audio_path), len(page_images) * 4.0)
    page_durations = _page_durations(page_scripts, duration, len(page_images))

    pages = []
    cursor = 0.0
    for index, (source, target_name) in enumerate(copied_pages):
        script = page_scripts[min(index, len(page_scripts) - 1)] if page_scripts else None
        highlights = page_highlights[index] if index < len(page_highlights) else []
        width, height = _image_size(source)
        page_duration = page_durations[index] if index < len(page_durations) else duration / max(1, len(page_images))
        focus = _clean_label(script.focus if script else f"Page {index + 1}")
        pages.append(
            {
                "pageNumber": index + 1,
                "imageSrc": f"jobs/{job_key}/{target_name}",
                "width": width,
                "height": height,
                "startSeconds": round(cursor, 3),
                "durationSeconds": round(page_duration, 3),
                "focus": focus,
                "narration": script.narration if script else "",
                "highlights": _highlight_payloads(highlights, width, height),
            }
        )
        cursor += page_duration

    props = {
        "title": _clean_label(title) or "Report walkthrough",
        "brandName": brand_name,
        "avatarMode": avatar_mode,
        "prospectLabel": _clean_label(prospect_label),
        "videoFormat": _normalize_video_format(video_format),
        "fps": settings.remotion_fps,
        "durationSeconds": round(duration, 3),
        "audioSrc": f"jobs/{job_key}/{audio_name}",
        "pages": pages,
    }

    props_path = public_job_dir / "props.json"
    props_path.write_text(json.dumps(props, indent=2, ensure_ascii=False), encoding="utf-8")
    return props_path


def _render_project(project_dir: Path, props_path: Path, output_path: Path) -> None:
    npm = _resolve_npm()
    if not npm:
        raise RuntimeError("npm was not found. Install Node.js/npm before using the video renderer.")

    relative_props = props_path.relative_to(project_dir)
    command = [
        npm,
        "exec",
        "--",
        "remotion",
        "render",
        "src/index.ts",
        "ReportWalkthrough",
        str(output_path),
        f"--props={relative_props.as_posix()}",
        "--overwrite",
        f"--codec={settings.remotion_codec}",
        f"--crf={settings.remotion_crf}",
        "--pixel-format=yuv420p",
    ]
    if settings.remotion_concurrency.strip():
        command.append(f"--concurrency={settings.remotion_concurrency.strip()}")

    completed = subprocess.run(
        command,
        cwd=project_dir,
        text=True,
        capture_output=True,
        timeout=settings.remotion_timeout_seconds,
    )
    if completed.returncode != 0:
        detail = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
        if len(detail) > 4000:
            detail = detail[-4000:]
        raise RuntimeError(f"Video renderer failed with exit code {completed.returncode}.\n{detail}")


def _ensure_node_dependencies(project_dir: Path) -> None:
    local_remotion = _local_bin(project_dir, "remotion")
    if local_remotion.exists():
        return

    npm = _resolve_npm()
    if not npm:
        raise RuntimeError("npm was not found. Install Node.js/npm before using the video renderer.")

    completed = subprocess.run(
        [npm, "install", "--no-audit", "--no-fund"],
        cwd=project_dir,
        text=True,
        capture_output=True,
        timeout=1200,
    )
    if completed.returncode != 0:
        detail = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
        raise RuntimeError(f"Unable to install video renderer dependencies.\n{detail}")


def _local_bin(project_dir: Path, command: str) -> Path:
    suffix = ".cmd" if os.name == "nt" else ""
    return project_dir / "node_modules" / ".bin" / f"{command}{suffix}"


def _resolve_npm() -> str | None:
    if os.name == "nt":
        return shutil.which("npm.cmd") or shutil.which("npm")
    return shutil.which("npm")


def _image_size(path: Path) -> tuple[int, int]:
    with Image.open(path) as image:
        return image.width, image.height


def _page_durations(page_scripts: list[PageScript], total_duration: float, page_count: int) -> list[float]:
    if page_count <= 0:
        return []
    if not page_scripts:
        return [total_duration / page_count] * page_count

    weights = []
    for index in range(page_count):
        script = page_scripts[min(index, len(page_scripts) - 1)]
        weights.append(max(len(script.narration.split()), 45))

    total_weight = sum(weights) or page_count
    durations = [max(3.0, total_duration * weight / total_weight) for weight in weights]
    scale = total_duration / sum(durations)
    return [duration * scale for duration in durations]


def _highlight_payloads(items: Iterable[HighlightBox], page_width: int, page_height: int) -> list[dict[str, object]]:
    payloads = []
    for item in items:
        width = max(0.0, min(float(item.x1), page_width) - max(float(item.x0), 0.0))
        height = max(0.0, min(float(item.y1), page_height) - max(float(item.y0), 0.0))
        if width < 8 or height < 6:
            continue
        payloads.append(
            {
                "x0": max(float(item.x0), 0.0),
                "y0": max(float(item.y0), 0.0),
                "x1": min(float(item.x1), float(page_width)),
                "y1": min(float(item.y1), float(page_height)),
                "label": _clean_label(item.label),
            }
        )
        if len(payloads) >= 6:
            break
    return payloads


def _clean_label(text: str) -> str:
    cleaned = " ".join((text or "").split())
    replacements = (
        ("report visuals", "report section"),
        ("visual section", "report section"),
        ("visual context", "report context"),
        ("visuals", "sections"),
    )
    for source, target in replacements:
        cleaned = cleaned.replace(source, target).replace(source.title(), target.title())
    return cleaned.strip(" .:-")


def _normalize_video_format(value: str) -> str:
    normalized = (value or "horizontal").strip().lower()
    if normalized in {"vertical", "portrait", "9:16", "reels", "shorts"}:
        return "vertical"
    return "horizontal"
