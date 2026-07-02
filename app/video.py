from __future__ import annotations

from pathlib import Path
import math
import subprocess
from typing import Callable, Iterable

from imageio_ffmpeg import get_ffmpeg_exe
from PIL import Image, ImageDraw, ImageFont, ImageFilter

from app.audio import audio_duration_seconds
from app.pdf_tools import HighlightBox
from app.render_options import normalize_render_fps, render_dimensions
from app.script_writer import PageScript


FPS = 24
LocalProgressCallback = Callable[[float, str, int | None, int | None], None]
SAFFRON = (238, 103, 35)
GOLD = (240, 162, 38)
INK = (20, 27, 39)
MUTED = (103, 116, 135)
PAPER_BG = (244, 247, 251)
WHITE = (255, 255, 255)
LINE = (219, 228, 238)
BLUE = (22, 134, 255)
BACKGROUND_CACHE: dict[tuple[int, int], Image.Image] = {}


def compose_walkthrough_video(
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
    render_fps: int = FPS,
    progress_callback: LocalProgressCallback | None = None,
) -> Path:
    if not page_images:
        raise ValueError("No PDF page images were provided for video composition.")

    duration = max(audio_duration_seconds(audio_path), len(page_images) * 4.0)
    fps = normalize_render_fps(render_fps)
    canvas_size = render_dimensions(video_format, render_quality)
    frame_total = max(int(duration * fps), fps)
    page_durations = _page_durations(page_scripts, duration, len(page_images))
    page_boundaries = _boundaries(page_durations)

    loaded_pages = [Image.open(path).convert("RGB") for path in page_images]
    loaded_highlights = page_highlights or [[] for _ in loaded_pages]
    loaded_highlights = _align_highlight_labels(loaded_highlights, page_scripts, loaded_pages)
    fonts = _load_fonts()

    ffmpeg = get_ffmpeg_exe()
    command = [
        ffmpeg,
        "-y",
        "-f",
        "rawvideo",
        "-vcodec",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{canvas_size[0]}x{canvas_size[1]}",
        "-r",
        str(fps),
        "-i",
        "-",
        "-i",
        str(audio_path),
        "-shortest",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        str(output_path),
    ]

    process = subprocess.Popen(command, stdin=subprocess.PIPE)
    assert process.stdin is not None

    try:
        for frame_index in range(frame_total):
            current_time = frame_index / fps
            page_index, local_progress = _locate_page(current_time, page_boundaries)
            page_index = min(page_index, len(loaded_pages) - 1)
            script = page_scripts[min(page_index, len(page_scripts) - 1)] if page_scripts else None
            frame = _render_frame(
                page_image=loaded_pages[page_index],
                page_number=page_index + 1,
                total_pages=len(loaded_pages),
                progress=local_progress,
                highlights=loaded_highlights[page_index] if page_index < len(loaded_highlights) else [],
                title=title,
                focus=script.focus if script else f"Page {page_index + 1}",
                brand_name=brand_name,
                avatar_mode=avatar_mode,
                prospect_label=prospect_label,
                fonts=fonts,
                frame_index=frame_index,
                canvas_size=canvas_size,
            )
            process.stdin.write(frame.tobytes())
            if progress_callback and (frame_index % fps == 0 or frame_index == frame_total - 1):
                progress_callback(frame_index / max(1, frame_total - 1), "Rendering frames", frame_index, frame_total)
    finally:
        process.stdin.close()

    return_code = process.wait()
    if return_code != 0:
        raise RuntimeError(f"FFmpeg failed with exit code {return_code}")

    return output_path


