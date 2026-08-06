from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from .models import ModelSuggestion, SuggestionEnvelope, TextUnit


class Reviewer(Protocol):
    def review(self, units: list[TextUnit], model: str) -> list[ModelSuggestion]: ...


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

    def review(self, units: list[TextUnit], model: str) -> list[ModelSuggestion]:
        all_suggestions: list[ModelSuggestion] = []
        for batch in make_batches(units, self.max_chars):
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
            response = self.client.responses.parse(
                model=model,
                input=[
                    {"role": "system", "content": self.policy},
                    {
                        "role": "user",
                        "content": "Granska följande textenheter. Returnera bara nödvändiga minimala korrigeringar.\n"
                        + json.dumps(payload, ensure_ascii=False),
                    },
                ],
                text_format=SuggestionEnvelope,
            )
            parsed = response.output_parsed
            if parsed is None:
                raise RuntimeError("Modellen returnerade inget strukturerat svar.")
            all_suggestions.extend(parsed.suggestions)
        return all_suggestions


class MockReviewer:
    """Offlinegranskare för installations- och flödestest."""

    def review(self, units: list[TextUnit], model: str = "mock") -> list[ModelSuggestion]:
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
