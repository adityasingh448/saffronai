from __future__ import annotations

from dataclasses import asdict, dataclass
import math


FPS_OPTIONS = (24, 30, 60)


@dataclass(frozen=True)
class RenderQuality:
    value: str
    label: str
    short_label: str
    description: str
    horizontal_width: int
    horizontal_height: int
    vertical_width: int
    vertical_height: int
    crf: int
    time_factor: float
    recommended: bool = False

    def dimensions(self, video_format: str) -> tuple[int, int]:
        if _is_vertical(video_format):
            return self.vertical_width, self.vertical_height
        return self.horizontal_width, self.horizontal_height

    def to_dict(self, video_format: str = "horizontal", render_fps: int = 30, target_minutes: int = 3) -> dict[str, object]:
        width, height = self.dimensions(video_format)
        payload = asdict(self)
        payload.update(
            {
                "width": width,
                "height": height,
                "estimated_render_seconds": estimate_render_seconds(target_minutes, self.value, render_fps),
            }
        )
        return payload


QUALITY_OPTIONS: tuple[RenderQuality, ...] = (
    RenderQuality(
        value="480p",
        label="480p Fast",
        short_label="480p",
        description="Fast preview render",
        horizontal_width=854,
        horizontal_height=480,
        vertical_width=480,
        vertical_height=854,
        crf=24,
        time_factor=1.8,
    ),
    RenderQuality(
        value="720p",
        label="720p Balanced",
        short_label="720p",
        description="Good quality with faster turnaround",
        horizontal_width=1280,
        horizontal_height=720,
        vertical_width=720,
        vertical_height=1280,
        crf=21,
        time_factor=4.0,
        recommended=True,
    ),
    RenderQuality(
        value="1080p",
        label="1080p Best",
        short_label="1080p",
        description="Full HD final export",
        horizontal_width=1920,
        horizontal_height=1080,
        vertical_width=1080,
        vertical_height=1920,
        crf=18,
        time_factor=7.5,
    ),
)


def quality_options(video_format: str = "horizontal", render_fps: int = 30, target_minutes: int = 3) -> list[dict[str, object]]:
    fps = normalize_render_fps(render_fps)
    minutes = normalize_target_minutes(target_minutes)
    return [quality.to_dict(video_format=video_format, render_fps=fps, target_minutes=minutes) for quality in QUALITY_OPTIONS]


def fps_options() -> list[int]:
    return list(FPS_OPTIONS)


def default_render_quality() -> str:
    return next((quality.value for quality in QUALITY_OPTIONS if quality.recommended), QUALITY_OPTIONS[0].value)


def normalize_render_quality(value: str | None) -> str:
    normalized = (value or default_render_quality()).strip().lower()
    if normalized.isdigit():
        normalized = f"{normalized}p"
    return next((quality.value for quality in QUALITY_OPTIONS if quality.value.lower() == normalized), default_render_quality())


def normalize_render_fps(value: int | str | None) -> int:
    try:
        requested = int(value or 30)
    except (TypeError, ValueError):
        requested = 30
    return min(FPS_OPTIONS, key=lambda option: abs(option - requested))


def normalize_target_minutes(value: int | str | None) -> int:
    try:
        requested = int(value or 3)
    except (TypeError, ValueError):
        requested = 3
    return max(1, min(requested, 6))


def render_dimensions(video_format: str, render_quality: str | None) -> tuple[int, int]:
    return get_quality(render_quality).dimensions(video_format)


def render_crf(render_quality: str | None) -> int:
    return get_quality(render_quality).crf


def get_quality(render_quality: str | None) -> RenderQuality:
    normalized = normalize_render_quality(render_quality)
    return next((quality for quality in QUALITY_OPTIONS if quality.value == normalized), QUALITY_OPTIONS[0])


def estimate_render_seconds(target_minutes: int | str | None, render_quality: str | None, render_fps: int | str | None) -> int:
    minutes = normalize_target_minutes(target_minutes)
    fps = normalize_render_fps(render_fps)
    quality = get_quality(render_quality)
    fps_factor = {24: 0.84, 30: 1.0, 60: 1.82}.get(fps, fps / 30)
    video_seconds = minutes * 60
    setup_seconds = 22
    return int(math.ceil(setup_seconds + video_seconds * quality.time_factor * fps_factor))


def format_duration(seconds: int | float | None) -> str:
    if seconds is None or seconds < 0:
        return "Calculating"
    if seconds <= 0:
        return "Done"
    total = int(round(seconds))
    minutes = total // 60
    remainder = total % 60
    if minutes <= 0:
        return f"{remainder}s"
    if remainder == 0:
        return f"{minutes}m"
    return f"{minutes}m {remainder}s"


def _is_vertical(video_format: str) -> bool:
    return (video_format or "horizontal").strip().lower() in {"vertical", "portrait", "9:16", "reels", "shorts"}
