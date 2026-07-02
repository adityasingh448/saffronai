from __future__ import annotations

from pathlib import Path
import json
import os
import re
import shutil
import subprocess
import threading
import time
from typing import Callable, Iterable

from PIL import Image

from app.audio import audio_duration_seconds
from app.config import settings
from app.pdf_tools import HighlightBox
from app.render_options import get_quality, normalize_render_fps, render_crf, render_dimensions
from app.script_writer import PageScript


RenderProgressCallback = Callable[[float, str, int | None, int | None], None]


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
    render_quality: str = "720p",
    render_fps: int = 30,
    progress_callback: RenderProgressCallback | None = None,
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
        render_quality=render_quality,
        render_fps=render_fps,
    )

    _ensure_node_dependencies(project_dir)
    _render_project(
        project_dir,
        props_path,
        output_path,
        video_format=video_format,
        render_quality=render_quality,
        render_fps=render_fps,
        progress_callback=progress_callback,
    )

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
    render_quality: str,
    render_fps: int,
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
    fps = normalize_render_fps(render_fps)
    render_width, render_height = render_dimensions(video_format, render_quality)

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
                "highlights": _highlight_payloads(highlights, width, height, script.highlight_terms if script else []),
            }
        )
        cursor += page_duration

    props = {
        "title": _clean_label(title) or "Report walkthrough",
        "brandName": brand_name,
        "avatarMode": avatar_mode,
        "prospectLabel": _clean_label(prospect_label),
        "videoFormat": _normalize_video_format(video_format),
        "fps": fps,
        "renderQuality": render_quality,
        "renderWidth": render_width,
        "renderHeight": render_height,
        "durationSeconds": round(duration, 3),
        "audioSrc": f"jobs/{job_key}/{audio_name}",
        "pages": pages,
    }

    props_path = public_job_dir / "props.json"
    props_path.write_text(json.dumps(props, indent=2, ensure_ascii=False), encoding="utf-8")
    return props_path


def _render_project(
    project_dir: Path,
    props_path: Path,
    output_path: Path,
    video_format: str,
    render_quality: str,
    render_fps: int,
    progress_callback: RenderProgressCallback | None,
) -> None:
    npm = _resolve_npm()
    if not npm:
        raise RuntimeError("npm was not found. Install Node.js/npm before using the video renderer.")

    relative_props = props_path.relative_to(project_dir)
    fps = normalize_render_fps(render_fps)
    render_width, render_height = render_dimensions(video_format, render_quality)
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
        f"--crf={render_crf(render_quality)}",
        f"--fps={fps}",
        f"--width={render_width}",
        f"--height={render_height}",
        "--pixel-format=yuv420p",
    ]
    if settings.remotion_concurrency.strip():
        command.append(f"--concurrency={settings.remotion_concurrency.strip()}")

    _run_render_command(
        command,
        project_dir=project_dir,
        props_path=props_path,
        log_path=output_path.with_suffix(".remotion.log"),
        render_quality=render_quality,
        render_fps=fps,
        progress_callback=progress_callback,
    )


