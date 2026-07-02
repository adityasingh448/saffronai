from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any

import requests

from app.config import settings
from app.script_writer import PageScript, WalkthroughScript, clean_script_text
from app.script_writer import _spoken_text  # Reuse the report-specific pronunciation fixes.
from app.text_tools import TextDocument, TextSection


@dataclass
class TextPoint:
    title: str
    detail: str
    section_title: str
    score: float


def write_text_walkthrough_script(
    document: TextDocument,
    prospect_name: str,
    target_company: str,
    language: str,
    max_minutes: int,
) -> WalkthroughScript:
    language = "English"
    if settings.openai_api_key:
        try:
            return _write_with_openai(document, prospect_name, target_company, language, max_minutes)
        except Exception as exc:
            print(f"OpenAI text script generation failed, falling back locally: {exc}")

    return _write_locally(document, prospect_name, target_company, language, max_minutes)


def _write_with_openai(
    document: TextDocument,
    prospect_name: str,
    target_company: str,
    language: str,
    max_minutes: int,
) -> WalkthroughScript:
    target_words = _target_words(max_minutes)
    payload = {
        "source_title": document.title,
        "prospect_name": prospect_name or "there",
        "target_company": target_company or "your business",
        "target_minutes": max_minutes,
        "target_words": target_words,
        "source_text": document.text[:18000],
        "writing_rules": [
            "Write a natural voiceover that starts exactly with: Hi there,",
            "Use the uploaded text as the only source. Do not invent facts or metrics.",
            "Explain point by point, not page by page.",
            "Never say Point 1, 1.1, page number, section number, pre point, or inch point.",
            "Use First point for the first idea, then Next point for the remaining ideas.",
            "Do not use the word formed by c-l-i-e-n-t anywhere.",
            "Expand short forms in narration, especially SEO, AEO, GEO, SERP, CTR, CPC, OG, AI, and JSON-LD.",
            "Sound like a clear consultant explaining a report to the viewer in a realistic conversational tone.",
            "Keep every point grounded in a distinct line, finding, metric, recommendation, or idea from the uploaded text.",
        ],
        "output_contract": {
            "title": "short title",
            "points": [
                {
                    "focus": "short on-screen label copied or closely adapted from the source text",
                    "detail": "one source-grounded explanation",
                    "narration": "spoken paragraph for this point",
                    "highlight_terms": "2-4 short labels for camera movement",
                }
            ],
        },
    }
    response = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/json"},
        json={
            "model": settings.openai_model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You create concise, human report walkthrough scripts. "
                        "Everything must be grounded in the supplied source text."
                    ),
                },
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "temperature": 0.42,
        },
        timeout=90,
    )
    response.raise_for_status()
    parsed = _parse_json_response(response.json()["choices"][0]["message"]["content"])
    points = parsed.get("points") or []
    scripts = []
    for index, item in enumerate(points[:10], start=1):
        focus = _clean_visible_text(str(item.get("focus") or item.get("detail") or f"Key point {index}"))
        narration = _clean_narration(str(item.get("narration") or item.get("detail") or focus))
        labels = [focus] + [str(label) for label in item.get("highlight_terms", []) if str(label).strip()]
        scripts.append(
            PageScript(
                page=index,
                narration=narration,
                focus=focus,
                highlight_terms=_dedupe_visible(labels)[:4],
            )
        )

    if not scripts:
        raise ValueError("OpenAI returned no text points.")

    scripts[0].narration = _force_hi_there(scripts[0].narration)
    full_script = "\n\n".join(script.narration for script in scripts)
    return WalkthroughScript(
        title=_clean_visible_text(str(parsed.get("title") or document.title or _default_title(target_company))),
        full_script=full_script,
        page_scripts=scripts,
        source="openai-text",
    )