def _align_highlight_labels(
    page_highlights: list[list[HighlightBox]],
    page_scripts: list[PageScript],
    page_images: list[Image.Image],
) -> list[list[HighlightBox]]:
    aligned: list[list[HighlightBox]] = []
    for index, page_image in enumerate(page_images):
        script = page_scripts[min(index, len(page_scripts) - 1)] if page_scripts else None
        labels = [label for label in (script.highlight_terms if script else []) if _clean_pointer_label(label)]
        items = page_highlights[index] if index < len(page_highlights) else []
        if items:
            relabeled = []
            for item_index, item in enumerate(items):
                if labels and item_index >= len(labels):
                    break
                label = labels[item_index] if item_index < len(labels) else item.label
                relabeled.append(HighlightBox(item.x0, item.y0, item.x1, item.y1, label))
            aligned.append(relabeled)
            continue

        if labels:
            width, height = page_image.size
            aligned.append(
                [
                    HighlightBox(
                        x0=width * 0.15,
                        y0=height * (0.18 + item_index * 0.18),
                        x1=width * 0.85,
                        y1=height * (0.255 + item_index * 0.18),
                        label=label,
                    )
                    for item_index, label in enumerate(labels[:4])
                ]
            )
            continue

        aligned.append(items)
    return aligned


def overlay_avatar_video(base_video: Path, avatar_video: Path, output_path: Path) -> Path:
    ffmpeg = get_ffmpeg_exe()
    filter_graph = (
        "[1:v]scale=230:-1:flags=lanczos,format=rgba,colorchannelmixer=aa=0.92[avatar];"
        "[0:v][avatar]overlay=W-w-34:H-h-34:format=auto[v]"
    )
    command = [
        ffmpeg,
        "-y",
        "-i",
        str(base_video),
        "-stream_loop",
        "-1",
        "-i",
        str(avatar_video),
        "-filter_complex",
        filter_graph,
        "-map",
        "[v]",
        "-map",
        "0:a?",
        "-shortest",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "copy",
        str(output_path),
    ]
    subprocess.run(command, check=True, capture_output=True)
    return output_path


def _page_durations(page_scripts: list[PageScript], total_duration: float, page_count: int) -> list[float]:
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


def _boundaries(durations: Iterable[float]) -> list[tuple[float, float]]:
    boundaries: list[tuple[float, float]] = []
    cursor = 0.0
    for duration in durations:
        boundaries.append((cursor, cursor + duration))
        cursor += duration
    return boundaries


def _locate_page(current_time: float, boundaries: list[tuple[float, float]]) -> tuple[int, float]:
    for index, (start, end) in enumerate(boundaries):
        if current_time <= end:
            progress = 0.0 if end <= start else (current_time - start) / (end - start)
            return index, max(0.0, min(progress, 1.0))

    return len(boundaries) - 1, 1.0


def _render_frame(
    page_image: Image.Image,
    page_number: int,
    total_pages: int,
    progress: float,
    highlights: list[HighlightBox],
    title: str,
    focus: str,
    brand_name: str,
    avatar_mode: str,
    prospect_label: str,
    fonts: dict[str, ImageFont.ImageFont],
    frame_index: int,
    canvas_size: tuple[int, int],
) -> Image.Image:
    width, height = canvas_size
    frame = _background_frame(canvas_size).copy()
    draw = ImageDraw.Draw(frame)

    has_local_presenter = avatar_mode == "local"
    _draw_top_bar(draw, width, title, brand_name, has_local_presenter, fonts, frame_index)

    page_viewport, panel_rect, footer_rect = _layout_regions(canvas_size)
    pointers = _pointer_boxes(highlights, page_image, focus)
    pointer_index, pointer_progress = _active_pointer(progress, len(pointers))
    active_pointer = pointers[pointer_index]
    previous_pointer = pointers[pointer_index - 1] if pointer_index > 0 else None

    placement = _draw_page_view(
        frame,
        page_image,
        page_viewport,
        active_pointer,
        previous_pointer,
        pointer_progress,
        progress,
        frame_index,
    )
    _draw_pointer_panel(
        frame,
        panel_rect,
        active_pointer,
        pointer_index,
        len(pointers),
        focus,
        prospect_label,
        fonts,
        pointer_progress,
        frame_index,
    )
    _draw_pointer_strip(draw, footer_rect, page_number, total_pages, pointers, pointer_index, focus, fonts)
    _draw_page_transition(frame, progress)

    return frame


