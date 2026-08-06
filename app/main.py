from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import settings
from .pdf_extractor import PdfExtractionError
from .service import ProofreadingService

BASE_DIR = Path(__file__).resolve().parent.parent
app = FastAPI(title="PDF-korrektur", version="1.0.0")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
service = ProofreadingService(BASE_DIR)


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"default_model": settings.openai_model, "max_upload_mb": settings.max_upload_mb},
    )


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/review")
def review_pdf(
    file: UploadFile = File(...),
    api_key: str = Form(""),
    model: str = Form(settings.openai_model),
    mock: bool = Form(False),
):
    filename = Path(file.filename or "document.pdf").name
    if Path(filename).suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail="Endast PDF-filer stöds.")

    max_bytes = settings.max_upload_mb * 1024 * 1024
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp_path = Path(tmp.name)
        total = 0
        while chunk := file.file.read(1024 * 1024):
            total += len(chunk)
            if total > max_bytes:
                tmp_path.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail=f"Filen är större än {settings.max_upload_mb} MB.")
            tmp.write(chunk)

    try:
        output_path, diagnostics = service.process(
            pdf_path=tmp_path,
            original_filename=filename,
            api_key=api_key.strip() or None,
            model=model.strip() or settings.openai_model,
            mock=mock,
        )
    except (PdfExtractionError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Bearbetningen misslyckades: {exc}") from exc
    finally:
        tmp_path.unlink(missing_ok=True)

    return FileResponse(
        output_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=output_path.name,
        headers={"X-Run-Id": str(diagnostics["run_id"])},
    )
