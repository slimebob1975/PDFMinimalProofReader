from __future__ import annotations

import json
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .models import ModelSuggestion, SuggestionEnvelope, TextUnit
from .validator import SuggestionValidator


MULTIPASS_VERSION = "3.2"
MIN_MULTIPASS_RUNS = 5


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


def _normalize_text(value: str) -> str:
    """Normalize representation without erasing meaningful case/punctuation differences."""
    return unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")


def _minimal_change(old: str, new: str) -> tuple[str, str, int]:
    """Trim a shared prefix/suffix and return the minimal changed core plus prefix length."""
    old = _normalize_text(old)
    new = _normalize_text(new)

    prefix = 0
    max_prefix = min(len(old), len(new))
    while prefix < max_prefix and old[prefix] == new[prefix]:
        prefix += 1

    suffix = 0
    max_suffix = min(len(old) - prefix, len(new) - prefix)
    while suffix < max_suffix and old[len(old) - 1 - suffix] == new[len(new) - 1 - suffix]:
        suffix += 1

    old_end = len(old) - suffix if suffix else len(old)
    new_end = len(new) - suffix if suffix else len(new)
    return old[prefix:old_end], new[prefix:new_end], prefix


def _suggestion_key(suggestion: ModelSuggestion, unit: TextUnit | None) -> tuple[str, int | str, str, str]:
    """Create a conservative identity for repeated observations of the same correction.

    The position prevents two identical character-level edits in the same unit from being
    merged. Trimming shared context lets e.g. a sentence-wide replacement and a word-wide
    replacement resolve to the same correction when they target the same location.
    """
    old = _normalize_text(suggestion.old)
    new = _normalize_text(suggestion.new)
    old_core, new_core, prefix_len = _minimal_change(old, new)

    position: int | str = f"old:{old}"
    if unit is not None and old:
        first = unit.text.find(old)
        if first >= 0 and unit.text.find(old, first + 1) < 0:
            position = first + prefix_len

    return (suggestion.unit_id, position, old_core, new_core)


def _suggestion_span(suggestion: ModelSuggestion, unit: TextUnit | None) -> tuple[str, int, int] | None:
    """Locate the minimal changed span in a text unit for locus-level saturation.

    Different proposed fixes to overlapping/touching text are treated as hypotheses
    about the same proofreading locus. If the source span cannot be located uniquely,
    return None rather than guessing.
    """
    if unit is None or not suggestion.old:
        return None
    old = _normalize_text(suggestion.old)
    new = _normalize_text(suggestion.new)
    first = unit.text.find(old)
    if first < 0 or unit.text.find(old, first + 1) >= 0:
        return None
    old_core, _new_core, prefix_len = _minimal_change(old, new)
    start = first + prefix_len
    end = start + len(old_core)
    return (suggestion.unit_id, start, end)


def _span_touches(a: tuple[str, int, int], b: tuple[str, int, int]) -> bool:
    if a[0] != b[0]:
        return False
    _, a_start, a_end = a
    _, b_start, b_end = b
    # Two ordinary replacement spans must actually overlap; adjacent words are
    # separate loci. Insertions are zero-width, however, and alternative fixes
    # around the same word boundary can land a character or two apart after
    # shared-prefix trimming, so allow a tiny tolerance only in that case.
    tolerance = 2 if a_start == a_end or b_start == b_end else 0
    return a_start <= b_end + tolerance and b_start <= a_end + tolerance


def _merge_locus_span(
    loci: list[tuple[str, int, int]], span: tuple[str, int, int]
) -> tuple[bool, int]:
    """Merge a span into existing loci; return (is_new_locus, locus_index)."""
    touching = [index for index, existing in enumerate(loci) if _span_touches(existing, span)]
    if not touching:
        loci.append(span)
        return True, len(loci) - 1

    unit_id = span[0]
    starts = [span[1]] + [loci[index][1] for index in touching]
    ends = [span[2]] + [loci[index][2] for index in touching]
    keep = touching[0]
    loci[keep] = (unit_id, min(starts), max(ends))
    for index in reversed(touching[1:]):
        del loci[index]
    return False, keep


@dataclass(slots=True)
class _ObservedSuggestion:
    suggestion: ModelSuggestion
    hit_count: int = 0
    run_numbers: list[int] = field(default_factory=list)