def _run_render_command(
    command: list[str],
    project_dir: Path,
    props_path: Path,
    log_path: Path,
    render_quality: str,
    render_fps: int,
    progress_callback: RenderProgressCallback | None,
) -> None:
    captured: list[str] = []
    stop_ticker = threading.Event()
    progress_state = {"value": 0.0}
    render_started = time.monotonic()
    last_frame_update = {"at": render_started}
    stalled = {"hit": False}
    estimated_seconds = _estimated_seconds_from_props(props_path, render_quality, render_fps)
    render_timeout = _render_timeout_seconds(estimated_seconds)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("w", encoding="utf-8")
    log_file.write(" ".join(command) + "\n\n")
    log_file.flush()

    if progress_callback:
        progress_callback(0.01, "Preparing render", None, None)

    process = subprocess.Popen(
        command,
        cwd=project_dir,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
    )

    def publish(value: float, message: str, frames_done: int | None = None, total_frames: int | None = None) -> None:
        progress_state["value"] = max(progress_state["value"], max(0.0, min(value, 1.0)))
        if frames_done is not None:
            last_frame_update["at"] = time.monotonic()
        if progress_callback:
            progress_callback(progress_state["value"], message, frames_done, total_frames)

    def ticker() -> None:
        while not stop_ticker.wait(1.2):
            elapsed = time.monotonic() - render_started
            if elapsed > 240 and time.monotonic() - last_frame_update["at"] > 180:
                stalled["hit"] = True
                _terminate_process_tree(process.pid)
                stop_ticker.set()
                return
            estimated_value = min(0.94, elapsed / max(20.0, estimated_seconds))
            publish(max(progress_state["value"], estimated_value), "Rendering frames", None, None)

    def reader() -> None:
        if process.stdout is None:
            return
        for raw_line in process.stdout:
            captured.append(raw_line)
            log_file.write(raw_line)
            log_file.flush()
            for segment in re.split(r"[\r\n]+", raw_line):
                parsed = _parse_remotion_progress(segment)
                if parsed:
                    value, message, frames_done, total_frames = parsed
                    publish(value, message, frames_done, total_frames)

    ticker_thread = threading.Thread(target=ticker, daemon=True)
    reader_thread = threading.Thread(target=reader, daemon=True)
    ticker_thread.start()
    reader_thread.start()

    try:
        return_code = process.wait(timeout=render_timeout)
    except subprocess.TimeoutExpired as exc:
        _terminate_process_tree(process.pid)
        stop_ticker.set()
        raise RuntimeError(f"Video renderer timed out after {render_timeout} seconds.") from exc
    finally:
        stop_ticker.set()
        reader_thread.join(timeout=2)
        log_file.close()

    if stalled["hit"]:
        raise RuntimeError("Video renderer stalled while rendering frames.")

    if return_code != 0:
        detail = "".join(captured).strip()
        if len(detail) > 4000:
            detail = detail[-4000:]
        raise RuntimeError(f"Video renderer failed with exit code {return_code}.\n{detail}")

    publish(1.0, "Finalizing video", None, None)


def _parse_remotion_progress(text: str) -> tuple[float, str, int | None, int | None] | None:
    cleaned = _strip_ansi(text)
    if not cleaned.strip():
        return None

    encoding = re.search(r"\b(?:Encoded|Encoding)\D+(\d+)\s*/\s*(\d+)", cleaned, flags=re.IGNORECASE)
    if encoding:
        frames_done = int(encoding.group(1))
        total_frames = max(1, int(encoding.group(2)))
        value = 0.86 + 0.14 * min(frames_done / total_frames, 1.0)
        return value, f"Encoding video {frames_done}/{total_frames}", frames_done, total_frames

    rendering = re.search(r"\b(?:Rendered|Rendering)\D+(\d+)\s*/\s*(\d+)", cleaned, flags=re.IGNORECASE)
    if rendering:
        frames_done = int(rendering.group(1))
        total_frames = max(1, int(rendering.group(2)))
        value = 0.04 + 0.82 * min(frames_done / total_frames, 1.0)
        return value, f"Rendering frames {frames_done}/{total_frames}", frames_done, total_frames

    return None


def _strip_ansi(value: str) -> str:
    return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", value or "")


def _estimated_seconds_from_props(props_path: Path, render_quality: str, render_fps: int) -> float:
    try:
        props = json.loads(props_path.read_text(encoding="utf-8"))
        duration_seconds = float(props.get("durationSeconds") or 60)
    except (OSError, ValueError, TypeError):
        duration_seconds = 60.0

    quality = get_quality(render_quality)
    fps_factor = {24: 0.84, 30: 1.0, 60: 1.82}.get(render_fps, render_fps / 30)
    return 22 + duration_seconds * quality.time_factor * fps_factor


