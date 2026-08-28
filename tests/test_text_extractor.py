from pathlib import Path

import pytest

from app.text_extractor import TextExtractionError, document_name_from_filename, extract_text_units


def test_document_name_comes_from_filename():
    assert document_name_from_filename(Path("lukas.txt")) == "Lukas"
    assert document_name_from_filename(Path("andra_kungaboken.txt")) == "Andra Kungaboken"


def test_extract_chapters_and_verses(tmp_path: Path):
    txt = tmp_path / "andra_kungaboken.txt"
    txt.write_text(
        "Kapitel: 1\n\n"
        "1. Första versen.\n\n"
        "2. Andra versen.\n"
        "fortsätter på nästa fysiska rad.\n\n"
        "Kapitel: 2\n\n"
        "1. Nytt kapitel.\n",
        encoding="utf-8",
    )

    units, diagnostics = extract_text_units(txt)

    assert diagnostics["source_type"] == "txt"
    assert diagnostics["chapter_count"] == 2
    assert [unit.chapter for unit in units] == [1, 1, 2]
    assert [unit.verse for unit in units] == [1, 2, 1]
    assert units[0].document == "Andra Kungaboken"
    assert units[0].reference == "Andra Kungaboken 1:1"
    assert units[1].text == "Andra versen. fortsätter på nästa fysiska rad."
    assert units[1].line_start == 5
    assert units[1].line_end == 6
    assert all(unit.verse_inferred is False for unit in units)


def test_rejects_verse_before_chapter(tmp_path: Path):
    txt = tmp_path / "lukas.txt"
    txt.write_text("1. Fel ordning.\n", encoding="utf-8")

    with pytest.raises(TextExtractionError, match="Vers före första kapitelmarkören"):
        extract_text_units(txt)