def _draw_top_bar(
    draw: ImageDraw.ImageDraw,
    width: int,
    title: str,
    brand_name: str,
    has_local_presenter: bool,
    fonts: dict[str, ImageFont.ImageFont],
    frame_index: int,
) -> None:
    if not brand_name:
        return

    draw.rounded_rectangle((30, 18, width - 30, 68), radius=8, fill=(255, 255, 255), outline=(222, 229, 238))
    draw.rounded_rectangle((48, 30, 82, 56), radius=6, fill=(255, 239, 230), outline=(255, 210, 184))
    draw.text((57, 34), "SE", font=fonts["tiny_bold"], fill=SAFFRON)
    draw.text((98, 28), brand_name, font=fonts["medium"], fill=INK)
    draw.text((98, 49), _trim(title, 78), font=fonts["tiny"], fill=MUTED)

    if has_local_presenter:
        _draw_voice_chip(draw, width - 274, 28, 220, 30, fonts, frame_index)


def _layout_regions(canvas_size: tuple[int, int]) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int], tuple[int, int, int, int]]:
    width, height = canvas_size
    if height > width:
        return (
            (36, 78, width - 36, int(height * 0.66)),
            (52, int(height * 0.70), width - 52, height - 76),
            (36, height - 58, width - 36, height - 18),
        )

    return (
        (42, 76, int(width * 0.69), height - 82),
        (int(width * 0.72), 96, width - 42, height - 116),
        (42, height - 62, width - 42, height - 18),
    )


def _draw_background(draw: ImageDraw.ImageDraw, width: int, height: int, frame_index: int) -> None:
    for y in range(0, height + 32, 32):
        for x in range(0, width + 32, 32):
            draw.ellipse((x, y, x + 2, y + 2), fill=(218, 226, 235))

    draw.rounded_rectangle((24, 22, width - 24, height - 22), radius=18, outline=(232, 238, 245), width=1)


def _background_frame(canvas_size: tuple[int, int]) -> Image.Image:
    cached = BACKGROUND_CACHE.get(canvas_size)
    if cached is not None:
        return cached

    width, height = canvas_size
    frame = Image.new("RGB", canvas_size, PAPER_BG)
    draw = ImageDraw.Draw(frame)
    _draw_background(draw, width, height, 0)
    BACKGROUND_CACHE[canvas_size] = frame
    return frame


def _pointer_boxes(highlights: list[HighlightBox], page_image: Image.Image, focus: str) -> list[HighlightBox]:
    cleaned = [
        HighlightBox(item.x0, item.y0, item.x1, item.y1, _clean_pointer_label(item.label))
        for item in highlights[:5]
        if (item.x1 - item.x0) > 8 and (item.y1 - item.y0) > 6
    ]
    if cleaned:
        return cleaned

    width, height = page_image.size
    label = _clean_pointer_label(focus) or "Key section"
    synthetic: list[HighlightBox] = []
    for index, y_ratio in enumerate((0.18, 0.42, 0.66), start=1):
        synthetic.append(
            HighlightBox(
                x0=width * 0.16,
                y0=height * y_ratio,
                x1=width * 0.84,
                y1=height * (y_ratio + 0.075),
                label=label if index == 1 else f"Supporting point {index}",
            )
        )
    return synthetic


def _active_pointer(progress: float, pointer_count: int) -> tuple[int, float]:
    count = max(1, pointer_count)
    scaled = max(0.0, min(progress, 0.999)) * count
    index = min(int(scaled), count - 1)
    return index, scaled - index


