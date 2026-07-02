from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import math
import platform
import re
import subprocess
import wave

from imageio_ffmpeg import get_ffmpeg_exe
import requests
from mutagen import File as MutagenFile

from app.config import settings
from app.voices import get_voice, normalize_voice_provider, resolve_voice_model


VOICE_PREVIEW_TEXT = (
    "Hi, this is a short preview of the report walkthrough voice. "
    "I will explain each point clearly... what it means for your team, why it matters, and what you can improve next."
)


def synthesize_voiceover(
    script_text: str,
    job_dir: Path,
    voice_model: str | None = None,
    voice_provider: str = "elevenlabs",
) -> tuple[Path, str]:
    script_path = job_dir / "voiceover-script.txt"
    script_path.write_text(script_text, encoding="utf-8")

    provider = normalize_voice_provider(voice_provider)
    selected_voice_id = resolve_voice_model(voice_model, provider=provider)

    if provider == "deepgram":
        if not settings.deepgram_api_key:
            raise RuntimeError("Please add a Deepgram API key before creating the video with Deepgram.")
        return _synthesize_with_deepgram(script_text, job_dir, selected_voice_id), "deepgram"

    if settings.elevenlabs_api_key:
        if not selected_voice_id:
            raise RuntimeError("Please choose an ElevenLabs voice before creating the video.")
        return _synthesize_with_elevenlabs(script_text, job_dir, selected_voice_id), "elevenlabs"

    if platform.system().lower() == "windows":
        try:
            return _synthesize_with_windows_sapi(script_path, job_dir), "windows-sapi"
        except Exception as exc:
            print(f"Windows SAPI synthesis failed, creating silent demo audio: {exc}")

    return _create_silent_audio(script_text, job_dir), "silent-demo"


