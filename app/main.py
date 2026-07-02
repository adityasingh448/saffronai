from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import re
import shutil
import threading
import time
import uuid

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.audio import synthesize_voice_preview, synthesize_voiceover
from app.config import settings
from app.heygen import can_use_heygen, create_heygen_avatar_video
from app.hyperframes_renderer import can_use_hyperframes, compose_hyperframes_walkthrough_video
from app.pdf_tools import extract_pdf_pages
from app.remotion_renderer import can_use_remotion, compose_remotion_walkthrough_video
from app.render_options import (
    default_render_quality,
    estimate_render_seconds,
    format_duration,
    fps_options,
    normalize_render_fps,
    normalize_render_quality,
    normalize_target_minutes,
    quality_options,
    render_dimensions,
)
from app.script_writer import write_walkthrough_script
from app.video import compose_walkthrough_video, overlay_avatar_video
from app.voices import default_voice_model, get_voice, resolve_voice_model, voice_label, voice_options


STATIC_DIR = settings.base_dir / "app" / "static"

app = FastAPI(title="Saffron AI Sales Automation Agent")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _job_dir(job_id: str) -> Path:
    return settings.jobs_dir / job_id


def _metadata_path(job_id: str) -> Path:
    return _job_dir(job_id) / "job.json"


def _write_job(job_id: str, payload: dict) -> None:
    job_dir = _job_dir(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    payload["updated_at"] = _now()
    tmp_path = job_dir / "job.json.tmp"
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp_path.replace(_metadata_path(job_id))


def _read_job(job_id: str) -> dict:
    path = _metadata_path(job_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Job not found")
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/styles.css", include_in_schema=False)
def root_styles() -> FileResponse:
    return FileResponse(STATIC_DIR / "styles.css", media_type="text/css")


@app.get("/app.js", include_in_schema=False)
def root_app_js() -> FileResponse:
    return FileResponse(STATIC_DIR / "app.js", media_type="application/javascript")


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "brand": settings.brand_name,
        "openai": bool(settings.openai_api_key),
        "elevenlabs": bool(settings.elevenlabs_api_key),
        "heygen": can_use_heygen(),
        "hyperframes": can_use_hyperframes(),
        "remotion": can_use_remotion(),
        "video_renderer": settings.video_renderer,
    }


@app.get("/api/voices")
def list_voices() -> dict:
    return {
        "default_voice_model": default_voice_model(),
        "voices": voice_options(force_refresh=True),
        "preview_enabled": bool(settings.elevenlabs_api_key),
    }


@app.get("/api/render-options")
def list_render_options(
    max_minutes: int = Query(3, ge=1, le=6),
    render_fps: int = Query(30),
    video_format: str = Query("horizontal"),
) -> dict:
    fps = normalize_render_fps(render_fps)
    minutes = normalize_target_minutes(max_minutes)
    return {
        "default_quality": default_render_quality(),
        "default_fps": fps,
        "fps_options": fps_options(),
        "qualities": quality_options(video_format=video_format, render_fps=fps, target_minutes=minutes),
    }


@app.get("/api/voices/preview")
def voice_preview(voice_model: str = Query(..., min_length=3)) -> FileResponse:
    selected_voice = get_voice(voice_model)
    if not selected_voice:
        raise HTTPException(status_code=400, detail="Unknown voice option.")
    if not settings.elevenlabs_api_key:
        raise HTTPException(status_code=503, detail="ElevenLabs is not configured.")

    safe_name = selected_voice.model.replace("/", "-").replace("\\", "-")
    preview_path = settings.data_dir / "voice-previews" / f"{safe_name}.mp3"
    if not preview_path.exists() or preview_path.stat().st_size <= 0:
        synthesize_voice_preview(selected_voice.model, preview_path)

    return FileResponse(preview_path, media_type="audio/mpeg", filename=f"{selected_voice.name}-preview.mp3")


@app.post("/api/jobs")
async def create_job(
    background_tasks: BackgroundTasks,
    pdf: UploadFile = File(...),
    prospect_name: str = Form(""),
    target_company: str = Form(""),
    language: str = Form("English"),
    max_minutes: int = Form(3),
    avatar_mode: str = Form("off"),
    video_format: str = Form("horizontal"),
    render_quality: str = Form("720p"),
    render_fps: int = Form(30),
    voice_model: str = Form(""),
) -> dict:
    if not pdf.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Please upload a PDF report.")

    safe_max_minutes = normalize_target_minutes(max_minutes)
    safe_video_format = _normalize_video_format(video_format)
    safe_quality = normalize_render_quality(render_quality)
    safe_fps = normalize_render_fps(render_fps)
    render_width, render_height = render_dimensions(safe_video_format, safe_quality)
    estimated_render_seconds = estimate_render_seconds(safe_max_minutes, safe_quality, safe_fps)

    job_id = uuid.uuid4().hex[:12]
    job_dir = _job_dir(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)

    pdf_path = job_dir / "input.pdf"
    with pdf_path.open("wb") as output:
        shutil.copyfileobj(pdf.file, output)

    metadata = {
        "id": job_id,
        "status": "queued",
        "stage": "Waiting to start",
        "created_at": _now(),
        "inputs": {
            "prospect_name": prospect_name,
            "target_company": target_company,
            "language": "English",
            "max_minutes": safe_max_minutes,
            "avatar_mode": "off",
            "video_format": safe_video_format,
            "render_quality": safe_quality,
            "render_fps": safe_fps,
            "render_width": render_width,
            "render_height": render_height,
            "estimated_render_seconds": estimated_render_seconds,
            "voice_model": resolve_voice_model(voice_model),
            "filename": pdf.filename,
        },
        "progress": _progress_payload(
            percent=0,
            label="Waiting to start",
            detail=f"Selected {safe_quality} at {safe_fps} FPS",
            eta_seconds=estimated_render_seconds,
        ),
        "artifacts": {},
    }
    _write_job(job_id, metadata)
    background_tasks.add_task(_run_pipeline, job_id)
    return {"job_id": job_id, "status_url": f"/api/jobs/{job_id}"}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    return _read_job(job_id)


@app.get("/api/jobs/{job_id}/video")
def download_video(job_id: str) -> FileResponse:
    job = _read_job(job_id)
    video_path = _job_dir(job_id) / job.get("artifacts", {}).get("video", "")
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="Video is not ready yet.")
    return FileResponse(video_path, media_type="video/mp4", filename=f"saffron-edge-report-{job_id}.mp4")


