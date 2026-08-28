from __future__ import annotations

import json
import shutil
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .config import settings
from .excel_exporter import export_batch_workbook, export_workbook
from .pdf_extractor import build_units, detect_document_name, extract_lines
from .text_extractor import extract_text_units
from .reviewer import (
    MULTIPASS_VERSION,
    MockReviewer,
    OpenAIReviewer,
    consensus_rejection_reason,
    make_batches,
    required_consensus_hits,
)
from .validator import SuggestionValidator


class ProofreadingService:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.policy_path = base_dir / "policy" / "prompt_policy.md"
        self.uploads_dir = base_dir / settings.uploads_dir
        self.uploads_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _new_run_id() -> str:
        return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:8]

    @staticmethod
    def _safe_directory_name(filename: str, index: int) -> str:
        stem = Path(filename).stem
        safe = "".join(char if char.isalnum() or char in "-_ " else "_" for char in stem).strip()
        return f"{index:02d}_{safe or 'document'}"

    def _process_document(
        self,
        *,
        source_path: Path,
        original_filename: str,
        api_key: str | None,
        model: str,
        mock: bool,
        run_id: str,
        run_dir: Path,
        prefix: str,
    ) -> dict:
        started_total = time.perf_counter()
        run_dir.mkdir(parents=True, exist_ok=True)

        stored_source_path = run_dir / Path(original_filename).name
        shutil.copy2(source_path, stored_source_path)

        suffix = stored_source_path.suffix.lower()
        print(f"{prefix} Uppladdad fil sparad: {stored_source_path.resolve()}", flush=True)
        extraction_started = time.perf_counter()

        if suffix == ".pdf":
            print(f"{prefix} Extraherar PDF-text...", flush=True)
            lines, diagnostics = extract_lines(stored_source_path, max_pages=settings.max_pages)
            document = detect_document_name(lines, stored_source_path)
            units = build_units(lines, document=document)
            extraction_records = [asdict(x) for x in lines]
            extraction_label = f"{len(lines)} PDF-rader"
        elif suffix == ".txt":
            print(f"{prefix} Extraherar TXT-text...", flush=True)
            units, diagnostics = extract_text_units(stored_source_path)
            document = units[0].document if units else stored_source_path.stem
            extraction_records = []
            extraction_label = f"{diagnostics.get('line_count', 0)} textrader"
        else:
            raise ValueError(f"Filtypen {suffix or '(saknas)'} stöds inte.")

        extraction_elapsed = time.perf_counter() - extraction_started
        if not units:
            raise ValueError("Ingen granskningsbar textstruktur kunde identifieras.")

        print(
            f"{prefix} Extraktion klar på {extraction_elapsed:.1f} s: "
            f"{extraction_label}, {len(units)} textenheter, dokument={document}.",
            flush=True,
        )

        reviewer = MockReviewer() if mock else OpenAIReviewer(
            api_key=api_key or settings.openai_api_key or "",
            policy_path=self.policy_path,
            max_chars=settings.batch_max_chars,
            min_runs=settings.multipass_min_runs,
            max_runs=settings.multipass_max_runs,
            saturation_ratio=settings.multipass_saturation_ratio,
            saturation_streak=settings.multipass_saturation_streak,
        )
        if not mock and not (api_key or settings.openai_api_key):
            raise ValueError("OpenAI API-nyckel saknas.")

        batch_count = 0 if mock else len(make_batches(units, settings.batch_max_chars))
        effective_min_runs = 0 if mock else reviewer.min_runs
        effective_max_runs = 0 if mock else reviewer.max_runs
        estimated_calls_min = 0 if mock else batch_count * effective_min_runs
        estimated_calls_max = 0 if mock else batch_count * effective_max_runs
        print(
            f"{prefix} Multipass v{MULTIPASS_VERSION}; GPT-anrop: adaptivt "
            f"{estimated_calls_min}–{estimated_calls_max} ({batch_count} batcher; "
            f"{effective_min_runs}–{effective_max_runs} per batch).",
            flush=True,
        )

        review_started = time.perf_counter()
        raw_suggestions = reviewer.review(units, model=model, run_id=run_id)
        review_elapsed = time.perf_counter() - review_started

        print(f"{prefix} Validerar {len(raw_suggestions)} råa förslag...", flush=True)
        support_map = {} if mock else getattr(reviewer, "last_support", {})
        accepted, rejected = SuggestionValidator().validate(
            raw_suggestions, units, support_map=support_map
        )

        consensus_rejected = 0
        if not mock:
            kept = []
            raw_by_key = {(item.unit_id, item.old, item.new): item for item in raw_suggestions}
            for item in accepted:
                key = (item.unit_id, item.old, item.new)
                raw_item = raw_by_key.get(key)
                support = support_map.get(key, {})
                hit_count = int(support.get("hit_count", 0))
                if raw_item is None:
                    # This should never happen, but fail closed in a precision-first export gate.
                    reason = "multipass_supportdata_saknas"
                    required_hits = required_consensus_hits(item.error_type)
                else:
                    reason = consensus_rejection_reason(raw_item, hit_count)
                    required_hits = required_consensus_hits(raw_item.error_type)

                if reason is None:
                    kept.append(item)
                    continue

                consensus_rejected += 1
                rejected.append({
                    "suggestion": {
                        "unit_id": item.unit_id,
                        "old": item.old,
                        "new": item.new,
                        "error_type": item.error_type,
                        "motivation": item.motivation,
                        "confidence": item.confidence,
                    },
                    "reasons": [reason],
                    "consensus": {
                        "hit_count": hit_count,
                        "required_hits": required_hits,
                        "run_numbers": support.get("run_numbers", []),
                    },
                })
            accepted = kept

        print(
            f"{prefix} Validering klar: {len(accepted)} godkända, {len(rejected)} avvisade"
            + (f" ({consensus_rejected} avvisade av multipass-konsensus)." if not mock else "."),
            flush=True,
        )

        total_elapsed = time.perf_counter() - started_total
        diagnostics.update({
            "run_id": run_id,
            "run_directory": str(run_dir.resolve()),
            "source_filename": original_filename,
            "stored_source": str(stored_source_path.resolve()),
            "source_type": suffix.lstrip("."),
            "document": document,
            "unit_count": len(units),
            "estimated_gpt_calls_min": estimated_calls_min,
            "estimated_gpt_calls_max": estimated_calls_max,
            "actual_gpt_calls": reviewer.last_diagnostics.get("actual_gpt_calls", 0),
            "reviewer": reviewer.last_diagnostics,
            "raw_suggestion_count": len(raw_suggestions),
            "accepted_suggestion_count": len(accepted),
            "rejected_suggestion_count": len(rejected),
            "consensus_rejected_suggestion_count": consensus_rejected,
            "model": "mock" if mock else model,
            "extraction_seconds": round(extraction_elapsed, 3),
            "review_seconds": round(review_elapsed, 3),
            "total_seconds": round(total_elapsed, 3),
        })

        extraction_json = run_dir / "extraction.json"
        raw_json = run_dir / "suggestions_raw.json"
        rejected_json = run_dir / "suggestions_rejected.json"

        extraction_json.write_text(
            json.dumps(
                {
                    "diagnostics": diagnostics,
                    "lines": extraction_records,
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
        print(f"{prefix} Dokument klart på {total_elapsed:.1f} s.", flush=True)

        return {
            "success": True,
            "filename": original_filename,
            "document": document,
            "suggestions": accepted,
            "units": units,
            "rejected": rejected,
            "diagnostics": diagnostics,
        }

    def process(
        self,
        source_path: Path,
        original_filename: str,
        api_key: str | None,
        model: str,
        mock: bool = False,
    ) -> tuple[Path, dict]:
        """Process one supported source document (PDF or TXT)."""
        run_id = self._new_run_id()
        run_dir = self.uploads_dir / run_id
        prefix = f"[{run_id}]"

        print("", flush=True)
        print(f"{prefix} Ny körning: {original_filename}", flush=True)
        print(f"{prefix} Körningsmapp: {run_dir.resolve()}", flush=True)
        print(f"{prefix} Modell: {'mock' if mock else model}", flush=True)

        result = self._process_document(
            source_path=source_path,
            original_filename=original_filename,
            api_key=api_key,
            model=model,
            mock=mock,
            run_id=run_id,
            run_dir=run_dir,
            prefix=prefix,
        )

        output_path = run_dir / f"{Path(original_filename).stem}_korrektur.xlsx"
        print(f"{prefix} Skapar Excel-fil...", flush=True)
        export_workbook(
            output_path,
            result["suggestions"],
            result["units"],
            result["rejected"],
            result["diagnostics"],
        )
        print(f"{prefix} Resultat: {output_path.resolve()}", flush=True)
        print("", flush=True)
        return output_path, result["diagnostics"]

    def process_batch(
        self,
        documents: list[tuple[Path, str]],
        api_key: str | None,
        model: str,
        mock: bool = False,
    ) -> tuple[Path, dict]:
        """Process several PDF/TXT documents sequentially and return one workbook."""
        started_batch = time.perf_counter()
        run_id = self._new_run_id()
        run_dir = self.uploads_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        prefix = f"[{run_id}]"

        print("", flush=True)
        print(f"{prefix} Ny batchkörning: {len(documents)} dokument", flush=True)
        print(f"{prefix} Körningsmapp: {run_dir.resolve()}", flush=True)
        print(f"{prefix} Modell: {'mock' if mock else model}", flush=True)

        if not mock and not (api_key or settings.openai_api_key):
            raise ValueError("OpenAI API-nyckel saknas.")

        results: list[dict] = []
        total_documents = len(documents)

        for index, (source_path, original_filename) in enumerate(documents, start=1):
            document_prefix = f"{prefix} [Dokument {index}/{total_documents}]"
            document_dir = run_dir / self._safe_directory_name(original_filename, index)
            document_run_id = f"{run_id}_{index:02d}"

            print("", flush=True)
            print(f"{document_prefix} Startar: {original_filename}", flush=True)
            try:
                result = self._process_document(
                    source_path=source_path,
                    original_filename=original_filename,
                    api_key=api_key,
                    model=model,
                    mock=mock,
                    run_id=document_run_id,
                    run_dir=document_dir,
                    prefix=document_prefix,
                )
            except Exception as exc:
                print(f"{document_prefix} MISSLYCKADES: {exc}", flush=True)
                results.append({
                    "success": False,
                    "filename": original_filename,
                    "document": Path(original_filename).stem,
                    "suggestions": [],
                    "units": [],
                    "rejected": [],
                    "diagnostics": {},
                    "error": str(exc),
                })
                continue

            results.append(result)

        successful = sum(1 for result in results if result["success"])
        failed = len(results) - successful
        batch_elapsed = time.perf_counter() - started_batch
        batch_diagnostics = {
            "run_id": run_id,
            "run_directory": str(run_dir.resolve()),
            "document_count": len(documents),
            "successful_documents": successful,
            "failed_documents": failed,
            "model": "mock" if mock else model,
            "total_seconds": round(batch_elapsed, 3),
        }

        output_path = run_dir / f"korrektur_{run_id}.xlsx"
        print("", flush=True)
        print(f"{prefix} Skapar gemensam Excel-fil...", flush=True)
        export_batch_workbook(output_path, results, batch_diagnostics)
        print(
            f"{prefix} Batch klar på {batch_elapsed:.1f} s: "
            f"{successful} lyckades, {failed} misslyckades.",
            flush=True,
        )
        print(f"{prefix} Resultat: {output_path.resolve()}", flush=True)
        print("", flush=True)
        return output_path, batch_diagnostics