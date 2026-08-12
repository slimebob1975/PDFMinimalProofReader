from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

import fitz

from .models import PdfLine, TextUnit

VERSE_RE = re.compile(r"^(\d{1,3})\.\s*(.*)$")
CHAPTER_RE = re.compile(
    r"^(?:(?:Kapitel|Psalm)\s+(?P<num_after>\d+)|(?P<num_before>\d+)\s+Kapitlet)\s*$",
    re.IGNORECASE,
)
PAGE_NUMBER_RE = re.compile(r"^\d{1,5}$")


class PdfExtractionError(ValueError):
    pass


def _normalize_line(text: str) -> str:
    text = text.replace("\u00ad", "-").replace("\ufffe", "").replace("\ufffd", "")
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^([A-ZÅÄÖ])\s+([a-zåäö]{2,})", r"\1\2", text)
    return text


def _group_page_lines(page: fitz.Page) -> list[dict]:
    grouped: dict[tuple[int, int], list[tuple]] = {}
    for word in page.get_text("words", sort=False):
        grouped.setdefault((int(word[5]), int(word[6])), []).append(word)
    result: list[dict] = []
    for words in grouped.values():
        words.sort(key=lambda item: item[0])
        text = _normalize_line(" ".join(str(item[4]) for item in words))
        if text:
            result.append(
                {
                    "x0": min(item[0] for item in words),
                    "y0": min(item[1] for item in words),
                    "x1": max(item[2] for item in words),
                    "y1": max(item[3] for item in words),
                    "text": text,
                }
            )
    return result