@app.get("/api/jobs/{job_id}/script")
def download_script(job_id: str) -> FileResponse:
    job = _read_job(job_id)
    script_path = _job_dir(job_id) / job.get("artifacts", {}).get("script", "")
    if not script_path.exists():
        raise HTTPException(status_code=404, detail="Script is not ready yet.")
    return FileResponse(script_path, media_type="text/plain", filename=f"saffron-edge-script-{job_id}.txt")


def _set_stage(
    job_id: str,
    status: str,
    stage: str,
    progress_percent: int | None = None,
    progress_detail: str = "",
    eta_seconds: int | None = None,
    render_progress: dict[str, object] | None = None,
    **extra: object,
) -> None:
    job = _read_job(job_id)
    job.update({"status": status, "stage": stage})
    if progress_percent is not None:
        job["progress"] = _progress_payload(
            percent=progress_percent,
            label=stage,
            detail=progress_detail,
            eta_seconds=eta_seconds,
            render_progress=render_progress,
        )
    job.update(extra)
    _write_job(job_id, job)


def _progress_payload(
    percent: int,
    label: str,
    detail: str = "",
    eta_seconds: int | None = None,
    render_progress: dict[str, object] | None = None,
) -> dict[str, object]:
    safe_percent = max(0, min(int(percent), 100))
    payload: dict[str, object] = {
        "percent": safe_percent,
        "label": label,
        "detail": detail,
        "eta_seconds": eta_seconds,
        "eta_label": format_duration(eta_seconds),
        "updated_at": _now(),
    }
    if render_progress:
        payload["render"] = render_progress
    return payload


