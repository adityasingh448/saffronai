from dataclasses import dataclass
from pathlib import Path
import os

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")


def _env_path(name: str, default: str) -> Path:
    value = os.getenv(name, default)
    path = Path(value)
    return path if path.is_absolute() else BASE_DIR / path


def _env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    base_dir: Path = BASE_DIR
    data_dir: Path = BASE_DIR / "data"
    jobs_dir: Path = BASE_DIR / "data" / "jobs"
    brand_name: str = os.getenv("BRAND_NAME", "Saffron AI")
    default_language: str = os.getenv("DEFAULT_LANGUAGE", "English")

    openai_api_key: str | None = os.getenv("OPENAI_API_KEY")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

    elevenlabs_api_key: str | None = os.getenv("ELEVENLABS_API_KEY")
    elevenlabs_voice_id: str | None = os.getenv("ELEVENLABS_VOICE_ID")
    elevenlabs_model_id: str = os.getenv("ELEVENLABS_MODEL_ID", "eleven_v3")

    heygen_api_key: str | None = os.getenv("HEYGEN_API_KEY")
    heygen_avatar_id: str | None = os.getenv("HEYGEN_AVATAR_ID")

    video_renderer: str = os.getenv("VIDEO_RENDERER", "local").lower()
    hyperframes_template_dir: Path = _env_path("HYPERFRAMES_TEMPLATE_DIR", "hyperframes-sales-edit")
    hyperframes_cli: str = os.getenv("HYPERFRAMES_CLI", "")
    hyperframes_package: str = os.getenv("HYPERFRAMES_PACKAGE", "hyperframes@0.7.22")
    hyperframes_quality: str = os.getenv("HYPERFRAMES_QUALITY", "medium")
    hyperframes_fps: int = int(os.getenv("HYPERFRAMES_FPS", "30"))
    hyperframes_render_timeout_seconds: int = int(os.getenv("HYPERFRAMES_RENDER_TIMEOUT_SECONDS", "7200"))
    hyperframes_media_tools_dir: Path = _env_path("HYPERFRAMES_MEDIA_TOOLS_DIR", "data/tools/hyperframes-media")
    hyperframes_ffprobe_package: str = os.getenv("HYPERFRAMES_FFPROBE_PACKAGE", "ffprobe-static@3.1.0")

    remotion_template_dir: Path = _env_path("REMOTION_TEMPLATE_DIR", "remotion-report")
    remotion_fps: int = int(os.getenv("REMOTION_FPS", "60"))
    remotion_crf: int = int(os.getenv("REMOTION_CRF", "18"))
    remotion_codec: str = os.getenv("REMOTION_CODEC", "h264")
    remotion_timeout_seconds: int = int(os.getenv("REMOTION_TIMEOUT_SECONDS", "7200"))
    remotion_concurrency: str = os.getenv("REMOTION_CONCURRENCY", "")


settings = Settings()
settings.jobs_dir.mkdir(parents=True, exist_ok=True)