def synthesize_voice_preview(voice_model: str, output_path: Path, voice_provider: str = "elevenlabs") -> Path:
    provider = normalize_voice_provider(voice_provider)
    if provider == "deepgram":
        if not settings.deepgram_api_key:
            raise RuntimeError("Deepgram is not configured.")
        selected_voice = get_voice(voice_model, provider=provider)
        if not selected_voice:
            raise RuntimeError("Unknown Deepgram voice.")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        _request_deepgram_audio(VOICE_PREVIEW_TEXT, output_path, selected_voice.model)
        return output_path

    if not settings.elevenlabs_api_key:
        raise RuntimeError("ElevenLabs is not configured.")

    selected_voice = get_voice(voice_model, provider=provider)
    if not selected_voice:
        raise RuntimeError("Unknown ElevenLabs voice.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    _request_elevenlabs_audio(VOICE_PREVIEW_TEXT, output_path, selected_voice.model)
    return output_path


def audio_duration_seconds(audio_path: Path, fallback_text: str = "") -> float:
    if audio_path.suffix.lower() == ".wav":
        with wave.open(str(audio_path), "rb") as audio:
            frames = audio.getnframes()
            rate = audio.getframerate()
            return frames / float(rate)

    ffmpeg_duration = _ffmpeg_audio_duration_seconds(audio_path)
    if ffmpeg_duration:
        return ffmpeg_duration

    metadata = MutagenFile(str(audio_path))
    if metadata and metadata.info and getattr(metadata.info, "length", None):
        return float(metadata.info.length)

    return _estimated_spoken_duration(fallback_text)


def _synthesize_with_elevenlabs(script_text: str, job_dir: Path, voice_id: str) -> Path:
    output_path = job_dir / "voiceover.mp3"
    tts_text = _prepare_elevenlabs_text(script_text)
    chunks = _split_tts_text(tts_text, limit=4200)

    if len(chunks) == 1:
        _request_elevenlabs_audio(chunks[0], output_path, voice_id)
        return output_path

    part_paths: list[Path] = []
    for index, chunk in enumerate(chunks, start=1):
        part_path = job_dir / f"voiceover-part-{index:02d}.mp3"
        part_paths.append(part_path)

    _run_elevenlabs_requests_in_parallel(
        [
            (
                chunk,
                part_path,
                voice_id,
                chunks[index - 1][-700:] if index > 0 else None,
                chunks[index + 1][:700] if index < len(chunks) - 1 else None,
            )
            for index, (chunk, part_path) in enumerate(zip(chunks, part_paths))
        ],
    )
    _concat_audio_parts(part_paths, output_path)
    return output_path


def _synthesize_with_deepgram(script_text: str, job_dir: Path, voice_model: str) -> Path:
    output_path = job_dir / "voiceover.mp3"
    tts_text = _prepare_deepgram_text(script_text)
    chunks = _split_tts_text(tts_text, limit=1800)

    if len(chunks) == 1:
        _request_deepgram_audio(chunks[0], output_path, voice_model)
        return output_path

    part_paths: list[Path] = []
    for index, chunk in enumerate(chunks, start=1):
        part_path = job_dir / f"voiceover-deepgram-part-{index:02d}.mp3"
        _request_deepgram_audio(chunk, part_path, voice_model)
        part_paths.append(part_path)
    _concat_audio_parts(part_paths, output_path)
    return output_path


def _run_elevenlabs_requests_in_parallel(
    jobs: list[tuple[str, Path, str, str | None, str | None]],
) -> None:
    max_workers = min(4, len(jobs))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = []
        for script_text, output_path, voice_id, previous_text, next_text in jobs:
            futures.append(
                executor.submit(
                    _request_elevenlabs_audio,
                    script_text,
                    output_path,
                    voice_id,
                    previous_text,
                    next_text,
                )
            )

        for future in futures:
            future.result()


def _prepare_elevenlabs_text(script_text: str) -> str:
    paragraphs = [clean_script for clean_script in re_split_paragraphs(script_text) if clean_script]
    if not paragraphs:
        return script_text

    normalized = []
    for paragraph in paragraphs:
        paragraph = re.sub(r"\s+", " ", paragraph).strip()
        paragraph = re.sub(r"\bPoint\s+(\d+):", r"Point \1. ", paragraph)
        paragraph = paragraph.replace("Now, here is", "Now... here is")
        paragraph = re.sub(r"\b(\d{1,3})\s*/\s*100\b", r"\1 out of 100", paragraph)
        paragraph = re.sub(r"\bSEO\b", "Search Engine Optimization", paragraph)
        paragraph = re.sub(r"\bAEO\b", "Answer Engine Optimization", paragraph)
        paragraph = re.sub(r"\bGEO\b", "Generative Engine Optimization", paragraph)
        paragraph = re.sub(r"\bSERP\b", "Search Engine Results Page", paragraph)
        paragraph = re.sub(r"\bCTR\b", "click-through rate", paragraph)
        paragraph = re.sub(r"\bCPC\b", "cost per click", paragraph)
        paragraph = re.sub(r"\bOG\b", "Open Graph", paragraph)
        paragraph = re.sub(r"\bHQ\b", "headquarters", paragraph)
        paragraph = re.sub(r"\bCEO\b", "chief executive officer", paragraph)
        normalized.append(paragraph)

    return "\n\n".join(normalized)


def _prepare_deepgram_text(script_text: str) -> str:
    text = _prepare_elevenlabs_text(script_text)
    text = re.sub(r"\.\.\.+", "...", text)
    text = re.sub(r"\bFirst point:", "First point.", text, flags=re.IGNORECASE)
    text = re.sub(r"\bNext point:", "Next point.", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _ffmpeg_audio_duration_seconds(audio_path: Path) -> float | None:
    sink = "NUL" if platform.system().lower() == "windows" else "/dev/null"
    try:
        completed = subprocess.run(
            [get_ffmpeg_exe(), "-hide_banner", "-i", str(audio_path), "-f", "null", sink],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except Exception:
        return None

    output = f"{completed.stdout}\n{completed.stderr}"
    matches = re.findall(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)", output)
    if matches:
        return _duration_parts_to_seconds(matches[-1])

    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", output)
    if match:
        return _duration_parts_to_seconds(match.groups())

    return None


def _duration_parts_to_seconds(parts: tuple[str, str, str]) -> float:
    hours, minutes, seconds = parts
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _request_elevenlabs_audio(
    script_text: str,
    output_path: Path,
    voice_id: str,
    previous_text: str | None = None,
    next_text: str | None = None,
) -> None:
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    has_context = bool(previous_text or next_text)
    payload = {
        "text": script_text,
        "model_id": settings.elevenlabs_model_id,
        "voice_settings": {
            "stability": 0.34,
            "similarity_boost": 0.86,
            "style": 0.72,
            "use_speaker_boost": True,
        },
        "apply_text_normalization": "auto",
    }
    if previous_text:
        payload["previous_text"] = previous_text
    if next_text:
        payload["next_text"] = next_text

    response = _post_elevenlabs_tts(url, payload)
    if response.status_code >= 400 and has_context:
        payload.pop("previous_text", None)
        payload.pop("next_text", None)
        response = _post_elevenlabs_tts(url, payload)

    if response.status_code >= 400:
        detail = _safe_response_detail(response)
        raise RuntimeError(f"ElevenLabs voice generation failed ({response.status_code}): {detail}")

    content_type = response.headers.get("content-type", "")
    if not response.content or "audio" not in content_type.lower():
        raise RuntimeError("ElevenLabs did not return audio for the selected voice.")

    output_path.write_bytes(response.content)


def _request_deepgram_audio(script_text: str, output_path: Path, voice_model: str) -> None:
    response = requests.post(
        "https://api.deepgram.com/v1/speak",
        headers={
            "Authorization": f"Token {settings.deepgram_api_key}",
            "Content-Type": "application/json",
        },
        params={"model": voice_model},
        json={"text": script_text},
        timeout=180,
    )
    if response.status_code >= 400:
        detail = _safe_deepgram_response_detail(response)
        raise RuntimeError(f"Deepgram voice generation failed ({response.status_code}): {detail}")

    content_type = response.headers.get("content-type", "")
    if not response.content or "audio" not in content_type.lower():
        detail = _safe_deepgram_response_detail(response)
        raise RuntimeError(f"Deepgram did not return audio for the selected voice. {detail}")

    output_path.write_bytes(response.content)


def _post_elevenlabs_tts(url: str, payload: dict) -> requests.Response:
    response = requests.post(
        url,
        headers={
            "xi-api-key": settings.elevenlabs_api_key,
            "Content-Type": "application/json",
        },
        params={"output_format": "mp3_44100_128"},
        json=payload,
        timeout=180,
    )
    return response


def _safe_response_detail(response: requests.Response) -> str:
    try:
        detail = response.json()
    except ValueError:
        detail = response.text
    text = str(detail)
    if settings.elevenlabs_api_key:
        text = text.replace(settings.elevenlabs_api_key, "[hidden]")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:700] or "No error details returned."


def _safe_deepgram_response_detail(response: requests.Response) -> str:
    try:
        detail = response.json()
    except ValueError:
        detail = response.text
    text = str(detail)
    if settings.deepgram_api_key:
        text = text.replace(settings.deepgram_api_key, "[hidden]")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:700] or "No error details returned."


def _split_tts_text(text: str, limit: int = 4300) -> list[str]:
    paragraphs = [paragraph.strip() for paragraph in re_split_paragraphs(text) if paragraph.strip()]
    chunks: list[str] = []
    current = ""

    for paragraph in paragraphs:
        if len(paragraph) > limit:
            sentence_chunks = _split_long_paragraph(paragraph, limit)
        else:
            sentence_chunks = [paragraph]

        for item in sentence_chunks:
            if current and len(current) + len(item) + 2 > limit:
                chunks.append(current.strip())
                current = item
            else:
                current = f"{current}\n\n{item}".strip() if current else item

    if current:
        chunks.append(current.strip())

    return chunks or [text]


def re_split_paragraphs(text: str) -> list[str]:
    return [part.strip() for part in text.replace("\r\n", "\n").split("\n\n")]


def _split_long_paragraph(text: str, limit: int) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > limit:
            if current:
                chunks.append(current.strip())
                current = ""
            chunks.extend(sentence[index : index + limit] for index in range(0, len(sentence), limit))
            continue
        if current and len(current) + len(sentence) + 1 > limit:
            chunks.append(current.strip())
            current = sentence
        else:
            current = f"{current} {sentence}".strip() if current else sentence
    if current:
        chunks.append(current.strip())
    return chunks


def _concat_audio_parts(part_paths: list[Path], output_path: Path) -> None:
    list_path = output_path.with_suffix(".concat.txt")
    lines = []
    for path in part_paths:
        safe_path = str(path.resolve()).replace("\\", "/").replace("'", "'\\''")
        lines.append(f"file '{safe_path}'")
    list_path.write_text("\n".join(lines), encoding="utf-8")

    ffmpeg = get_ffmpeg_exe()
    command = [
        ffmpeg,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_path),
        "-c",
        "copy",
        str(output_path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode == 0:
        return

    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            "-c:a",
            "libmp3lame",
            "-b:a",
            "128k",
            str(output_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def _synthesize_with_windows_sapi(script_path: Path, job_dir: Path) -> Path:
    output_path = job_dir / "voiceover.wav"
    ps_command = (
        "Add-Type -AssemblyName System.Speech; "
        "$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$synth.Rate = 0; "
        "$synth.Volume = 100; "
        f"$text = Get-Content -LiteralPath '{script_path}' -Raw -Encoding UTF8; "
        f"$synth.SetOutputToWaveFile('{output_path}'); "
        "$synth.Speak($text); "
        "$synth.Dispose();"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_command],
        check=True,
        capture_output=True,
        text=True,
    )
    return output_path


def _create_silent_audio(script_text: str, job_dir: Path) -> Path:
    output_path = job_dir / "voiceover.wav"
    duration = _estimated_spoken_duration(script_text)
    sample_rate = 44100
    total_frames = int(duration * sample_rate)

    with wave.open(str(output_path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)

        chunk = b"\x00\x00" * sample_rate
        frames_written = 0
        while frames_written < total_frames:
            frames = min(sample_rate, total_frames - frames_written)
            audio.writeframes(chunk[: frames * 2])
            frames_written += frames

    return output_path


def _estimated_spoken_duration(script_text: str) -> float:
    words = len(script_text.split())
    minutes = max(words / 130.0, 0.25)
    return math.ceil(minutes * 60)
