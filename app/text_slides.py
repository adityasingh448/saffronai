from __future__ import annotations

from pathlib import Path
import re
import textwrap

from PIL import Image, ImageDraw, ImageFont

from app.pdf_tools import HighlightBox
from app.script_writer import PageScript, clean_script_text
from app.text_tools import TextDocument


SLIDE_WIDTH = 1600
SLIDE_HEIGHT = 1000
INK = "#182033"
MUTED = "#64748b"
SAFFRON = "#ee6723"
BLUE = "#1f9bd1"
GREEN = "#17a06b"
LINE = "#d8e2ef"
PAPER = "#ffffff"
BACKGROUND = "#f6f8fb"


def create_text_slides(
    document: TextDocument,
    page_scripts: list[PageScript],
    output_dir: Path,
) -> tuple[list[Path], list[list[HighlightBox]]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    images: list[Path] = []
    highlights: list[list[HighlightBox]] = []

    for index, script in enumerate(page_scripts, start=1):
        image_path = output_dir / f"text-point-{index:02d}.png"
        slide_highlights = _draw_slide(image_path, document, script, index, len(page_scripts))
        images.append(image_path)
        highlights.append(slide_highlights)

    return images, highlights


def _draw_slide(
    image_path: Path,
    document: TextDocument,
    script: PageScript,
    index: int,
    total: int,
) -> list[HighlightBox]:
    image = Image.new("RGB", (SLIDE_WIDTH, SLIDE_HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)
    _draw_dotted_background(draw)

    margin = 92
    panel = (margin, 82, SLIDE_WIDTH - margin, SLIDE_HEIGHT - 82)
    draw.rounded_rectangle(panel, radius=34, fill=(255, 255, 255), outline=LINE, width=2)

    _draw_header(draw, document, index, total)
    _draw_motion_graphic(draw, index)

    title_font = _font(60, "bold")
    body_font = _font(32, "regular")
    small_font = _font(24, "medium")
    label_font = _font(22, "bold")

    focus = _visible(script.focus or f"Key point {index}", 76)
    summary = _summary_from_narration(script.narration, focus)
    action = _action_from_narration(script.narration)

    title_box = (132, 188, 1040, 330)
    title_text_box = _draw_wrapped_text(draw, focus, title_box, title_font, INK, line_spacing=8)
    draw.text((132, 152), "KEY POINT", font=label_font, fill=SAFFRON)

    summary_panel = (132, 388, 1038, 612)
    _draw_card(draw, summary_panel, "What this means", summary, body_font, small_font)

    action_panel = (132, 646, 1038, 852)
    _draw_card(draw, action_panel, "Next move", action, body_font, small_font)

    source_label = _visible(document.title or document.source_name or "Source file", 54)
    draw.rounded_rectangle((1092, 612, 1466, 852), radius=24, fill="#f8fafc", outline=LINE, width=2)
    draw.text((1130, 646), "SOURCE", font=label_font, fill=SAFFRON)
    _draw_wrapped_text(draw, source_label, (1130, 690, 1428, 780), _font(31, "bold"), INK, line_spacing=6)
    draw.text((1130, 806), "Text-based walkthrough", font=small_font, fill=MUTED)

    image.save(image_path, quality=96)

    highlight_labels = script.highlight_terms or [focus, summary, action]
    return [
        _box_from_rect(title_text_box, highlight_labels[0] if len(highlight_labels) > 0 else focus),
        _box_from_rect(summary_panel, highlight_labels[1] if len(highlight_labels) > 1 else summary),
        _box_from_rect(action_panel, highlight_labels[2] if len(highlight_labels) > 2 else action),
    ]


def _draw_dotted_background(draw: ImageDraw.ImageDraw) -> None:
    for y in range(0, SLIDE_HEIGHT, 32):
        for x in range(0, SLIDE_WIDTH, 32):
            draw.ellipse((x, y, x + 2, y + 2), fill="#d9e2ee")


def _draw_header(draw: ImageDraw.ImageDraw, document: TextDocument, index: int, total: int) -> None:
    draw.text((132, 112), "REPORT WALKTHROUGH", font=_font(24, "bold"), fill=SAFFRON)
    count = f"{index}/{total}"
    bbox = draw.textbbox((0, 0), count, font=_font(26, "bold"))
    pill_w = bbox[2] - bbox[0] + 46
    draw.rounded_rectangle((SLIDE_WIDTH - 132 - pill_w, 106, SLIDE_WIDTH - 132, 152), radius=18, fill="#111827")
    draw.text((SLIDE_WIDTH - 132 - pill_w + 23, 115), count, font=_font(24, "bold"), fill=PAPER)


def _draw_motion_graphic(draw: ImageDraw.ImageDraw, index: int) -> None:
    left = 1100
    top = 178
    draw.rounded_rectangle((left, top, left + 360, top + 360), radius=32, fill="#f8fafc", outline=LINE, width=2)
    colors = [SAFFRON, BLUE, GREEN]
    values = [0.62, 0.84, 0.48]
    for item, value in enumerate(values):
        x = left + 58 + item * 94
        bar_h = int(210 * value)
        draw.rounded_rectangle((x, top + 284 - bar_h, x + 48, top + 284), radius=14, fill=colors[(item + index) % len(colors)])
        draw.ellipse((x - 8, top + 284 - bar_h - 16, x + 56, top + 284 - bar_h + 48), outline=colors[(item + index) % len(colors)], width=5)

    center = (left + 252, top + 114)
    draw.arc((center[0] - 68, center[1] - 68, center[0] + 68, center[1] + 68), start=210, end=520, fill=SAFFRON, width=11)
    draw.ellipse((center[0] - 18, center[1] - 18, center[0] + 18, center[1] + 18), fill="#111827")
    draw.text((left + 42, top + 318), "Signals to act on", font=_font(25, "bold"), fill=INK)


def _draw_card(
    draw: ImageDraw.ImageDraw,
    rect: tuple[int, int, int, int],
    label: str,
    body: str,
    body_font: ImageFont.FreeTypeFont,
    label_font: ImageFont.FreeTypeFont,
) -> None:
    draw.rounded_rectangle(rect, radius=26, fill="#fbfdff", outline=LINE, width=2)
    x0, y0, x1, y1 = rect
    draw.text((x0 + 32, y0 + 28), label.upper(), font=label_font, fill=SAFFRON)
    _draw_wrapped_text(draw, body, (x0 + 32, y0 + 72, x1 - 32, y1 - 28), body_font, INK, line_spacing=8)


def _draw_wrapped_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    rect: tuple[int, int, int, int],
    font: ImageFont.FreeTypeFont,
    fill: str,
    line_spacing: int = 6,
) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = rect
    width = max(20, x1 - x0)
    lines = _wrap_text(draw, text, font, width)
    line_height = font.getbbox("Ag")[3] - font.getbbox("Ag")[1] + line_spacing
    y = y0
    used_bottom = y0
    for line in lines:
        if y + line_height > y1:
            break
        draw.text((x0, y), line, font=font, fill=fill)
        used_bottom = y + line_height
        y += line_height
    return (x0, y0, x1, min(y1, used_bottom + 4))


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        probe = f"{current} {word}".strip()
        if draw.textlength(probe, font=font) <= width:
            current = probe
            continue
        if current:
            lines.append(current)
        if draw.textlength(word, font=font) <= width:
            current = word
        else:
            wrapped = textwrap.wrap(word, width=18)
            lines.extend(wrapped[:-1])
            current = wrapped[-1] if wrapped else ""
    if current:
        lines.append(current)
    return lines


def _summary_from_narration(narration: str, focus: str) -> str:
    cleaned = clean_script_text(narration)
    cleaned = re.sub(r"^hi there,\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^(first point|next point)\s*:\s*", "", cleaned, flags=re.IGNORECASE)
    sentences = [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", cleaned) if len(sentence.strip()) > 24]
    if len(sentences) >= 2:
        summary = sentences[1]
    elif sentences:
        summary = sentences[0]
    else:
        summary = f"Focus on {focus} and turn it into one practical improvement."
    return _visible(summary, 34)


def _action_from_narration(narration: str) -> str:
    sentences = [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", clean_script_text(narration)) if sentence.strip()]
    for sentence in reversed(sentences):
        if re.search(r"\b(start|fix|add|choose|track|measure|tighten|reduce|validate|compare)\b", sentence, flags=re.IGNORECASE):
            return _visible(sentence, 30)
    return _visible(sentences[-1] if sentences else "Choose one owner, define the first fix, and measure the change.", 30)


def _visible(text: str, limit_words: int) -> str:
    cleaned = clean_script_text(text)
    cleaned = re.sub(r"\bclient\b", "viewer", cleaned, flags=re.IGNORECASE)
    words = cleaned.split()
    if len(words) > limit_words:
        cleaned = " ".join(words[:limit_words]) + "..."
    return cleaned.strip(" .:-")


def _box_from_rect(rect: tuple[int, int, int, int], label: str) -> HighlightBox:
    x0, y0, x1, y1 = rect
    return HighlightBox(x0=float(x0), y0=float(y0), x1=float(x1), y1=float(y1), label=_visible(label, 12))


def _font(size: int, weight: str) -> ImageFont.FreeTypeFont:
    candidates = []
    if weight == "bold":
        candidates.extend(
            [
                "C:/Windows/Fonts/Inter-Bold.ttf",
                "C:/Windows/Fonts/segoeuib.ttf",
                "C:/Windows/Fonts/arialbd.ttf",
            ]
        )
    elif weight == "medium":
        candidates.extend(
            [
                "C:/Windows/Fonts/Inter-SemiBold.ttf",
                "C:/Windows/Fonts/segoeuisl.ttf",
                "C:/Windows/Fonts/arial.ttf",
            ]
        )
    candidates.extend(
        [
            "C:/Windows/Fonts/Inter-Regular.ttf",
            "C:/Windows/Fonts/segoeui.ttf",
            "C:/Windows/Fonts/arial.ttf",
        ]
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)
