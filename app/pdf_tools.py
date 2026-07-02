from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

import fitz


@dataclass
class HighlightBox:
    x0: float
    y0: float
    x1: float
    y1: float
    label: str


@dataclass
class PdfPage:
    page_number: int
    text: str
    image_path: Path
    width: float
    height: float
    render_scale: float
    lines: list[str]
    highlights: list[HighlightBox]
    heading: str = ""
    heading_box: HighlightBox | None = None


def clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text


def extract_pdf_pages(pdf_path: Path, output_dir: Path, max_pages: int = 24) -> list[PdfPage]:
    output_dir.mkdir(parents=True, exist_ok=True)
    pages: list[PdfPage] = []
    render_scale = 2.0

    with fitz.open(pdf_path) as doc:
        for index, page in enumerate(doc):
            if index >= max_pages:
                break

            page_number = index + 1
            raw_text = page.get_text("text")
            text = clean_text(raw_text)
            lines = _extract_text_lines(raw_text)
            heading, heading_box = _extract_page_heading(page, render_scale)
            highlights = _merge_highlight_boxes(heading_box, _extract_highlight_boxes(page, render_scale))
            matrix = fitz.Matrix(render_scale, render_scale)
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            image_path = output_dir / f"page-{page_number:02d}.png"
            pixmap.save(str(image_path))

            pages.append(
                PdfPage(
                    page_number=page_number,
                    text=text,
                    image_path=image_path,
                    width=page.rect.width,
                    height=page.rect.height,
                    render_scale=render_scale,
                    lines=lines,
                    highlights=highlights,
                    heading=heading,
                    heading_box=heading_box,
                )
            )

    if not pages:
        raise ValueError("The PDF did not contain any readable pages.")

    return pages


def page_excerpt(text: str, limit: int = 1200) -> str:
    text = clean_text(text)
    if len(text) <= limit:
        return text

    clipped = text[:limit].rsplit(" ", 1)[0]
    return f"{clipped}..."


def _extract_text_lines(raw_text: str) -> list[str]:
    lines = []
    for line in raw_text.splitlines():
        cleaned = clean_text(line)
        if cleaned:
            lines.append(cleaned)
    return lines


def _extract_page_heading(page: fitz.Page, render_scale: float) -> tuple[str, HighlightBox | None]:
    try:
        structured = page.get_text("dict", sort=True)
    except Exception:
        structured = {}

    candidates: list[tuple[float, HighlightBox]] = []
    page_height = max(1.0, float(page.rect.height))

    for block in structured.get("blocks", []):
        if block.get("type") != 0:
            continue

        for line in block.get("lines", []):
            spans = line.get("spans", [])
            parts = [clean_text(str(span.get("text", ""))) for span in spans]
            label = clean_text(" ".join(part for part in parts if part))
            if not _is_heading_candidate(label):
                continue

            bbox = line.get("bbox")
            if not bbox or len(bbox) < 4:
                bbox = _combined_span_bbox(spans)
            if not bbox:
                continue

            max_size = max((float(span.get("size", 0) or 0) for span in spans), default=0.0)
            bold = any("bold" in str(span.get("font", "")).lower() for span in spans)
            y0 = float(bbox[1])
            word_count = len(label.split())
            important_hits = sum(1 for word in label.split() if _normalize_word(word) in IMPORTANT_TERMS)
            top_bonus = max(0.0, (page_height * 0.42 - y0) / (page_height * 0.42)) * 9.0
            length_bonus = 4.0 if 2 <= word_count <= 12 else 0.0
            casing_bonus = 2.0 if label[:1].isupper() else 0.0
            score = max_size * 2.2 + (7.0 if bold else 0.0) + top_bonus + length_bonus + casing_bonus + important_hits * 2.5

            candidates.append((score, _scaled_box(bbox, render_scale, label)))

    if candidates:
        _, heading_box = max(candidates, key=lambda item: item[0])
        return heading_box.label, heading_box

    for line in _extract_text_lines(page.get_text("text")):
        if _is_heading_candidate(line):
            return line, None

    return "", None


def _combined_span_bbox(spans: list[dict]) -> tuple[float, float, float, float] | None:
    boxes = [span.get("bbox") for span in spans if span.get("bbox") and len(span.get("bbox")) >= 4]
    if not boxes:
        return None

    return (
        min(float(box[0]) for box in boxes),
        min(float(box[1]) for box in boxes),
        max(float(box[2]) for box in boxes),
        max(float(box[3]) for box in boxes),
    )


def _scaled_box(bbox: tuple[float, float, float, float], render_scale: float, label: str) -> HighlightBox:
    return HighlightBox(
        x0=float(bbox[0]) * render_scale,
        y0=float(bbox[1]) * render_scale,
        x1=float(bbox[2]) * render_scale,
        y1=float(bbox[3]) * render_scale,
        label=clean_text(label),
    )


