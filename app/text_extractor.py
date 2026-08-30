from __future__ import annotations

import re
from pathlib import Path

from .models import TextUnit

CHAPTER_RE = re.compile(r"^Kapitel:\s*(\d+)\s*$", re.IGNORECASE)
VERSE_RE = re.compile(r"^(\d{1,3})\.\s*(.*)$")


class TextExtractionError(ValueError):
    pass


def document_name_from_filename(path: Path) -> str:
    """Build the document name from a TXT filename.

    Examples:
        lukas.txt -> Lukas
        andra_kungaboken.txt -> Andra Kungaboken
    """
    stem = path.stem.strip()
    normalized = re.sub(r"[_-]+", " ", stem)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return "Dokument"
    return normalized.title()


def extract_text_units(txt_path: Path) -> tuple[list[TextUnit], dict]:
    """Parse the supported chapter/verse TXT format into ordinary TextUnit objects.

    Expected structure:
        Kapitel: 1

        1. First verse text.
        2. Second verse text.

    Blank lines are ignored. A non-empty line without a new verse number is treated
    as a continuation of the current verse, which makes wrapped text files safe to use.
    """
    try:
        raw_text = txt_path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise TextExtractionError(
            "Textfilen måste vara UTF-8-kodad (UTF-8 med BOM stöds också)."
        ) from exc
    except OSError as exc:
        raise TextExtractionError(f"Textfilen kunde inte läsas: {exc}") from exc

    document = document_name_from_filename(txt_path)
    physical_lines = raw_text.splitlines()
    document_title_from_content: str | None = None
    units: list[TextUnit] = []

    chapter: int | None = None
    current_verse: int | None = None
    current_parts: list[str] = []
    current_source_lines: list[str] = []
    current_line_start: int | None = None
    current_line_end: int | None = None

    def flush() -> None:
        nonlocal current_verse, current_parts, current_source_lines
        nonlocal current_line_start, current_line_end

        if current_verse is None:
            return

        text = re.sub(r"\s+", " ", " ".join(current_parts)).strip()
        if not text:
            raise TextExtractionError(
                f"Tom verstext i {document} {chapter}:{current_verse}."
            )

        unit_number = len(units) + 1
        units.append(
            TextUnit(
                unit_id=f"unit_{unit_number:05d}",
                document=document,
                chapter=chapter,
                verse=current_verse,
                verse_inferred=False,
                reference=f"{document} {chapter}:{current_verse}",
                # TXT has no page/column geometry. Keep the common TextUnit contract
                # so the remainder of the proofreading pipeline stays unchanged.
                page_start=1,
                page_end=1,
                printed_page_start=None,
                column_start="text",
                line_start=current_line_start or 1,
                line_end=current_line_end or current_line_start or 1,
                text=text,
                source_lines=list(current_source_lines),
            )
        )

        current_verse = None
        current_parts = []
        current_source_lines = []
        current_line_start = None
        current_line_end = None

    for line_no, raw_line in enumerate(physical_lines, start=1):
        line = raw_line.strip()
        if not line:
            continue

        chapter_match = CHAPTER_RE.fullmatch(line)
        if chapter_match:
            flush()
            new_chapter = int(chapter_match.group(1))
            if new_chapter < 1:
                raise TextExtractionError(f"Ogiltigt kapitelnummer på rad {line_no}: {line}")
            chapter = new_chapter
            continue

        verse_match = VERSE_RE.match(line)
        if verse_match:
            if chapter is None:
                raise TextExtractionError(
                    f"Vers före första kapitelmarkören på rad {line_no}: {line}"
                )
            flush()
            current_verse = int(verse_match.group(1))
            current_parts = [verse_match.group(2).strip()]
            current_source_lines = [raw_line]
            current_line_start = line_no
            current_line_end = line_no
            continue

        if chapter is None:
            # Newer TXT exports may contain one document-title line before the
            # first chapter marker. Use it as the document name. Older files
            # start directly with "Kapitel: N" and keep using the filename.
            if document_title_from_content is None:
                document_title_from_content = line
                document = line
                continue
            raise TextExtractionError(
                f"Oväntad text före första 'Kapitel: N' på rad {line_no}: {line}"
            )
        if current_verse is None:
            raise TextExtractionError(
                f"Oväntad text före första versen i kapitel {chapter} på rad {line_no}: {line}"
            )

        current_parts.append(line)
        current_source_lines.append(raw_line)
        current_line_end = line_no

    flush()

    if not units:
        raise TextExtractionError("Ingen granskningsbar kapitel-/versstruktur hittades i textfilen.")

    diagnostics = {
        "source_type": "txt",
        "line_count": len(physical_lines),
        "nonempty_line_count": sum(1 for line in physical_lines if line.strip()),
        "chapter_count": len({unit.chapter for unit in units if unit.chapter is not None}),
        "unit_count": len(units),
        "document": document,
        "document_title_source": "content" if document_title_from_content else "filename",
    }
    return units, diagnostics
