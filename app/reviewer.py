from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Protocol

from .models import ModelSuggestion, SuggestionEnvelope, TextUnit


class Reviewer(Protocol):
    def review(
        self,
        units: list[TextUnit],
        model: str,
        run_id: str | None = None,
    ) -> list[ModelSuggestion]: ...


def load_policy(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def make_batches(units: list[TextUnit], max_chars: int) -> list[list[TextUnit]]:
    batches: list[list[TextUnit]] = []
    current: list[TextUnit] = []
    current_size = 0
    for unit in units:
        size = len(unit.text) + 180
        if current and current_size + size > max_chars:
            batches.append(current)
            current = []
            current_size = 0
        current.append(unit)
        current_size += size
    if current:
        batches.append(current)
    return batches


class OpenAIReviewer:
    def __init__(self, api_key: str, policy_path: Path, max_chars: int = 14_000):
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("Paketet openai saknas. Kör: python -m pip install -r requirements.txt") from exc
        self.client = OpenAI(api_key=api_key)
        self.policy = load_policy(policy_path)
        self.max_chars = max_chars

    def review(
        self,
        units: list[TextUnit],
        model: str,
        run_id: str | None = None,
    ) -> list[ModelSuggestion]:
        all_suggestions: list[ModelSuggestion] = []
        batches = make_batches(units, self.max_chars)
        total_calls = len(batches)
        prefix = f"[{run_id}] " if run_id else ""

        print(f"{prefix}GPT-anrop planerade: {total_calls}", flush=True)

        for index, batch in enumerate(batches, start=1):
            payload = [
                {
                    "unit_id": unit.unit_id,
                    "reference": unit.reference,
                    "page": unit.page_start,
                    "column": unit.column_start,
                    "line_start": unit.line_start,
                    "line_end": unit.line_end,
                    "text": unit.text,
                }
                for unit in batch
            ]
            user_message = (
                "Granska följande textenheter. Returnera bara nödvändiga minimala korrigeringar.\n"
                + json.dumps(payload, ensure_ascii=False)
            )
            request_input = [
                {"role": "system", "content": self.policy},
                {"role": "user", "content": user_message},
            ]

            first_ref = batch[0].reference if batch else "-"
            last_ref = batch[-1].reference if batch else "-"
            print(
                f"{prefix}GPT-anrop {index}/{total_calls} startar "
                f"({len(batch)} textenheter, {first_ref} – {last_ref})...",
                flush=True,
            )
            started = time.perf_counter()
            response = self.client.responses.parse(
                model=model,
                input=request_input,
                text_format=SuggestionEnvelope,
            )
            elapsed = time.perf_counter() - started

            parsed = response.output_parsed
            if parsed is None:
                raise RuntimeError("Modellen returnerade inget strukturerat svar.")

            parsed_suggestions = parsed.suggestions
            all_suggestions.extend(parsed_suggestions)

            print(
                f"{prefix}GPT-anrop {index}/{total_calls} klart på {elapsed:.1f} s; "
                f"{len(parsed_suggestions)} förslag i svaret. Totalt hittills: {len(all_suggestions)}.",
                flush=True,
            )

        return all_suggestions


class MockReviewer:
    """Offlinegranskare för installations- och flödestest."""

    def review(
        self,
        units: list[TextUnit],
        model: str = "mock",
        run_id: str | None = None,
    ) -> list[ModelSuggestion]:
        prefix = f"[{run_id}] " if run_id else ""
        print(f"{prefix}Offlineläge: inga GPT-anrop utförs.", flush=True)
        suggestions: list[ModelSuggestion] = []
        for unit in units:
            if "  " in unit.text:
                suggestions.append(
                    ModelSuggestion(
                        unit_id=unit.unit_id,
                        old="  ",
                        new=" ",
                        error_type="annat_entydigt_språkfel",
                        motivation="Dubbelt blanksteg.",
                        confidence="hög",
                    )
                )
        return suggestions