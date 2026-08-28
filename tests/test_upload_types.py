from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.datastructures import UploadFile

from app.main import _store_upload


def _upload(filename: str, content: bytes = b"test") -> UploadFile:
    import io
    return UploadFile(filename=filename, file=io.BytesIO(content))


def test_store_upload_accepts_pdf_and_txt(tmp_path: Path):
    pdf_path, pdf_name = _store_upload(_upload("a.pdf"), tmp_path / "a.pdf", 1024)
    txt_path, txt_name = _store_upload(_upload("b.txt"), tmp_path / "b.txt", 1024)

    assert pdf_path.exists() and pdf_name == "a.pdf"
    assert txt_path.exists() and txt_name == "b.txt"


def test_store_upload_rejects_other_extensions(tmp_path: Path):
    with pytest.raises(HTTPException) as exc_info:
        _store_upload(_upload("c.docx"), tmp_path / "c.docx", 1024)

    assert exc_info.value.status_code == 400
    assert "Endast PDF- och TXT-filer" in exc_info.value.detail