def _run_pipeline(job_id: str) -> None:
    job_dir = _job_dir(job_id)
    pdf_path = job_dir / "input.pdf"

    try:
        job = _read_job(job_id)
        inputs = job["inputs"]

        _set_stage(job_id, "running", "Reading and rendering PDF pages", progress_percent=8)
        pages = extract_pdf_pages(pdf_path, job_dir / "pages")

        _set_stage(job_id, "running", "Writing voiceover script", progress_percent=24)
        script = write_walkthrough_script(
            pages=pages,
            prospect_name=inputs.get("prospect_name", ""),
            target_company=inputs.get("target_company", ""),
            language=inputs.get("language", settings.default_language),
            max_minutes=int(inputs.get("max_minutes", 3)),
        )
        script_path = job_dir / "voiceover-script.txt"
        script_path.write_text(script.full_script, encoding="utf-8")
        (job_dir / "script.json").write_text(json.dumps(script.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

        _set_stage(job_id, "running", "Generating voiceover audio", progress_percent=40)
        selected_voice_model = resolve_voice_model(inputs.get("voice_model", ""))
        audio_path, audio_source = synthesize_voiceover(script.full_script, job_dir, voice_model=selected_voice_model)

        requested_avatar_mode = inputs.get("avatar_mode", "off")
        render_avatar_mode = requested_avatar_mode
        avatar_source = requested_avatar_mode

        if requested_avatar_mode == "heygen":
            render_avatar_mode = "off" if can_use_heygen() else "local"
            avatar_source = "heygen" if can_use_heygen() else "local-fallback"

        base_video_path = job_dir / ("walkthrough-base.mp4" if requested_avatar_mode == "heygen" and can_use_heygen() else "walkthrough.mp4")
        prospect_label = inputs.get("target_company") or inputs.get("prospect_name") or "Prospect report"
        page_images = [page.image_path for page in pages]
        page_highlights = [page.highlights for page in pages]
        render_quality = normalize_render_quality(str(inputs.get("render_quality", "720p")))
        render_fps = normalize_render_fps(inputs.get("render_fps", 30))
        render_width, render_height = render_dimensions(inputs.get("video_format", "horizontal"), render_quality)
        estimated_render_seconds = estimate_render_seconds(inputs.get("max_minutes", 3), render_quality, render_fps)
        render_progress_callback = _make_render_progress_callback(job_id, estimated_render_seconds)
        video_renderer_used = settings.video_renderer

        if settings.video_renderer == "remotion":
            _set_stage(
                job_id,
                "running",
                "Rendering the video",
                progress_percent=52,
                progress_detail=f"{render_quality} at {render_fps} FPS",
                eta_seconds=estimated_render_seconds,
            )
            try:
                video_path = compose_remotion_walkthrough_video(
                    page_images=page_images,
                    page_scripts=script.page_scripts,
                    page_highlights=page_highlights,
                    audio_path=audio_path,
                    output_path=base_video_path,
                    title=script.title,
                    brand_name="",
                    avatar_mode=render_avatar_mode,
                    prospect_label=prospect_label,
                    video_format=inputs.get("video_format", "horizontal"),
                    render_quality=render_quality,
                    render_fps=render_fps,
                    progress_callback=render_progress_callback,
                )
            except Exception as render_exc:
                print(f"Primary video renderer was too slow or failed; using fast fallback: {render_exc}")
                video_renderer_used = "fast-fallback"
                _set_stage(
                    job_id,
                    "running",
                    "Finishing with fast renderer",
                    progress_percent=52,
                    progress_detail=f"{render_quality} at {render_fps} FPS",
                    eta_seconds=estimated_render_seconds,
                )
                video_path = compose_walkthrough_video(
                    page_images=page_images,
                    page_scripts=script.page_scripts,
                    page_highlights=page_highlights,
                    audio_path=audio_path,
                    output_path=base_video_path,
                    title=script.title,
                    brand_name="",
                    avatar_mode=render_avatar_mode,
                    prospect_label=prospect_label,
                    video_format=inputs.get("video_format", "horizontal"),
                    render_quality=render_quality,
                    render_fps=render_fps,
                    progress_callback=render_progress_callback,
                )
        elif settings.video_renderer == "hyperframes":
            _set_stage(job_id, "running", "Rendering the video", progress_percent=52, eta_seconds=estimated_render_seconds)
            video_path = compose_hyperframes_walkthrough_video(
                page_images=page_images,
                page_scripts=script.page_scripts,
                page_highlights=page_highlights,
                audio_path=audio_path,
                output_path=base_video_path,
                title=script.title,
                brand_name="",
                avatar_mode=render_avatar_mode,
                prospect_label=prospect_label,
                video_format=inputs.get("video_format", "horizontal"),
            )
        elif settings.video_renderer == "local":
            _set_stage(job_id, "running", "Rendering the video", progress_percent=52, eta_seconds=estimated_render_seconds)
            video_path = compose_walkthrough_video(
                page_images=page_images,
                page_scripts=script.page_scripts,
                page_highlights=page_highlights,
                audio_path=audio_path,
                output_path=base_video_path,
                title=script.title,
                brand_name="",
                avatar_mode=render_avatar_mode,
                prospect_label=prospect_label,
                video_format=inputs.get("video_format", "horizontal"),
                render_quality=render_quality,
                render_fps=render_fps,
                progress_callback=render_progress_callback,
            )
        else:
            raise ValueError("Video renderer is not configured correctly.")

        heygen_result = None
        if requested_avatar_mode == "heygen" and can_use_heygen():
            _set_stage(job_id, "running", "Generating HeyGen lip-sync avatar", progress_percent=96)
            heygen_result = create_heygen_avatar_video(audio_path, job_dir, script.title)
            _set_stage(job_id, "running", "Overlaying HeyGen avatar on walkthrough", progress_percent=98)
            video_path = overlay_avatar_video(base_video_path, heygen_result.local_path, job_dir / "walkthrough.mp4")

        job = _read_job(job_id)
        job["status"] = "complete"
        job["stage"] = "Ready"
        job["progress"] = _progress_payload(percent=100, label="Ready", detail="Video is ready", eta_seconds=0)
        job["artifacts"] = {
            "video": video_path.name,
            "script": script_path.name,
            "script_json": "script.json",
            "audio": audio_path.name,
        }
        if heygen_result:
            job["artifacts"]["heygen_avatar"] = heygen_result.local_path.name
            job["artifacts"]["heygen_video_url"] = heygen_result.video_url
        job["summary"] = {
            "pages": len(pages),
            "script_source": script.source,
            "audio_source": audio_source,
            "voice_model": selected_voice_model,
            "voice_label": voice_label(selected_voice_model),
            "avatar_mode": avatar_source,
            "video_renderer": video_renderer_used,
            "video_format": inputs.get("video_format", "horizontal"),
            "render_quality": render_quality,
            "render_fps": render_fps,
            "render_width": render_width,
            "render_height": render_height,
            "estimated_render_seconds": estimated_render_seconds,
        }
        _write_job(job_id, job)
    except Exception as exc:
        job = _read_job(job_id)
        job["status"] = "failed"
        job["stage"] = "Failed"
        job["progress"] = _progress_payload(percent=int(job.get("progress", {}).get("percent", 0)), label="Failed")
        job["error"] = _public_error_message(str(exc))
        _write_job(job_id, job)


def _make_render_progress_callback(job_id: str, estimated_render_seconds: int):
    render_started = time.monotonic()
    last_update = {"at": 0.0, "percent": -1}
    write_lock = threading.Lock()

    def update(value: float, message: str = "", frames_done: int | None = None, total_frames: int | None = None) -> None:
        progress_value = max(0.0, min(float(value), 1.0))
        overall_percent = min(98, max(52, int(round(52 + progress_value * 44))))
        now = time.monotonic()
        if overall_percent == last_update["percent"] and now - last_update["at"] < 1.2 and progress_value < 1:
            return

        elapsed = max(0.0, now - render_started)
        if progress_value > 0.025:
            eta_seconds = int(max(0.0, elapsed * (1.0 - progress_value) / progress_value))
        else:
            eta_seconds = int(max(0.0, estimated_render_seconds - elapsed))

        render_payload: dict[str, object] = {
            "percent": int(round(progress_value * 100)),
            "elapsed_seconds": int(elapsed),
        }
        if frames_done is not None and total_frames is not None:
            render_payload["frames_done"] = frames_done
            render_payload["total_frames"] = total_frames

        with write_lock:
            _set_stage(
                job_id,
                "running",
                "Rendering the video",
                progress_percent=overall_percent,
                progress_detail=message or "Rendering frames",
                eta_seconds=eta_seconds,
                render_progress=render_payload,
            )
        last_update["at"] = now
        last_update["percent"] = overall_percent

    return update


def _normalize_video_format(value: str) -> str:
    normalized = (value or "horizontal").strip().lower()
    if normalized in {"vertical", "portrait", "9:16", "reels", "shorts"}:
        return "vertical"
    return "horizontal"


def _public_error_message(message: str) -> str:
    cleaned = re.sub(r"\bHyperFrames\b", "video renderer", message or "", flags=re.IGNORECASE)
    cleaned = re.sub(r"\bhyperframes\b", "video renderer", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bRemotion\b", "video renderer", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bremotion\b", "video renderer", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"VIDEO_RENDERER must be either '[^']+' or '[^']+'", "Video renderer is not configured correctly", cleaned)
    return cleaned or "Video generation failed."