def _write_locally(
    document: TextDocument,
    prospect_name: str,
    target_company: str,
    language: str,
    max_minutes: int,
) -> WalkthroughScript:
    points = _select_points(document, max_minutes)
    company = _clean_visible_text(target_company) or "your business"
    title = _clean_visible_text(document.title or _default_title(company))
    scripts: list[PageScript] = []

    for index, point in enumerate(points, start=1):
        transition = "First point" if index == 1 else "Next point"
        focus = _clean_visible_text(point.title)
        detail = _clean_visible_text(point.detail)
        narration = _build_point_narration(
            transition=transition,
            focus=focus,
            detail=detail,
            company=company,
            is_first=index == 1,
            is_last=index == len(points),
        )
        scripts.append(
            PageScript(
                page=index,
                narration=narration,
                focus=focus,
                highlight_terms=_highlight_terms_for_point(point),
            )
        )

    if not scripts:
        raise ValueError("The uploaded text did not contain enough usable points.")

    scripts[0].narration = _force_hi_there(scripts[0].narration)
    full_script = "\n\n".join(script.narration for script in scripts)
    return WalkthroughScript(title=title, full_script=full_script, page_scripts=scripts, source="local-text")


def _select_points(document: TextDocument, max_minutes: int) -> list[TextPoint]:
    candidates: list[TextPoint] = []
    for section_index, section in enumerate(document.sections):
        candidates.extend(_section_points(section, section_index))

    if len(candidates) < 4:
        candidates.extend(_fallback_sentence_points(document.text))

    deduped: list[TextPoint] = []
    seen: set[str] = set()
    for candidate in sorted(candidates, key=lambda item: item.score, reverse=True):
        key = _point_key(candidate.title)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)

    target_count = max(4, min(9, int(max_minutes or 3) * 2 + 1))
    selected = deduped[:target_count]
    selected.sort(key=lambda item: _source_order(document, item))
    return selected or deduped[:4]


def _section_points(section: TextSection, section_index: int) -> list[TextPoint]:
    points: list[TextPoint] = []
    section_title = _clean_visible_text(section.title)

    for line_index, line in enumerate(section.lines[:80]):
        cleaned = _clean_visible_text(line)
        if not _is_useful_line(cleaned):
            continue
        title = _point_title(cleaned, section_title)
        detail = _best_detail([section.lines[line_index]], title) or cleaned
        score = _point_score(cleaned, line_index, section_title, section_index)
        points.append(TextPoint(title=title, detail=detail, section_title=section_title, score=score))

    if not points and _is_useful_line(section_title):
        points.append(
            TextPoint(
                title=section_title,
                detail=_best_detail(section.lines[:2], section_title) or section_title,
                section_title=section_title,
                score=56 - section_index * 0.9,
            )
        )

    return points


def _fallback_sentence_points(text: str) -> list[TextPoint]:
    points = []
    for index, sentence in enumerate(re.split(r"(?<=[.!?])\s+", clean_script_text(text))[:80]):
        sentence = _clean_visible_text(sentence)
        if _is_useful_line(sentence):
            points.append(TextPoint(title=_point_title(sentence, ""), detail=sentence, section_title="", score=_point_score(sentence, index, "", 0)))
    return points


def _point_title(text: str, section_title: str) -> str:
    cleaned = _clean_visible_text(text)
    if ":" in cleaned and len(cleaned.split(":")[0].split()) <= 8:
        return cleaned.split(":", 1)[0].strip()
    if " - " in cleaned and len(cleaned.split(" - ")[0].split()) <= 8:
        return cleaned.split(" - ", 1)[0].strip()
    words = cleaned.split()
    important = _important_phrase(cleaned)
    if important:
        return important
    if len(words) <= 9:
        return cleaned
    if _is_specific_heading(section_title):
        return section_title
    return " ".join(words[:9]).strip(" .:-")


def _is_specific_heading(text: str) -> bool:
    cleaned = _clean_visible_text(text)
    words = cleaned.split()
    if not 2 <= len(words) <= 8:
        return False
    lower = cleaned.lower()
    if any(term in lower for term in ("audit", "overview", "summary", "report")) and len(words) > 3:
        return False
    return True


def _important_phrase(text: str) -> str:
    for phrase in ("On-Page SEO", "Off-Page SEO"):
        if re.search(re.escape(phrase), text, flags=re.IGNORECASE):
            return phrase
    acronym_match = re.search(r"\b(SEO|AEO|GEO)\b(?:\s+([A-Za-z]+))?", text)
    if acronym_match:
        suffix = acronym_match.group(2) or ""
        if suffix.lower() in {"readiness", "opportunity", "score", "audit", "strategy"}:
            return f"{acronym_match.group(1)} {suffix}".strip()
        return acronym_match.group(1)

    phrases = (
        "Answer Engine Optimization",
        "Generative Engine Optimization",
        "Search Engine Optimization",
        "AI Overview",
        "Knowledge Graph",
        "structured data",
        "schema markup",
        "internal links",
        "Open Graph",
        "conversion rate",
        "organic traffic",
        "page speed",
        "content strategy",
    )
    for phrase in phrases:
        if re.search(re.escape(phrase), text, flags=re.IGNORECASE):
            return phrase
    return ""


