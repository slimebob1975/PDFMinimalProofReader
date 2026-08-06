from __future__ import annotations

import difflib

from .models import ModelSuggestion, TextUnit, ValidatedSuggestion


class SuggestionValidator:
    def __init__(self, max_replacement_chars: int = 220, max_change_ratio: float = 0.65):
        self.max_replacement_chars = max_replacement_chars
        self.max_change_ratio = max_change_ratio

    def validate(
        self, suggestions: list[ModelSuggestion], units: list[TextUnit]
    ) -> tuple[list[ValidatedSuggestion], list[dict]]:
        unit_map = {unit.unit_id: unit for unit in units}
        accepted: list[ValidatedSuggestion] = []
        rejected: list[dict] = []
        seen: set[tuple[str, str, str]] = set()

        for suggestion in suggestions:
            reasons: list[str] = []
            unit = unit_map.get(suggestion.unit_id)
            if unit is None:
                reasons.append("okänt_unit_id")
            elif not suggestion.old:
                reasons.append("tom_originaltext")
            elif suggestion.old == suggestion.new:
                reasons.append("ingen_förändring")
            elif suggestion.old not in unit.text:
                reasons.append("originaltext_saknas_i_kontext")
            elif len(suggestion.old) > self.max_replacement_chars or len(suggestion.new) > self.max_replacement_chars:
                reasons.append("för_lång_ersättning")
            else:
                ratio = difflib.SequenceMatcher(None, suggestion.old, suggestion.new).ratio()
                if ratio < self.max_change_ratio and max(len(suggestion.old), len(suggestion.new)) > 25:
                    reasons.append("för_omfattande_omskrivning")

            key = (suggestion.unit_id, suggestion.old, suggestion.new)
            if key in seen:
                reasons.append("dubblett")
            seen.add(key)

            if reasons or unit is None:
                rejected.append({"suggestion": suggestion.model_dump(), "reasons": reasons})
                continue

            accepted.append(
                ValidatedSuggestion(
                    suggestion_id=len(accepted) + 1,
                    unit_id=unit.unit_id,
                    document=unit.document,
                    chapter=unit.chapter,
                    verse=unit.verse,
                    reference=unit.reference,
                    page=unit.page_start,
                    column=unit.column_start,
                    line_start=unit.line_start,
                    line_end=unit.line_end,
                    original_context=unit.text,
                    old=suggestion.old,
                    new=suggestion.new,
                    error_type=suggestion.error_type,
                    motivation=suggestion.motivation,
                    confidence=suggestion.confidence,
                )
            )
        return accepted, rejected