def _draw_page_view(
    frame: Image.Image,
    page_image: Image.Image,
    viewport: tuple[int, int, int, int],
    active_pointer: HighlightBox,
    previous_pointer: HighlightBox | None,
    pointer_progress: float,
    page_progress: float,
    frame_index: int,
) -> tuple[int, int, float]:
    left, top, right, bottom = viewport
    view_w = right - left
    view_h = bottom - top

    shadow = Image.new("RGBA", (view_w + 24, view_h + 24), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rounded_rectangle((12, 12, view_w + 5, view_h + 5), radius=8, fill=(15, 23, 42, 26))
    shadow = shadow.filter(ImageFilter.GaussianBlur(9))
    frame.paste(shadow, (left - 12, top - 9), shadow)

    viewport_image = Image.new("RGB", (view_w, view_h), (252, 253, 255))
    start_state = _camera_state(page_image, view_w, view_h, previous_pointer)
    end_state = _camera_state(page_image, view_w, view_h, active_pointer)
    if previous_pointer is None:
        start_state = _camera_state(page_image, view_w, view_h, None)

    if page_progress < 0.12 and previous_pointer is None:
        camera_mix = _ease_in_out(page_progress / 0.12)
    else:
        camera_mix = _ease_in_out(min(1.0, pointer_progress / 0.30))

    breath = 1.0 + (0.004 * math.sin(frame_index / 8.0))
    scale = _lerp(start_state[2], end_state[2], camera_mix) * breath
    scaled_w = max(1, int(page_image.width * scale))
    scaled_h = max(1, int(page_image.height * scale))
    resized = page_image.resize((scaled_w, scaled_h), Image.Resampling.BICUBIC)

    x = int(_lerp(start_state[0], end_state[0], camera_mix))
    y = int(_lerp(start_state[1], end_state[1], camera_mix))

    viewport_image.paste(resized, (x, y))
    frame.paste(viewport_image, (left, top))

    draw = ImageDraw.Draw(frame)
    draw.rounded_rectangle((left, top, right, bottom), radius=8, outline=(197, 208, 222), width=2)
    return left + x, top + y, scale


def _camera_state(
    page_image: Image.Image,
    view_w: int,
    view_h: int,
    target: HighlightBox | None,
) -> tuple[float, float, float]:
    base_scale = min((view_w * 0.76) / page_image.width, (view_h * 0.94) / page_image.height)
    if target is None:
        scaled_w = page_image.width * base_scale
        scaled_h = page_image.height * base_scale
        return (view_w - scaled_w) / 2, (view_h - scaled_h) / 2, base_scale

    target_w = max(1.0, target.x1 - target.x0)
    target_h = max(1.0, target.y1 - target.y0)
    target_scale = min((view_w * 0.58) / target_w, (view_h * 0.28) / target_h)
    focus_scale = max(base_scale * 1.42, min(base_scale * 2.25, target_scale))

    scaled_w = page_image.width * focus_scale
    scaled_h = page_image.height * focus_scale
    focus_x = ((target.x0 + target.x1) / 2) * focus_scale
    focus_y = ((target.y0 + target.y1) / 2) * focus_scale
    x = (view_w * 0.50) - focus_x
    y = (view_h * 0.42) - focus_y

    if scaled_w <= view_w:
        x = (view_w - scaled_w) / 2
    else:
        x = _clamp(x, view_w - scaled_w - 24, 24)

    if scaled_h <= view_h:
        y = (view_h - scaled_h) / 2
    else:
        y = _clamp(y, view_h - scaled_h - 24, 24)

    return x, y, focus_scale


def _draw_text_highlight(
    frame: Image.Image,
    viewport: tuple[int, int, int, int],
    placement: tuple[int, int, float],
    active_pointer: HighlightBox,
    pointer_progress: float,
    frame_index: int,
) -> None:
    alpha_factor = _highlight_alpha(pointer_progress)
    if alpha_factor <= 0:
        return

    page_left, page_top, page_scale = placement
    box = active_pointer
    x0 = page_left + int(box.x0 * page_scale) - 3
    y0 = page_top + int(box.y0 * page_scale) - 2
    x1 = page_left + int(box.x1 * page_scale) + 3
    y1 = page_top + int(box.y1 * page_scale) + 2

    left, top, right, bottom = viewport
    if x1 < left or x0 > right or y1 < top or y0 > bottom:
        return

    x0 = max(left + 4, x0)
    y0 = max(top + 4, y0)
    x1 = min(right - 4, x1)
    y1 = min(bottom - 4, y1)

    overlay = Image.new("RGBA", frame.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    pulse = int(28 * (0.5 + 0.5 * math.sin(frame_index / 3.4)))
    draw.rounded_rectangle(
        (x0, y0, x1, y1),
        radius=4,
        fill=(255, 217, 84, int((112 + pulse) * alpha_factor)),
        outline=(*SAFFRON, int(210 * alpha_factor)),
        width=3,
    )
    dot_x = max(left + 12, x0 - 22)
    dot_y = (y0 + y1) // 2
    draw.ellipse((dot_x - 8, dot_y - 8, dot_x + 8, dot_y + 8), fill=(*SAFFRON, int(230 * alpha_factor)))
    _composite(frame, overlay)


def _highlight_alpha(progress: float) -> float:
    progress = max(0.0, min(progress, 1.0))
    if progress < 0.18:
        return progress / 0.18
    if progress > 0.76:
        return max(0.0, (1.0 - progress) / 0.24)
    return 1.0


def _draw_pointer_panel(
    frame: Image.Image,
    rect: tuple[int, int, int, int],
    pointer: HighlightBox,
    pointer_index: int,
    pointer_count: int,
    focus: str,
    prospect_label: str,
    fonts: dict[str, ImageFont.ImageFont],
    pointer_progress: float,
    frame_index: int,
) -> None:
    progress = _ease_out(min(1.0, pointer_progress / 0.26))
    left, top, right, bottom = rect
    width = right - left
    height = bottom - top
    slide = int((1.0 - progress) * 34)
    alpha = int(255 * progress)

    overlay = Image.new("RGBA", frame.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    x0 = left + slide
    x1 = right + slide
    draw.rounded_rectangle((x0 + 8, top + 10, x1 + 8, bottom + 10), radius=12, fill=(15, 23, 42, int(24 * progress)))
    draw.rounded_rectangle((x0, top, x1, bottom), radius=12, fill=(*WHITE, alpha), outline=(*LINE, alpha), width=2)

    label = _clean_pointer_label(pointer.label) or _clean_pointer_label(focus) or "Key point"
    draw.text((x0 + 24, top + 24), f"Point {pointer_index + 1:02d}", font=fonts["small_bold"], fill=(*SAFFRON, alpha))
    draw.text((x1 - 66, top + 24), f"{pointer_index + 1}/{pointer_count}", font=fonts["small_bold"], fill=(*MUTED, alpha))
    headline_y = top + 54
    _draw_wrapped_text(
        draw,
        _trim(label, 86),
        (x0 + 24, headline_y),
        width - 48,
        fonts["large_bold"],
        (*INK, alpha),
        line_gap=4,
        max_lines=3,
    )

    graphic_top = top + int(height * 0.38)
    _draw_motion_graphic(draw, (x0 + 24, graphic_top, x1 - 24, graphic_top + 120), pointer_index, frame_index, alpha)

    body_top = graphic_top + 142
    draw.text((x0 + 24, body_top), "What this means", font=fonts["small_bold"], fill=(*INK, alpha))
    takeaway = _viewer_takeaway(label, focus)
    _draw_wrapped_text(
        draw,
        takeaway,
        (x0 + 24, body_top + 26),
        width - 48,
        fonts["regular"],
        (*MUTED, alpha),
        line_gap=5,
        max_lines=3,
    )

    chip_y = bottom - 52
    for index, chip in enumerate(("Fix", "Prioritize", "Measure")):
        chip_left = x0 + 24 + index * 92
        draw.rounded_rectangle((chip_left, chip_y, chip_left + 78, chip_y + 30), radius=6, fill=(255, 244, 237, alpha), outline=(255, 203, 174, alpha))
        draw.text((chip_left + 14, chip_y + 7), chip, font=fonts["tiny_bold"], fill=(*SAFFRON, alpha))

    _composite(frame, overlay)


def _draw_motion_graphic(
    draw: ImageDraw.ImageDraw,
    rect: tuple[int, int, int, int],
    pointer_index: int,
    frame_index: int,
    alpha: int,
) -> None:
    left, top, right, bottom = rect
    draw.rounded_rectangle((left, top, right, bottom), radius=10, fill=(248, 250, 252, alpha), outline=(226, 232, 240, alpha))
    baseline = bottom - 24
    bar_w = max(12, (right - left - 92) // 5)
    for index in range(5):
        phase = min(1.0, max(0.0, (frame_index % FPS) / FPS + index * 0.06))
        growth = _ease_out(phase)
        target_h = 22 + ((index + pointer_index) % 4) * 12 + index * 8
        bar_h = int(target_h * growth)
        x = left + 22 + index * (bar_w + 10)
        color = SAFFRON if index == 4 else (255, 183, 120)
        draw.rounded_rectangle((x, baseline - bar_h, x + bar_w, baseline), radius=5, fill=(*color, alpha))

    arrow_points = [
        (right - 90, baseline - 12),
        (right - 42, top + 30),
        (right - 48, top + 58),
        (right - 26, top + 22),
        (right - 64, top + 26),
    ]
    draw.line(arrow_points[:2], fill=(*BLUE, alpha), width=5)
    draw.polygon(arrow_points[1:], fill=(*BLUE, alpha))
    draw.ellipse((right - 108, baseline - 30, right - 84, baseline - 6), fill=(236, 248, 255, alpha), outline=(*BLUE, alpha), width=2)


def _viewer_takeaway(label: str, focus: str) -> str:
    label = _clean_pointer_label(label) or _clean_pointer_label(focus) or "this point"
    return (
        f"This section is about {label}. "
        "Focus your next action here: confirm the gap, fix the highest-impact item, then measure the result."
    )


def _draw_pointer_strip(
    draw: ImageDraw.ImageDraw,
    rect: tuple[int, int, int, int],
    page_number: int,
    total_pages: int,
    pointers: list[HighlightBox],
    active_index: int,
    focus: str,
    fonts: dict[str, ImageFont.ImageFont],
) -> None:
    left, top, right, bottom = rect
    draw.rounded_rectangle((left, top, right, bottom), radius=8, fill=WHITE, outline=(226, 232, 240))
    draw.text((left + 16, top + 12), f"Page {page_number} / {total_pages}", font=fonts["small_bold"], fill=SAFFRON)

    active_label = _clean_pointer_label(pointers[active_index].label) or _clean_pointer_label(focus) or "Key point"
    draw.text((left + 138, top + 12), _trim(f"Point {active_index + 1}: {active_label}", 92), font=fonts["small"], fill=INK)

    chip_right = right - 16
    chip_w = 22
    gap = 7
    for index in range(min(len(pointers), 5) - 1, -1, -1):
        x1 = chip_right - (len(pointers[:5]) - 1 - index) * (chip_w + gap)
        x0 = x1 - chip_w
        fill = SAFFRON if index == active_index else (238, 242, 247)
        color = WHITE if index == active_index else MUTED
        draw.rounded_rectangle((x0, top + 10, x1, top + 32), radius=5, fill=fill)
        draw.text((x0 + 7, top + 14), str(index + 1), font=fonts["tiny_bold"], fill=color)


def _draw_page_transition(frame: Image.Image, progress: float) -> None:
    alpha = 0
    if progress < 0.055:
        alpha = int(245 * (1.0 - (progress / 0.055)))
    elif progress > 0.965:
        alpha = int(220 * ((progress - 0.965) / 0.035))

    if alpha <= 0:
        return

    overlay = Image.new("RGBA", frame.size, (*WHITE, alpha))
    _composite(frame, overlay)


def _draw_voice_chip(
    draw: ImageDraw.ImageDraw,
    left: int,
    top: int,
    width: int,
    height: int,
    fonts: dict[str, ImageFont.ImageFont],
    frame_index: int,
) -> None:
    right = left + width
    bottom = top + height
    draw.rounded_rectangle((left, top, right, bottom), radius=8, fill=(255, 247, 242), outline=(255, 214, 190))
    draw.text((left + 14, top + 8), "Narrated", font=fonts["tiny_bold"], fill=SAFFRON)

    wave_left = left + 94
    for index in range(9):
        phase = (frame_index / 3.5) + index * 0.85
        bar_h = 5 + int(14 * (0.5 + 0.5 * math.sin(phase)))
        x = wave_left + index * 11
        draw.rounded_rectangle((x, bottom - 7 - bar_h, x + 5, bottom - 7), radius=3, fill=SAFFRON if index % 2 else GOLD)


def _ease_in_out(value: float) -> float:
    return 0.5 - 0.5 * math.cos(max(0.0, min(value, 1.0)) * math.pi)


def _ease_out(value: float) -> float:
    value = max(0.0, min(value, 1.0))
    return 1.0 - (1.0 - value) * (1.0 - value)


def _lerp(start: float, end: float, amount: float) -> float:
    return start + (end - start) * max(0.0, min(amount, 1.0))


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(value, upper))


def _canvas_size(video_format: str) -> tuple[int, int]:
    normalized = (video_format or "horizontal").strip().lower()
    if normalized in {"vertical", "portrait", "9:16", "reels", "shorts"}:
        return 720, 1280
    return 1280, 720


def _trim(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _clean_pointer_label(text: str) -> str:
    cleaned = " ".join((text or "").split())
    cleaned = re_sub_visual_words(cleaned)
    cleaned = cleaned.strip(" .:-")
    if cleaned.lower() in {"main point", "key section", "supporting point"}:
        return cleaned
    return cleaned


def re_sub_visual_words(text: str) -> str:
    replacements = (
        ("report visuals", "report section"),
        ("visual section", "report section"),
        ("visual context", "report context"),
        ("visuals", "sections"),
    )
    cleaned = text
    for source, target in replacements:
        cleaned = cleaned.replace(source, target).replace(source.title(), target.title())
    return cleaned


def _draw_wrapped_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    xy: tuple[int, int],
    max_width: int,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int, int] | tuple[int, int, int],
    line_gap: int = 4,
    max_lines: int = 4,
) -> int:
    x, y = xy
    lines = _wrap_text(text, max_width, font, draw, max_lines=max_lines)
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        bbox = draw.textbbox((x, y), line, font=font)
        y += (bbox[3] - bbox[1]) + line_gap
    return y


def _wrap_text(
    text: str,
    max_width: int,
    font: ImageFont.ImageFont,
    draw: ImageDraw.ImageDraw,
    max_lines: int,
) -> list[str]:
    words = text.split()
    if not words:
        return []

    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = word
        if len(lines) >= max_lines:
            break

    if current and len(lines) < max_lines:
        lines.append(current)

    if len(lines) == max_lines and words:
        joined = " ".join(lines)
        if len(joined) < len(text):
            lines[-1] = _trim(lines[-1], max(8, len(lines[-1]) - 3))
    return lines


def _composite(frame: Image.Image, overlay: Image.Image) -> None:
    composed = Image.alpha_composite(frame.convert("RGBA"), overlay).convert("RGB")
    frame.paste(composed)


def _load_fonts() -> dict[str, ImageFont.ImageFont]:
    candidates = [
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/segoeui.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    bold_candidates = [
        Path("C:/Windows/Fonts/arialbd.ttf"),
        Path("C:/Windows/Fonts/segoeuib.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ]
    regular = next((path for path in candidates if path.exists()), None)
    bold = next((path for path in bold_candidates if path.exists()), regular)

    if regular and bold:
        return {
            "tiny": ImageFont.truetype(str(regular), 13),
            "tiny_bold": ImageFont.truetype(str(bold), 13),
            "small": ImageFont.truetype(str(regular), 16),
            "small_bold": ImageFont.truetype(str(bold), 16),
            "regular": ImageFont.truetype(str(regular), 18),
            "medium": ImageFont.truetype(str(bold), 20),
            "large_bold": ImageFont.truetype(str(bold), 27),
            "avatar": ImageFont.truetype(str(bold), 48),
        }

    fallback = ImageFont.load_default()
    return {name: fallback for name in ["tiny", "tiny_bold", "small", "small_bold", "regular", "medium", "large_bold", "avatar"]}