def _render_timeout_seconds(estimated_seconds: float) -> int:
    return min(settings.remotion_timeout_seconds, max(180, min(900, int(estimated_seconds * 2.3))))


def _terminate_process_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], text=True, capture_output=True)
        return
    try:
        os.kill(pid, 9)
    except OSError:
        pass


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


def _highlight_payloads(
    items: Iterable[HighlightBox],
    page_width: int,
    page_height: int,
    labels: Iterable[str] | None = None,
) -> list[dict[str, object]]:
    payloads = []
    source_items = list(items)
    clean_labels = [_clean_label(label) for label in (labels or []) if _clean_label(label)]
    max_payloads = len(clean_labels) if clean_labels else 3

    if clean_labels:
        used: set[int] = set()
        for index, label in enumerate(clean_labels[:4]):
            item = _best_highlight_for_label(label, source_items, used)
            if not item and index < len(source_items):
                item = source_items[index]
            if item:
                used.add(id(item))
                payloads.append(_highlight_payload_from_box(item, page_width, page_height, label))
            else:
                payloads.append(_synthetic_highlight_payload(label, index, page_width, page_height))
        return payloads

    for item in source_items:
        if len(payloads) >= max_payloads:
            break
        width = max(0.0, min(float(item.x1), page_width) - max(float(item.x0), 0.0))
        height = max(0.0, min(float(item.y1), page_height) - max(float(item.y0), 0.0))
        if width < 8 or height < 6:
            continue
        payloads.append(_highlight_payload_from_box(item, page_width, page_height, _clean_label(item.label)))
    return payloads


def _best_highlight_for_label(label: str, items: list[HighlightBox], used: set[int]) -> HighlightBox | None:
    scored: list[tuple[float, float, HighlightBox]] = []
    for item in items:
        if id(item) in used:
            continue
        score = _label_match_score(label, item.label)
        if score <= 0:
            continue
        scored.append((score, -float(item.y0), item))
    if not scored:
        return None
    score, _, item = max(scored, key=lambda value: (value[0], value[1]))
    return item if score >= 0.22 else None


def _label_match_score(label: str, candidate: str) -> float:
    label_key = _normalize_match_text(label)
    candidate_key = _normalize_match_text(candidate)
    if not label_key or not candidate_key:
        return 0.0
    if label_key in candidate_key or candidate_key in label_key:
        return 1.0

    label_tokens = set(_match_tokens(label))
    candidate_tokens = set(_match_tokens(candidate))
    if not label_tokens or not candidate_tokens:
        return 0.0
    overlap = len(label_tokens & candidate_tokens)
    label_ratio = overlap / max(1, len(label_tokens))
    candidate_ratio = overlap / max(1, len(candidate_tokens))
    return max(label_ratio, candidate_ratio * 0.82)


def _match_tokens(text: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(token) >= 2]


def _normalize_match_text(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", _clean_label(text).lower())


def _highlight_payload_from_box(
    item: HighlightBox,
    page_width: int,
    page_height: int,
    label: str,
) -> dict[str, object]:
    x0 = max(float(item.x0), 0.0)
    y0 = max(float(item.y0), 0.0)
    x1 = min(float(item.x1), float(page_width))
    y1 = min(float(item.y1), float(page_height))
    pad_x = max(10.0, page_width * 0.018)
    pad_y = max(8.0, (y1 - y0) * 1.1)
    return {
        "x0": max(0.0, x0 - pad_x),
        "y0": max(0.0, y0 - pad_y),
        "x1": min(float(page_width), x1 + pad_x),
        "y1": min(float(page_height), y1 + pad_y),
        "label": label,
    }


def _synthetic_highlight_payload(
    label: str,
    index: int,
    page_width: int,
    page_height: int,
) -> dict[str, object]:
    top = page_height * (0.2 + index * 0.18)
    return {
        "x0": page_width * 0.13,
        "y0": top,
        "x1": page_width * 0.87,
        "y1": min(page_height * 0.92, top + page_height * 0.08),
        "label": label,
    }


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
