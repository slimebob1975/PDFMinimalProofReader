from pathlib import Path

import fitz

from app.pdf_extractor import build_units, extract_lines


def create_two_column_pdf(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((70, 70), "Testbok", fontsize=20)
    page.insert_text((70, 120), "Kapitel 1", fontsize=14)
    page.insert_text((70, 220), "Detta är första versen.", fontsize=11)
    page.insert_text((70, 245), "2. Detta är andra versen.", fontsize=11)
    page.insert_text((320, 220), "3. Detta är tredje versen.", fontsize=11)
    page.insert_text((320, 245), "Den fortsätter här.", fontsize=11)
    doc.save(path)


def test_two_column_and_verse_extraction(tmp_path: Path):
    pdf = tmp_path / "sample.pdf"
    create_two_column_pdf(pdf)
    lines, diagnostics = extract_lines(pdf)
    units = build_units(lines, "Testbok")
    assert diagnostics["pages"] == 1
    assert [unit.verse for unit in units] == [1, 2, 3]
    assert units[0].verse_inferred is True
    assert "fortsätter" in units[2].text