def _merge_highlight_boxes(heading_box: HighlightBox | None, highlights: list[HighlightBox]) -> list[HighlightBox]:
    merged: list[HighlightBox] = []
    if heading_box:
        merged.append(heading_box)

    for highlight in highlights:
        if not _is_duplicate_box(highlight, merged):
            merged.append(highlight)
        if len(merged) >= 12:
            break

    return merged


def _is_duplicate_box(candidate: HighlightBox, existing: list[HighlightBox]) -> bool:
    candidate_label = _normalize_label(candidate.label)
    for item in existing:
        if candidate_label and candidate_label == _normalize_label(item.label):
            return True
        overlap_x = max(0.0, min(candidate.x1, item.x1) - max(candidate.x0, item.x0))
        overlap_y = max(0.0, min(candidate.y1, item.y1) - max(candidate.y0, item.y0))
        overlap_area = overlap_x * overlap_y
        candidate_area = max(1.0, (candidate.x1 - candidate.x0) * (candidate.y1 - candidate.y0))
        if overlap_area / candidate_area > 0.55:
            return True
    return False


def _is_heading_candidate(text: str) -> bool:
    text = clean_text(text)
    if not text:
        return False

    words = text.split()
    normalized = _normalize_label(text)
    if len(text) < 4 or len(text) > 120 or len(words) > 18:
        return False
    if normalized in {"saffronedge", "saffronai", "page", "wwwsaffronedgecom"}:
        return False
    if re.fullmatch(r"(page\s*)?\d+(\s*/\s*\d+)?", text, flags=re.IGNORECASE):
        return False
    if re.search(r"https?://|www\.|@", text, flags=re.IGNORECASE):
        return False
    if text.endswith(".") and len(words) > 9:
        return False

    return bool(re.search(r"[A-Za-z0-9]", text))


def _extract_highlight_boxes(page: fitz.Page, render_scale: float) -> list[HighlightBox]:
    words = page.get_text("words", sort=True)
    line_groups: dict[tuple[int, int], list[tuple]] = {}

    for word in words:
        if len(word) < 8:
            continue
        text = str(word[4]).strip()
        if not _is_meaningful_word(text):
            continue
        key = (int(word[5]), int(word[6]))
        line_groups.setdefault(key, []).append(word)

    candidates: list[tuple[float, float, HighlightBox]] = []
    for order, line_words in enumerate(line_groups.values()):
        line_words = sorted(line_words, key=lambda item: int(item[7]))
        selected = _select_phrase_words(line_words)
        if not selected:
            continue

        label = clean_text(" ".join(str(item[4]) for item in selected))
        if len(label) < 4:
            continue

        x0 = min(float(item[0]) for item in selected) * render_scale
        y0 = min(float(item[1]) for item in selected) * render_scale
        x1 = max(float(item[2]) for item in selected) * render_scale
        y1 = max(float(item[3]) for item in selected) * render_scale
        score = _highlight_score(label, order)
        candidates.append((score, y0, HighlightBox(x0=x0, y0=y0, x1=x1, y1=y1, label=label)))

    top_candidates = sorted(candidates, key=lambda item: item[0], reverse=True)[:12]
    top_candidates.sort(key=lambda item: (item[2].y0, item[2].x0))
    return [item[2] for item in top_candidates]


def _select_phrase_words(line_words: list[tuple]) -> list[tuple]:
    if not line_words:
        return []

    if len(line_words) <= 8:
        return line_words

    anchor_index = None
    for index, word in enumerate(line_words):
        normalized = _normalize_word(str(word[4]))
        if normalized in IMPORTANT_TERMS:
            anchor_index = index
            break

    if anchor_index is None:
        anchor_index = 0

    start = max(0, anchor_index - 2)
    end = min(len(line_words), start + 8)
    start = max(0, end - 8)
    return line_words[start:end]


def _highlight_score(label: str, order: int) -> float:
    words = [_normalize_word(word) for word in label.split()]
    important_hits = sum(1 for word in words if word in IMPORTANT_TERMS)
    number_hits = len(re.findall(r"\d", label))
    uppercase_bonus = 1 if label[:1].isupper() else 0
    early_bonus = max(0, 8 - order) * 0.08
    return important_hits * 3 + number_hits * 1.2 + uppercase_bonus + early_bonus


def _is_meaningful_word(word: str) -> bool:
    normalized = _normalize_word(word)
    return len(normalized) >= 2 or bool(re.search(r"\d", word))


def _normalize_word(word: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", word.lower())


def _normalize_label(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


IMPORTANT_TERMS = {
    "action",
    "analytics",
    "audit",
    "benchmark",
    "campaign",
    "conversion",
    "cost",
    "cpc",
    "ctr",
    "engagement",
    "error",
    "fix",
    "gap",
    "growth",
    "improve",
    "issue",
    "keyword",
    "leads",
    "opportunity",
    "organic",
    "performance",
    "problem",
    "recommendation",
    "revenue",
    "risk",
    "score",
    "seo",
    "strategy",
    "traffic",
}
