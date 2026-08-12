from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import AVAILABLE_MODELS, settings
from .pdf_extractor import PdfExtractionError
from .service import ProofreadingService

BASE_DIR = Path(__file__).resolve().parent.parent
MAX_BATCH_FILES = 20

app = FastAPI(title="PDF-korrektur", version="1.1.0")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
service = ProofreadingService(BASE_DIR)


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "default_model": settings.openai_model,
            "available_models": AVAILABLE_MODELS,
            "max_upload_mb": settings.max_upload_mb,
            "max_batch_files": MAX_BATCH_FILES,
        },
    )


@app.get("/health")
def health():
    return {"status": "ok"}


def _store_upload(upload: UploadFile, destination: Path, max_bytes: int) -> tuple[Path, str]:
    filename = Path(upload.filename or "document.pdf").name
    if Path(filename).suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail=f"Endast PDF-filer stöds: {filename}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with destination.open("wb") as target:
        while chunk := upload.file.read(1024 * 1024):
            total += len(chunk)
            if total > max_bytes:
                target.close()
                destination.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=413,
                    detail=f"Filen {filename} är större än {settings.max_upload_mb} MB.",
                )
            target.write(chunk)
    return destination, filename


@app.post("/review")
def review_pdf(
    files: list[UploadFile] = File(...),
    api_key: str = Form(""),
    model: str = Form(settings.openai_model),
    mock: bool = Form(False),
):
    if not files:
        raise HTTPException(status_code=400, detail="Välj minst en PDF-fil.")
    if len(files) > MAX_BATCH_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"Högst {MAX_BATCH_FILES} PDF-filer kan behandlas per körning.",
        )

    selected_model = model.strip() or settings.openai_model
    if not mock and selected_model not in AVAILABLE_MODELS:
        raise HTTPException(status_code=400, detail="Ogiltigt modellval.")

    max_bytes = settings.max_upload_mb * 1024 * 1024

    with tempfile.TemporaryDirectory(prefix="pdf_proofreader_") as tmp_dir_name:
        tmp_dir = Path(tmp_dir_name)
        documents: list[tuple[Path, str]] = []

        for index, upload in enumerate(files, start=1):
            filename = Path(upload.filename or f"document_{index}.pdf").name
            tmp_path = tmp_dir / f"{index:02d}_{filename}"
            stored_path, original_filename = _store_upload(upload, tmp_path, max_bytes)
            documents.append((stored_path, original_filename))

        try:
            if len(documents) == 1:
                output_path, diagnostics = service.process(
                    pdf_path=documents[0][0],
                    original_filename=documents[0][1],
                    api_key=api_key.strip() or None,
                    model=selected_model,
                    mock=mock,
                )
            else:
                output_path, diagnostics = service.process_batch(
                    documents=documents,
                    api_key=api_key.strip() or None,
                    model=selected_model,
                    mock=mock,
                )
        except (PdfExtractionError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Bearbetningen misslyckades: {exc}") from exc

    return FileResponse(
        output_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=output_path.name,
        headers={"X-Run-Id": str(diagnostics["run_id"])},
    )