def _best_detail(lines: list[str], title: str) -> str:
    cleaned_lines = [_clean_visible_text(line) for line in lines if _is_useful_line(_clean_visible_text(line))]
    if not cleaned_lines:
        return title

    title_key = _point_key(title)
    useful = []
    for line in cleaned_lines:
        if _point_key(line) == title_key and len(cleaned_lines) > 1:
            continue
        useful.append(line)
        if len(" ".join(useful).split()) >= 28:
            break
    return _join_detail(useful) or cleaned_lines[0]


def _join_detail(lines: list[str]) -> str:
    sentences = []
    for line in lines:
        line = _clean_visible_text(line)
        if not line:
            continue
        if not re.search(r"[.!?]$", line):
            line += "."
        sentences.append(line)
    return " ".join(sentences)


def _build_point_narration(
    transition: str,
    focus: str,
    detail: str,
    company: str,
    is_first: bool,
    is_last: bool,
) -> str:
    spoken_focus = _spoken_text(focus)
    spoken_detail = _spoken_text(_remove_duplicate_intro(detail, focus))
    meaning = _meaning_sentence(focus, detail)
    action = _action_sentence(focus, detail)
    prefix = f"Hi there, {transition.lower()}: " if is_first else f"{transition}: "
    ending = " That gives you a clear place to start, then measure the improvement after the change." if is_last else ""

    if spoken_detail and _point_key(spoken_detail) != _point_key(spoken_focus):
        narration = f"{prefix}{spoken_focus}. Here, the report is saying that {spoken_detail}. {meaning} {action}{ending}"
    else:
        narration = f"{prefix}{spoken_focus}. {meaning} {action}{ending}"
    return _clean_narration(narration.replace("your business", company if company != "your business" else "your business"))


def _meaning_sentence(focus: str, detail: str) -> str:
    lower = f"{focus} {detail}".lower()
    if any(term in lower for term in ("seo", "search", "organic", "keyword", "ranking")):
        return "In practical terms, this affects how easily the right people can discover you before they compare alternatives."
    if any(term in lower for term in ("aeo", "answer engine", "ai overview", "geo", "generative engine", "knowledge graph", "entity")):
        return "This matters because answer engines and artificial intelligence summaries need clear entity signals before they trust the page."
    if any(term in lower for term in ("schema", "structured data", "json", "microdata")):
        return "The meaning is simple: the page may be understandable to humans, but machines need stronger structure."
    if any(term in lower for term in ("conversion", "lead", "form", "cta", "enquiry", "demo")):
        return "This connects directly to action, because small clarity gaps can reduce qualified enquiries."
    if any(term in lower for term in ("speed", "technical", "crawl", "index", "mobile", "error")):
        return "This is technical friction, and friction quietly affects confidence, discovery, and momentum."
    if any(term in lower for term in ("score", "audit", "benchmark", "gap", "risk")):
        return "Treat this as a priority signal, because it turns a broad report into a ranked decision."
    return "The practical meaning is that this point should become a decision, not just a note inside the source file."


def _action_sentence(focus: str, detail: str) -> str:
    lower = f"{focus} {detail}".lower()
    if any(term in lower for term in ("seo", "search", "organic", "keyword", "ranking")):
        return "Start with the page or keyword that has the clearest upside, fix that first, and track movement weekly."
    if any(term in lower for term in ("aeo", "answer engine", "ai overview", "geo", "generative engine", "knowledge graph", "entity")):
        return "The next move is to make the brand, service, and proof points consistent across the places search systems read."
    if any(term in lower for term in ("schema", "structured data", "json", "microdata")):
        return "Add the right schema, validate it, and then re-check whether search results understand the page more clearly."
    if any(term in lower for term in ("conversion", "lead", "form", "cta", "enquiry", "demo")):
        return "Tighten the message, reduce the next-step friction, and measure whether more qualified enquiries come through."
    if any(term in lower for term in ("speed", "technical", "crawl", "index", "mobile", "error")):
        return "Fix the highest-impact technical item first, then compare the same metric again so progress is visible."
    return "Choose one owner, define the first fix, and decide which metric will prove the change worked."


