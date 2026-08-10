from __future__ import annotations

import json
import shutil
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .config import settings
from .excel_exporter import export_workbook
from .pdf_extractor import build_units, detect_document_name, extract_lines
from .reviewer import MockReviewer, OpenAIReviewer, make_batches
from .validator import SuggestionValidator


class ProofreadingService:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.policy_path = base_dir / "policy" / "prompt_policy.md"
        self.uploads_dir = base_dir / settings.uploads_dir
        self.uploads_dir.mkdir(parents=True, exist_ok=True)

    def process(
        self,
        pdf_path: Path,
        original_filename: str,
        api_key: str | None,
        model: str,
        mock: bool = False,
    ) -> tuple[Path, dict]:
        started_total = time.perf_counter()
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:8]
        run_dir = self.uploads_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        prefix = f"[{run_id}]"

        stored_pdf_path = run_dir / Path(original_filename).name
        shutil.copy2(pdf_path, stored_pdf_path)

        print("", flush=True)
        print(f"{prefix} Ny körning: {original_filename}", flush=True)
        print(f"{prefix} Körningsmapp: {run_dir.resolve()}", flush=True)
        print(f"{prefix} Uppladdad PDF sparad: {stored_pdf_path.resolve()}", flush=True)
        print(f"{prefix} Modell: {'mock' if mock else model}", flush=True)

        print(f"{prefix} Extraherar PDF-text...", flush=True)
        extraction_started = time.perf_counter()
        lines, diagnostics = extract_lines(stored_pdf_path, max_pages=settings.max_pages)
        document = detect_document_name(lines, stored_pdf_path)
        units = build_units(lines, document=document)
        extraction_elapsed = time.perf_counter() - extraction_started
        if not units:
            raise ValueError("Ingen granskningsbar textstruktur kunde identifieras.")

        print(
            f"{prefix} Extraktion klar på {extraction_elapsed:.1f} s: "
            f"{len(lines)} PDF-rader, {len(units)} textenheter, dokument={document}.",
            flush=True,
        )

        reviewer = MockReviewer() if mock else OpenAIReviewer(
            api_key=api_key or settings.openai_api_key or "",
            policy_path=self.policy_path,
            max_chars=settings.batch_max_chars,
        )
        if not mock and not (api_key or settings.openai_api_key):
            raise ValueError("OpenAI API-nyckel saknas.")

        estimated_calls = 0 if mock else len(make_batches(units, settings.batch_max_chars))
        print(f"{prefix} Uppskattat antal GPT-anrop: {estimated_calls}", flush=True)

        review_started = time.perf_counter()
        raw_suggestions = reviewer.review(units, model=model, run_id=run_id)
        review_elapsed = time.perf_counter() - review_started

        print(f"{prefix} Validerar {len(raw_suggestions)} råa förslag...", flush=True)
        accepted, rejected = SuggestionValidator().validate(raw_suggestions, units)
        print(
            f"{prefix} Validering klar: {len(accepted)} godkända, {len(rejected)} avvisade.",
            flush=True,
        )

        total_elapsed = time.perf_counter() - started_total
        diagnostics.update({
            "run_id": run_id,
            "run_directory": str(run_dir.resolve()),
            "source_filename": original_filename,
            "stored_pdf": str(stored_pdf_path.resolve()),
            "document": document,
            "unit_count": len(units),
            "estimated_gpt_calls": estimated_calls,
            "raw_suggestion_count": len(raw_suggestions),
            "accepted_suggestion_count": len(accepted),
            "rejected_suggestion_count": len(rejected),
            "model": "mock" if mock else model,
            "extraction_seconds": round(extraction_elapsed, 3),
            "review_seconds": round(review_elapsed, 3),
            "total_seconds": round(total_elapsed, 3),
        })

        output_path = run_dir / f"{Path(original_filename).stem}_korrektur.xlsx"
        print(f"{prefix} Skapar Excel-fil...", flush=True)
        export_workbook(output_path, accepted, units, rejected, diagnostics)

        extraction_json = run_dir / "extraction.json"
        raw_json = run_dir / "suggestions_raw.json"
        rejected_json = run_dir / "suggestions_rejected.json"

        extraction_json.write_text(
            json.dumps(
                {
                    "diagnostics": diagnostics,
                    "lines": [asdict(x) for x in lines],
                    "units": [asdict(x) for x in units],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        raw_json.write_text(
            json.dumps([item.model_dump() for item in raw_suggestions], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        rejected_json.write_text(
            json.dumps(rejected, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        print(f"{prefix} JSON sparad: {extraction_json.resolve()}", flush=True)
        print(f"{prefix} JSON sparad: {raw_json.resolve()}", flush=True)
        print(f"{prefix} JSON sparad: {rejected_json.resolve()}", flush=True)
        print(f"{prefix} Klart på {total_elapsed:.1f} s.", flush=True)
        print(f"{prefix} Resultat: {output_path.resolve()}", flush=True)
        print("", flush=True)
        return output_path, diagnostics