class OpenAIReviewer:
    def __init__(
        self,
        api_key: str,
        policy_path: Path,
        max_chars: int = 14_000,
        min_runs: int = 5,
        max_runs: int = 10,
        saturation_ratio: float = 0.10,
        saturation_streak: int = 2,
    ):
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("Paketet openai saknas. Kör: python -m pip install -r requirements.txt") from exc
        if min_runs < 1:
            raise ValueError("min_runs måste vara minst 1")
        effective_min_runs = max(MIN_MULTIPASS_RUNS, min_runs)
        if max_runs < effective_min_runs:
            raise ValueError(f"max_runs måste vara >= {effective_min_runs}")
        if not 0 <= saturation_ratio <= 1:
            raise ValueError("saturation_ratio måste ligga mellan 0 och 1")
        if saturation_streak < 1:
            raise ValueError("saturation_streak måste vara minst 1")

        self.client = OpenAI(api_key=api_key)
        self.policy = load_policy(policy_path)
        self.max_chars = max_chars
        self.configured_min_runs = min_runs
        self.min_runs = effective_min_runs
        self.max_runs = max_runs
        self.saturation_ratio = saturation_ratio
        self.saturation_streak = saturation_streak
        self.last_diagnostics: dict = {}
        self.saturation_validator = SuggestionValidator()

    def review(
        self,
        units: list[TextUnit],
        model: str,
        run_id: str | None = None,
    ) -> list[ModelSuggestion]:
        batches = make_batches(units, self.max_chars)
        prefix = f"[{run_id}] " if run_id else ""
        unit_map = {unit.unit_id: unit for unit in units}
        all_unique: dict[tuple[str, int | str, str, str], _ObservedSuggestion] = {}
        batch_diagnostics: list[dict] = []
        total_api_calls = 0
        saturation_validator = getattr(self, "saturation_validator", SuggestionValidator())

        configured_min_runs = getattr(self, "configured_min_runs", self.min_runs)
        if configured_min_runs != self.min_runs:
            print(
                f"{prefix}Multipass v{MULTIPASS_VERSION}: konfigurerat min_runs={configured_min_runs} "
                f"är lägre än säkerhetsgolvet {MIN_MULTIPASS_RUNS}; använder {self.min_runs}.",
                flush=True,
            )
        print(
            f"{prefix}Multipass v{MULTIPASS_VERSION}; GPT-batcher: {len(batches)}; "
            f"adaptivt {self.min_runs}–{self.max_runs} anrop per batch.",
            flush=True,
        )

        for batch_index, batch in enumerate(batches, start=1):
            # Deliberately expose only reviewable text plus stable identifiers.
            # Layout/line metadata is retained locally for validation/export and must
            # never become something the language model tries to "correct".
            payload = [
                {
                    "unit_id": unit.unit_id,
                    "reference": unit.reference,
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
            batch_seen: dict[tuple[str, int | str, str, str], _ObservedSuggestion] = {}
            batch_loci: list[tuple[str, int, int]] = []
            plausible_loci: list[tuple[str, int, int]] = []
            low_gain_streak = 0
            run_stats: list[dict] = []
            stop_reason = "max_runs"

            for run_number in range(1, self.max_runs + 1):
                previous_loci = len(batch_loci)
                print(
                    f"{prefix}Batch {batch_index}/{len(batches)}, körning {run_number}/{self.max_runs} "
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
                total_api_calls += 1

                parsed = response.output_parsed
                if parsed is None:
                    raise RuntimeError("Modellen returnerade inget strukturerat svar.")

                run_keys: set[tuple[str, int | str, str, str]] = set()
                new_unique = 0
                new_loci = 0
                for suggestion in parsed.suggestions:
                    key = _suggestion_key(suggestion, unit_map.get(suggestion.unit_id))
                    if key in run_keys:
                        continue
                    run_keys.add(key)

                    observed = batch_seen.get(key)
                    if observed is None:
                        observed = _ObservedSuggestion(suggestion=suggestion)
                        batch_seen[key] = observed
                        new_unique += 1
                    else:
                        # Prefer the model's more local representation when equivalent.
                        current_size = len(observed.suggestion.old) + len(observed.suggestion.new)
                        candidate_size = len(suggestion.old) + len(suggestion.new)
                        if candidate_size < current_size:
                            observed.suggestion = suggestion
                    observed.hit_count += 1
                    observed.run_numbers.append(run_number)

                    span = _suggestion_span(suggestion, unit_map.get(suggestion.unit_id))
                    if span is not None:
                        is_new_locus, _ = _merge_locus_span(batch_loci, span)
                        if is_new_locus:
                            new_loci += 1

                # V3: only validator-plausible loci drive saturation. Raw suggestions
                # are still retained above for diagnostics and final rejected output.
                previous_plausible = len(plausible_loci)
                candidate_suggestions = [item.suggestion for item in batch_seen.values()]
                accepted_now, _rejected_now = saturation_validator.validate(candidate_suggestions, batch)
                current_plausible_loci: list[tuple[str, int, int]] = []
                for item in accepted_now:
                    model_item = ModelSuggestion(
                        unit_id=item.unit_id,
                        old=item.old,
                        new=item.new,
                        error_type=item.error_type,
                        motivation=item.motivation,
                        confidence=item.confidence,
                    )
                    span = _suggestion_span(model_item, unit_map.get(item.unit_id))
                    if span is not None:
                        _merge_locus_span(current_plausible_loci, span)
                plausible_loci = current_plausible_loci
                plausible_after_run = len(plausible_loci)
                new_plausible_loci = max(0, plausible_after_run - previous_plausible)
                loci_after_run = len(batch_loci)
                raw_marginal_gain = (
                    new_loci / loci_after_run
                    if loci_after_run > 0
                    else 0.0
                )
                plausible_marginal_gain = (
                    new_plausible_loci / plausible_after_run
                    if plausible_after_run > 0
                    else 0.0
                )
                raw_saturated = raw_marginal_gain <= self.saturation_ratio
                plausible_saturated = plausible_marginal_gain <= self.saturation_ratio
                if (
                    run_number >= self.min_runs
                    and raw_saturated
                    and plausible_saturated
                ):
                    low_gain_streak += 1
                else:
                    low_gain_streak = 0

                run_stats.append({
                    "run": run_number,
                    "response_suggestions": len(parsed.suggestions),
                    "new_unique": new_unique,
                    "unique_after_run": len(batch_seen),
                    "new_loci": new_loci,
                    "loci_after_run": loci_after_run,
                    "validator_accepted": len(accepted_now),
                    "new_plausible_loci": new_plausible_loci,
                    "plausible_loci_after_run": plausible_after_run,
                    "raw_marginal_gain": round(raw_marginal_gain, 4),
                    "plausible_marginal_gain": round(plausible_marginal_gain, 4),
                    "raw_saturated": raw_saturated,
                    "plausible_saturated": plausible_saturated,
                    "seconds": round(elapsed, 3),
                })
                print(
                    f"{prefix}Batch {batch_index}/{len(batches)}, körning {run_number} klar på {elapsed:.1f} s; "
                    f"{len(parsed.suggestions)} förslag, {new_unique} nya varianter, "
                    f"{new_loci} nya råa felställen, {new_plausible_loci} nya plausibla felställen, "
                    f"rå marginal {raw_marginal_gain:.1%}, plausibel marginal {plausible_marginal_gain:.1%}, "
                    f"mättnadsserie {low_gain_streak}/{self.saturation_streak}.",
                    flush=True,
                )

                if run_number >= self.min_runs and low_gain_streak >= self.saturation_streak:
                    if run_number < self.max_runs:
                        stop_reason = "saturation"
                    break

            for key, observed in batch_seen.items():
                all_unique[key] = observed

            runs_used = len(run_stats)
            batch_diagnostics.append({
                "batch": batch_index,
                "first_reference": first_ref,
                "last_reference": last_ref,
                "unit_count": len(batch),
                "runs_used": runs_used,
                "stop_reason": stop_reason,
                "unique_suggestions": len(batch_seen),
                "unique_loci": len(batch_loci),
                "plausible_loci": len(plausible_loci),
                "runs": run_stats,
                "observations": [
                    {
                        "suggestion": observed.suggestion.model_dump(),
                        "hit_count": observed.hit_count,
                        "run_count": runs_used,
                        "agreement": round(observed.hit_count / runs_used, 4) if runs_used else 0.0,
                        "run_numbers": observed.run_numbers,
                    }
                    for observed in batch_seen.values()
                ],
            })

        self.last_diagnostics = {
            "strategy": "adaptive_multipass",
            "version": MULTIPASS_VERSION,
            "configured_min_runs": configured_min_runs,
            "min_runs": self.min_runs,
            "max_runs": self.max_runs,
            "saturation_ratio": self.saturation_ratio,
            "saturation_streak": self.saturation_streak,
            "batch_count": len(batches),
            "actual_gpt_calls": total_api_calls,
            "unique_suggestion_count": len(all_unique),
            "unique_locus_count": sum(batch["unique_loci"] for batch in batch_diagnostics),
            "batches": batch_diagnostics,
        }
        print(
            f"{prefix}GPT-granskning klar: {total_api_calls} faktiska anrop, "
            f"{len(all_unique)} unika förslag efter sammanslagning.",
            flush=True,
        )
        return [observed.suggestion for observed in all_unique.values()]


class MockReviewer:
    """Offlinegranskare för installations- och flödestest."""

    def __init__(self):
        self.last_diagnostics = {
            "strategy": "mock",
            "actual_gpt_calls": 0,
        }

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
