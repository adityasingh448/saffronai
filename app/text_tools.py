from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
import csv
import io
import json
import re
import zipfile
import xml.etree.ElementTree as ET

from app.pdf_tools import clean_text


@dataclass
class TextSection:
    title: str
    body: str
    lines: list[str]


@dataclass
class TextDocument:
    title: str
    text: str
    sections: list[TextSection]
    source_name: str
    extension: str


def extract_text_document(path: Path, source_name: str = "") -> TextDocument:
    extension = path.suffix.lower()
    source_name = source_name or path.name

    if extension == ".docx":
        raw_text = _read_docx(path)
    else:
        raw_text = _read_text_like_file(path, extension)

    normalized = _normalize_source_text(raw_text)
    if len(re.findall(r"[A-Za-z0-9]+", normalized)) < 12:
        raise ValueError("The uploaded file does not contain enough readable text.")

    sections = _build_sections(normalized)
    title = _document_title(source_name, sections, normalized)
    return TextDocument(title=title, text=normalized, sections=sections, source_name=source_name, extension=extension)


def is_pdf_filename(filename: str) -> bool:
    return Path(filename or "").suffix.lower() == ".pdf"


def source_storage_name(filename: str) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix == ".pdf":
        return "input.pdf"
    safe_suffix = re.sub(r"[^a-z0-9.]+", "", suffix)[:16] or ".txt"
    return f"input{safe_suffix}"


def _read_text_like_file(path: Path, extension: str) -> str:
    data = path.read_bytes()
    decoded = _decode_bytes(data)

    if extension in {".html", ".htm"}:
        return _strip_html(decoded)
    if extension == ".json":
        return _format_json(decoded)
    if extension in {".csv", ".tsv"}:
        return _format_delimited(decoded, delimiter="\t" if extension == ".tsv" else ",")
    if extension == ".rtf":
        return _strip_rtf(decoded)
    if extension in {".md", ".markdown"}:
        return _strip_markdown(decoded)

    return decoded


def _decode_bytes(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            decoded = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        decoded = data.decode("utf-8", errors="ignore")

    decoded = decoded.replace("\x00", " ")
    printable = sum(1 for char in decoded if char.isprintable() or char in "\r\n\t")
    if decoded and printable / max(1, len(decoded)) < 0.72:
        raise ValueError("This file looks binary. Please upload a readable text, markdown, document, or PDF file.")
    return decoded


def _read_docx(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        try:
            xml_data = archive.read("word/document.xml")
        except KeyError as exc:
            raise ValueError("The DOCX file could not be read.") from exc

    root = ET.fromstring(xml_data)
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs = []
    for paragraph in root.findall(".//w:p", namespace):
        parts = [node.text or "" for node in paragraph.findall(".//w:t", namespace)]
        line = clean_text("".join(parts))
        if line:
            paragraphs.append(line)
    return "\n".join(paragraphs)


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"br", "p", "div", "section", "article", "li", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data)

    def text(self) -> str:
        return " ".join(self.parts)


def _strip_html(text: str) -> str:
    parser = _HTMLTextExtractor()
    parser.feed(text)
    return parser.text()


def _format_json(text: str) -> str:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _format_delimited(text: str, delimiter: str) -> str:
    sample = io.StringIO(text)
    rows = []
    for row in csv.reader(sample, delimiter=delimiter):
        cleaned = [clean_text(cell) for cell in row if clean_text(cell)]
        if cleaned:
            rows.append(" | ".join(cleaned))
    return "\n".join(rows)


def _strip_rtf(text: str) -> str:
    text = re.sub(r"\\'[0-9a-fA-F]{2}", " ", text)
    text = re.sub(r"\\[a-zA-Z]+\d* ?", " ", text)
    text = text.replace("{", " ").replace("}", " ")
    return text


def _strip_markdown(text: str) -> str:
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s{0,3}>\s?", "", text, flags=re.MULTILINE)
    text = re.sub(r"[*_~]{1,3}", "", text)
    return text


def _normalize_source_text(text: str) -> str:
    lines = []
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = clean_text(raw_line)
        line = re.sub(r"^[\-*•–—]\s+", "", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _build_sections(text: str) -> list[TextSection]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    sections: list[TextSection] = []
    current_title = ""
    current_lines: list[str] = []

    for line in lines:
        if _looks_like_heading(line):
            if current_lines:
                sections.append(_section(current_title, current_lines))
                current_lines = []
            current_title = line.strip(" :-")
            continue
        current_lines.append(line)

    if current_lines:
        sections.append(_section(current_title, current_lines))

    if not sections:
        chunks = _paragraph_chunks(text)
        sections = [_section(f"Section {index + 1}", chunk) for index, chunk in enumerate(chunks)]

    return sections[:18]


def _section(title: str, lines: list[str]) -> TextSection:
    body = "\n".join(lines)
    title = clean_text(title) or _fallback_section_title(lines)
    return TextSection(title=title, body=body, lines=lines)


def _looks_like_heading(line: str) -> bool:
    cleaned = clean_text(line).strip(" :-")
    words = cleaned.split()
    if len(words) < 2 or len(words) > 12:
        return False
    if cleaned.endswith("."):
        return False
    if re.search(r"\d{2,}[/.-]\d{1,2}[/.-]\d{1,4}", cleaned):
        return False
    uppercase_ratio = sum(1 for char in cleaned if char.isupper()) / max(1, sum(1 for char in cleaned if char.isalpha()))
    return uppercase_ratio > 0.46 or bool(re.search(r"\b(overview|summary|score|seo|aeo|geo|recommendation|finding|analysis|audit|strategy)\b", cleaned, flags=re.IGNORECASE))


def _paragraph_chunks(text: str) -> list[list[str]]:
    sentences = re.split(r"(?<=[.!?])\s+", clean_text(text))
    chunks: list[list[str]] = []
    current: list[str] = []
    for sentence in sentences:
        if not sentence:
            continue
        current.append(sentence)
        if sum(len(item.split()) for item in current) >= 95:
            chunks.append(current)
            current = []
    if current:
        chunks.append(current)
    return chunks or [[clean_text(text)]]


def _fallback_section_title(lines: list[str]) -> str:
    if not lines:
        return "Report notes"
    words = lines[0].split()
    return " ".join(words[:8]).strip(" .:-") or "Report notes"


def _document_title(source_name: str, sections: list[TextSection], text: str) -> str:
    for section in sections:
        title = clean_text(section.title)
        if title and not title.lower().startswith("section "):
            return title[:90]
    stem = Path(source_name or "").stem.replace("_", " ").replace("-", " ").strip()
    if stem:
        return clean_text(stem).title()[:90]
    return clean_text(text).split(".")[0][:90] or "Report notes"