def _highlight_terms_for_point(point: TextPoint) -> list[str]:
    labels = [point.title, point.detail, point.section_title]
    return _dedupe_visible(labels)[:4]


def _is_useful_line(text: str) -> bool:
    cleaned = _clean_visible_text(text)
    if len(cleaned) < 8:
        return False
    if re.search(r"https?://|www\.|@", cleaned, flags=re.IGNORECASE):
        return False
    if re.fullmatch(r"[\W\d_]+", cleaned):
        return False
    if sum(1 for char in cleaned if char.isdigit()) > max(6, len(cleaned) * 0.48):
        return False
    return len(re.findall(r"[A-Za-z]", cleaned)) >= 6


IMPORTANT_TERMS = {
    "aeo",
    "ai",
    "analysis",
    "answer",
    "audit",
    "brand",
    "campaign",
    "conversion",
    "crawl",
    "entity",
    "geo",
    "growth",
    "keyword",
    "lead",
    "metric",
    "opportunity",
    "organic",
    "performance",
    "recommendation",
    "risk",
    "schema",
    "score",
    "search",
    "seo",
    "serp",
    "strategy",
    "traffic",
}


def _point_score(text: str, line_index: int, section_title: str, section_index: int) -> float:
    lower = text.lower()
    words = text.split()
    score = 20.0
    if 4 <= len(words) <= 22:
        score += 13.0
    if re.search(r"\d|%|\$|/100|score|rating", lower):
        score += 11.0
    tokens = set(re.findall(r"[a-z0-9]+", lower))
    score += sum(5.5 for term in IMPORTANT_TERMS if term in tokens)
    if section_title and _point_key(text) == _point_key(section_title):
        score += 14.0
    score += max(0, 8 - line_index * 0.55)
    score -= section_index * 0.45
    return score


def _source_order(document: TextDocument, point: TextPoint) -> int:
    needle = _point_key(point.title)
    for index, line in enumerate(document.text.splitlines()):
        if needle and needle in _point_key(line):
            return index
    return 9999


def _remove_duplicate_intro(detail: str, focus: str) -> str:
    detail = _clean_visible_text(detail)
    focus = _clean_visible_text(focus)
    if detail.lower().startswith(focus.lower()):
        detail = detail[len(focus) :].strip(" .:-")
    return detail


def _force_hi_there(text: str) -> str:
    cleaned = _clean_narration(text)
    cleaned = re.sub(r"^(?:first point|next point)\s*:\s*", "", cleaned, flags=re.IGNORECASE)
    if cleaned.lower().startswith("hi there,"):
        return cleaned
    if cleaned.lower().startswith("hi there"):
        return "Hi there," + cleaned[8:]
    return f"Hi there, {cleaned}"


def _clean_narration(text: str) -> str:
    cleaned = clean_script_text(text)
    cleaned = re.sub(r"\bpre\s+point\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\binch\s+point\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bpoint\s+\d+\b", "point", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bpage\s+\d+\b", "section", cleaned, flags=re.IGNORECASE)
    cleaned = _remove_banned_word(cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _clean_visible_text(text: str) -> str:
    cleaned = clean_script_text(text)
    cleaned = re.sub(r"^[\-*•–—\d.)\s]+", "", cleaned).strip(" \"'`")
    cleaned = _remove_banned_word(cleaned)
    words = cleaned.split()
    if len(words) > 28:
        cleaned = " ".join(words[:28]) + "..."
    return cleaned.strip(" .:-")


def _remove_banned_word(text: str) -> str:
    return re.sub(r"\bclient\b", "viewer", text or "", flags=re.IGNORECASE)


def _dedupe_visible(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        cleaned = _clean_visible_text(item)
        key = _point_key(cleaned)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def _point_key(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (text or "").lower())[:16])


def _target_words(max_minutes: int) -> int:
    return max(1, min(int(max_minutes or 1), 6)) * 95


def _default_title(company: str) -> str:
    company = _clean_visible_text(company) or "Report"
    return f"{company} walkthrough"


def _parse_json_response(content: str) -> dict[str, Any]:
    content = content.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", content, flags=re.DOTALL | re.IGNORECASE)
    if fence:
        content = fence.group(1).strip()
    return json.loads(content)
