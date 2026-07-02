from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import re
import shutil
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
    _metadata_path(job_id).write_text(json.dumps(payload, indent=2), encoding="utf-8")


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
    voice_model: str = Form(""),
) -> dict:
    if not pdf.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Please upload a PDF report.")

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
            "max_minutes": max(1, min(max_minutes, 6)),
            "avatar_mode": "off",
            "video_format": _normalize_video_format(video_format),
            "voice_model": resolve_voice_model(voice_model),
            "filename": pdf.filename,
        },
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


def _set_stage(job_id: str, status: str, stage: str, **extra: object) -> None:
    job = _read_job(job_id)
    job.update({"status": status, "stage": stage})
    job.update(extra)
    _write_job(job_id, job)


def _run_pipeline(job_id: str) -> None:
    job_dir = _job_dir(job_id)
    pdf_path = job_dir / "input.pdf"

    try:
        job = _read_job(job_id)
        inputs = job["inputs"]

        _set_stage(job_id, "running", "Reading and rendering PDF pages")
        pages = extract_pdf_pages(pdf_path, job_dir / "pages")

        _set_stage(job_id, "running", "Writing voiceover script")
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

        _set_stage(job_id, "running", "Generating voiceover audio")
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

        if settings.video_renderer == "remotion":
            _set_stage(job_id, "running", "Rendering the video")
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
            )
        elif settings.video_renderer == "hyperframes":
            _set_stage(job_id, "running", "Rendering the video")
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
            _set_stage(job_id, "running", "Rendering the video")
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
            )
        else:
            raise ValueError("Video renderer is not configured correctly.")

        heygen_result = None
        if requested_avatar_mode == "heygen" and can_use_heygen():
            _set_stage(job_id, "running", "Generating HeyGen lip-sync avatar")
            heygen_result = create_heygen_avatar_video(audio_path, job_dir, script.title)
            _set_stage(job_id, "running", "Overlaying HeyGen avatar on walkthrough")
            video_path = overlay_avatar_video(base_video_path, heygen_result.local_path, job_dir / "walkthrough.mp4")

        job = _read_job(job_id)
        job["status"] = "complete"
        job["stage"] = "Ready"
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
            "video_renderer": settings.video_renderer,
            "video_format": inputs.get("video_format", "horizontal"),
        }
        _write_job(job_id, job)
    except Exception as exc:
        job = _read_job(job_id)
        job["status"] = "failed"
        job["stage"] = "Failed"
        job["error"] = _public_error_message(str(exc))
        _write_job(job_id, job)


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
