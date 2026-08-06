from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .config import settings
from .excel_exporter import export_workbook
from .pdf_extractor import build_units, detect_document_name, extract_lines
from .reviewer import MockReviewer, OpenAIReviewer
from .validator import SuggestionValidator


class ProofreadingService:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.policy_path = base_dir / "policy" / "prompt_policy.md"

    def process(self, pdf_path: Path, original_filename: str, api_key: str | None, model: str, mock: bool = False) -> tuple[Path, dict]:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:8]
        run_dir = settings.runs_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        lines, diagnostics = extract_lines(pdf_path, max_pages=settings.max_pages)
        document = detect_document_name(lines, pdf_path)
        units = build_units(lines, document=document)
        if not units:
            raise ValueError("Ingen granskningsbar textstruktur kunde identifieras.")

        reviewer = MockReviewer() if mock else OpenAIReviewer(
            api_key=api_key or settings.openai_api_key or "",
            policy_path=self.policy_path,
            max_chars=settings.batch_max_chars,
        )
        if not mock and not (api_key or settings.openai_api_key):
            raise ValueError("OpenAI API-nyckel saknas.")

        raw_suggestions = reviewer.review(units, model=model)
        accepted, rejected = SuggestionValidator().validate(raw_suggestions, units)
        diagnostics.update({
            "run_id": run_id,
            "source_filename": original_filename,
            "document": document,
            "unit_count": len(units),
            "raw_suggestion_count": len(raw_suggestions),
            "accepted_suggestion_count": len(accepted),
            "rejected_suggestion_count": len(rejected),
            "model": "mock" if mock else model,
        })

        output_path = run_dir / f"{Path(original_filename).stem}_korrektur.xlsx"
        export_workbook(output_path, accepted, units, rejected, diagnostics)
        (run_dir / "extraction.json").write_text(
            json.dumps({"diagnostics": diagnostics, "lines": [asdict(x) for x in lines], "units": [asdict(x) for x in units]}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (run_dir / "suggestions_raw.json").write_text(
            json.dumps([item.model_dump() for item in raw_suggestions], ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (run_dir / "suggestions_rejected.json").write_text(
            json.dumps(rejected, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return output_path, diagnostics