def inspect_pdf(pdf_path: Path, max_pages: int) -> dict:
    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:
        raise PdfExtractionError(f"Filen kunde inte öppnas som PDF: {exc}") from exc
    if doc.needs_pass:
        raise PdfExtractionError("Lösenordsskyddade PDF-filer stöds inte.")
    if len(doc) == 0:
        raise PdfExtractionError("PDF-filen saknar sidor.")
    if len(doc) > max_pages:
        raise PdfExtractionError(f"PDF-filen har {len(doc)} sidor; gränsen är {max_pages}.")
    page_stats = []
    for page_number, page in enumerate(doc, 1):
        text = page.get_text("text").strip()
        page_stats.append({
            "page": page_number,
            "text_characters": len(text),
            "image_count": len(page.get_images(full=True)),
        })
    total_chars = sum(item["text_characters"] for item in page_stats)
    low_text_pages = [item["page"] for item in page_stats if item["text_characters"] < 25]
    if total_chars < max(50, len(doc) * 25) or len(low_text_pages) > max(1, len(doc) // 3):
        raise PdfExtractionError(
            "PDF-filen verkar sakna ett tillräckligt textlager. Skannade filer/OCR stöds inte."
        )
    return {"pages": len(doc), "total_text_characters": total_chars, "page_stats": page_stats}


def extract_lines(pdf_path: Path, max_pages: int = 500) -> tuple[list[PdfLine], dict]:
    diagnostics = inspect_pdf(pdf_path, max_pages=max_pages)
    doc = fitz.open(pdf_path)
    lines: list[PdfLine] = []

    for page_index, page in enumerate(doc, start=1):
        raw = _group_page_lines(page)
        width, height = page.rect.width, page.rect.height
        midpoint = width / 2
        printed_page = next(
            (
                item["text"]
                for item in raw
                if item["y0"] < height * 0.11 and PAGE_NUMBER_RE.fullmatch(item["text"])
            ),
            None,
        )
        usable: list[dict] = []
        for item in raw:
            text = item["text"]
            if item["y0"] < height * 0.11 and PAGE_NUMBER_RE.fullmatch(text):
                continue
            if item["y0"] > height * 0.972:
                continue
            center = (item["x0"] + item["x1"]) / 2
            column = "vänster" if center < midpoint else "höger"
            usable.append({**item, "column": column})

        for column in ("vänster", "höger"):
            column_lines = [item for item in usable if item["column"] == column]
            column_lines.sort(key=lambda item: (round(item["y0"], 1), item["x0"]))
            for line_no, item in enumerate(column_lines, start=1):
                kind = "body"
                if CHAPTER_RE.fullmatch(item["text"]):
                    kind = "chapter"
                elif (
                    item["y0"] < height * 0.20
                    and re.fullmatch(r"[A-ZÅÄÖ][A-Za-zÅÄÖåäö-]{2,40}", item["text"])
                    and item["x0"] < midpoint < item["x1"] + 80
                ):
                    kind = "running_title"
                elif item["y0"] < height * 0.24 and len(item["text"]) < 110:
                    kind = "heading_or_intro"
                lines.append(
                    PdfLine(
                        page=page_index,
                        printed_page=printed_page,
                        column=column,
                        line_no=line_no,
                        x0=item["x0"],
                        y0=item["y0"],
                        x1=item["x1"],
                        y1=item["y1"],
                        text=item["text"],
                        kind=kind,
                    )
                )
    diagnostics["line_count"] = len(lines)
    return lines, diagnostics


def detect_document_name(lines: Iterable[PdfLine], pdf_path: Path) -> str:
    all_lines = list(lines)
    if not all_lines:
        return pdf_path.stem

    first_page = min(line.page for line in all_lines)
    candidates = [
        line
        for line in all_lines
        if line.page == first_page
        and line.kind in {"heading_or_intro", "running_title"}
        and re.fullmatch(r"[A-ZÅÄÖ][A-Za-zÅÄÖåäö -]{2,60}", line.text)
    ]

    if not candidates:
        return pdf_path.stem

    candidates.sort(key=lambda line: (line.y0, line.x0))
    return candidates[0].text


def _append(parts: list[str], text: str) -> None:
    if not text:
        return
    if (
        parts
        and re.fullmatch(r"[A-ZÅÄÖ]", parts[-1])
        and parts[-1] not in {"I"}
        and re.match(r"^[a-zåäö]", text)
    ):
        parts[-1] = parts[-1] + text
    elif parts and parts[-1].endswith("-") and re.match(r"^[a-zåäö]", text):
        parts[-1] = parts[-1][:-1] + text
    else:
        parts.append(text)


def build_units(lines: list[PdfLine], document: str) -> list[TextUnit]:
    units: list[TextUnit] = []
    chapter: int | None = None
    current: dict | None = None
    waiting_for_first_verse = False
    fallback_counter = 0

    def flush() -> None:
        nonlocal current, fallback_counter
        if not current:
            return
        text = re.sub(r"\s+([,.;:!?])", r"\1", " ".join(current["parts"]))
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            fallback_counter += 1
            reference = (
                f"{document} {current['chapter']}:{current['verse']}"
                if current["chapter"] is not None and current["verse"] is not None
                else f"{document}, sida {current['page_start']}, rad {current['line_start']}"
            )
            units.append(
                TextUnit(
                    unit_id=f"unit_{fallback_counter:05d}",
                    document=document,
                    chapter=current["chapter"],
                    verse=current["verse"],
                    verse_inferred=current["inferred"],
                    reference=reference,
                    page_start=current["page_start"],
                    page_end=current["page_end"],
                    printed_page_start=current["printed_page_start"],
                    column_start=current["column_start"],
                    line_start=current["line_start"],
                    line_end=current["line_end"],
                    text=text,
                    source_lines=current["source_lines"],
                )
            )
        current = None

    for line in lines:
        if line.kind == "running_title":
            continue
        chapter_match = CHAPTER_RE.fullmatch(line.text)
        if chapter_match:
            flush()
            chapter = int(chapter_match.group("num_after") or chapter_match.group("num_before"))
            waiting_for_first_verse = True
            continue

        verse_match = VERSE_RE.match(line.text)
        if chapter is not None and verse_match:
            flush()
            current = {
                "chapter": chapter,
                "verse": int(verse_match.group(1)),
                "inferred": False,
                "page_start": line.page,
                "page_end": line.page,
                "printed_page_start": line.printed_page,
                "column_start": line.column,
                "line_start": line.line_no,
                "line_end": line.line_no,
                "parts": [],
                "source_lines": [line.text],
            }
            _append(current["parts"], verse_match.group(2))
            waiting_for_first_verse = False
            continue

        if chapter is not None and waiting_for_first_verse:
            if line.kind == "heading_or_intro" and len(line.text) > 1:
                continue
            current = {
                "chapter": chapter,
                "verse": 1,
                "inferred": True,
                "page_start": line.page,
                "page_end": line.page,
                "printed_page_start": line.printed_page,
                "column_start": line.column,
                "line_start": line.line_no,
                "line_end": line.line_no,
                "parts": [],
                "source_lines": [line.text],
            }
            _append(current["parts"], line.text)
            waiting_for_first_verse = False
            continue

        if current:
            current["page_end"] = line.page
            current["line_end"] = line.line_no
            current["source_lines"].append(line.text)
            _append(current["parts"], line.text)
        elif chapter is None and line.kind not in {"heading_or_intro", "chapter"}:
            current = {
                "chapter": None,
                "verse": None,
                "inferred": False,
                "page_start": line.page,
                "page_end": line.page,
                "printed_page_start": line.printed_page,
                "column_start": line.column,
                "line_start": line.line_no,
                "line_end": line.line_no,
                "parts": [line.text],
                "source_lines": [line.text],
            }
    flush()
    return units