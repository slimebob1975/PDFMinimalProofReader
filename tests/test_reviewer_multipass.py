from pathlib import Path
from types import SimpleNamespace

from app.models import ModelSuggestion, SuggestionEnvelope, TextUnit
from app.reviewer import OpenAIReviewer, _suggestion_key


def make_unit(text: str = "han gick hem. han sov.") -> TextUnit:
    return TextUnit(
        "unit_00001", "Test", 1, 1, False, "Test 1:1", 1, 1, None,
        "vänster", 1, 1, text, [text]
    )


def suggestion(old: str, new: str) -> ModelSuggestion:
    return ModelSuggestion(
        unit_id="unit_00001",
        old=old,
        new=new,
        error_type="annat_entydigt_språkfel",
        motivation="Test.",
        confidence="hög",
    )


def test_suggestion_key_merges_same_change_with_extra_context():
    unit = make_unit("han gick hem.")
    short = suggestion("han", "Han")
    wide = suggestion("han gick hem.", "Han gick hem.")
    assert _suggestion_key(short, unit) == _suggestion_key(wide, unit)


def test_suggestion_key_keeps_same_edit_at_different_positions_separate():
    unit = make_unit("han gick hem. han sov.")
    # The exact old span is ambiguous in the unit, so the conservative fallback
    # intentionally avoids position-based merging rather than guessing.
    first = suggestion("han", "Han")
    second = suggestion("han", "HAN")
    assert _suggestion_key(first, unit) != _suggestion_key(second, unit)


class FakeResponses:
    def __init__(self, envelopes):
        self.envelopes = iter(envelopes)
        self.calls = 0

    def parse(self, **kwargs):
        self.calls += 1
        return SimpleNamespace(output_parsed=next(self.envelopes))


class FakeClient:
    def __init__(self, envelopes):
        self.responses = FakeResponses(envelopes)


def test_adaptive_review_stops_after_two_low_gain_runs(tmp_path: Path):
    policy = tmp_path / "policy.md"
    policy.write_text("Testpolicy", encoding="utf-8")
    unit = make_unit("ord fel.")
    s1 = suggestion("fel", "rätt")

    reviewer = object.__new__(OpenAIReviewer)
    reviewer.client = FakeClient([
        SuggestionEnvelope(suggestions=[s1]),
        SuggestionEnvelope(suggestions=[s1]),
        SuggestionEnvelope(suggestions=[s1]),
    ])
    reviewer.policy = "Testpolicy"
    reviewer.max_chars = 14_000
    reviewer.min_runs = 2
    reviewer.max_runs = 10
    reviewer.saturation_ratio = 0.10
    reviewer.saturation_streak = 2
    reviewer.last_diagnostics = {}

    result = reviewer.review([unit], model="fake")

    assert len(result) == 1
    assert reviewer.client.responses.calls == 3
    assert reviewer.last_diagnostics["actual_gpt_calls"] == 3
    batch = reviewer.last_diagnostics["batches"][0]
    assert batch["stop_reason"] == "saturation"
    assert batch["observations"][0]["hit_count"] == 3
    assert batch["observations"][0]["agreement"] == 1.0


def test_adaptive_review_keeps_running_when_new_material_exceeds_threshold(tmp_path: Path):
    policy = tmp_path / "policy.md"
    policy.write_text("Testpolicy", encoding="utf-8")
    unit = make_unit("stavat böjd skriven.")
    s1 = suggestion("stavat", "stavad")
    s2 = suggestion("böjd", "böjt")
    s3 = suggestion("skriven", "skrivet")

    reviewer = object.__new__(OpenAIReviewer)
    reviewer.client = FakeClient([
        SuggestionEnvelope(suggestions=[s1]),
        SuggestionEnvelope(suggestions=[s1, s2]),
        SuggestionEnvelope(suggestions=[s1, s2, s3]),
        SuggestionEnvelope(suggestions=[s1, s2, s3]),
        SuggestionEnvelope(suggestions=[s1, s2, s3]),
    ])
    reviewer.policy = "Testpolicy"
    reviewer.max_chars = 14_000
    reviewer.min_runs = 2
    reviewer.max_runs = 10
    reviewer.saturation_ratio = 0.10
    reviewer.saturation_streak = 2
    reviewer.last_diagnostics = {}

    result = reviewer.review([unit], model="fake")

    assert len(result) == 3
    assert reviewer.client.responses.calls == 5
    assert reviewer.last_diagnostics["batches"][0]["stop_reason"] == "saturation"


def test_locus_saturation_groups_alternative_fixes_at_same_place():
    unit = make_unit("På den dagen du stod på andra sidan.")
    variants = [
        suggestion("På den dagen", "På den dagen,"),
        suggestion("På den dagen du stod", "På den dagen då du stod"),
        suggestion("På den dagen du stod på andra sidan", "På den dagen stod du på andra sidan"),
    ]
    from app.reviewer import _merge_locus_span, _suggestion_span

    loci = []
    created = []
    for item in variants:
        span = _suggestion_span(item, unit)
        assert span is not None
        is_new, _ = _merge_locus_span(loci, span)
        created.append(is_new)

    assert created == [True, False, False]
    assert len(loci) == 1


def test_review_payload_excludes_layout_metadata():
    unit = make_unit("ord fel.")
    s1 = suggestion("fel", "rätt")

    class CapturingResponses(FakeResponses):
        def __init__(self, envelopes):
            super().__init__(envelopes)
            self.kwargs = []

        def parse(self, **kwargs):
            self.kwargs.append(kwargs)
            return super().parse(**kwargs)

    reviewer = object.__new__(OpenAIReviewer)
    reviewer.client = SimpleNamespace(responses=CapturingResponses([
        SuggestionEnvelope(suggestions=[s1]),
        SuggestionEnvelope(suggestions=[s1]),
        SuggestionEnvelope(suggestions=[s1]),
    ]))
    reviewer.policy = "Testpolicy"
    reviewer.max_chars = 14_000
    reviewer.min_runs = 2
    reviewer.max_runs = 3
    reviewer.saturation_ratio = 0.10
    reviewer.saturation_streak = 2
    reviewer.last_diagnostics = {}

    reviewer.review([unit], model="fake")

    import json
    user_content = reviewer.client.responses.kwargs[0]["input"][1]["content"]
    payload = json.loads(user_content.split("\n", 1)[1])
    assert set(payload[0]) == {"unit_id", "reference", "text"}
    assert "line_end" not in user_content
    assert "column" not in user_content


def test_hybrid_saturation_keeps_running_while_raw_discovery_is_new():
    unit = make_unit("På den dagen du stod här. korrekt text.")
    bad1 = suggestion("På den dagen", "På den dagen,")
    bad2 = suggestion("korrekt", "Korrekt")

    reviewer = object.__new__(OpenAIReviewer)
    reviewer.client = FakeClient([
        SuggestionEnvelope(suggestions=[bad1]),
        SuggestionEnvelope(suggestions=[bad1, bad2]),
        SuggestionEnvelope(suggestions=[bad1, bad2]),
        SuggestionEnvelope(suggestions=[bad1, bad2]),
    ])
    reviewer.policy = "Testpolicy"
    reviewer.max_chars = 14_000
    reviewer.min_runs = 2
    reviewer.max_runs = 10
    reviewer.saturation_ratio = 0.10
    reviewer.saturation_streak = 2
    reviewer.last_diagnostics = {}

    reviewer.review([unit], model="fake")

    # Run 2 adds a new raw locus, so validator rejection alone must not start
    # the saturation streak. Two genuinely raw+plausible saturated runs follow.
    assert reviewer.client.responses.calls == 4
    runs = reviewer.last_diagnostics["batches"][0]["runs"]
    assert runs[1]["raw_marginal_gain"] > 0.10
    assert runs[1]["plausible_marginal_gain"] == 0.0
    assert runs[-1]["raw_saturated"] is True
    assert runs[-1]["plausible_saturated"] is True
    assert reviewer.last_diagnostics["batches"][0]["stop_reason"] == "saturation"


def test_hybrid_saturation_does_not_start_before_five_runs():
    unit = make_unit("På den dagen du stod här.")
    bad = suggestion("På den dagen", "På den dagen,")

    reviewer = object.__new__(OpenAIReviewer)
    reviewer.client = FakeClient([
        SuggestionEnvelope(suggestions=[bad]),
        SuggestionEnvelope(suggestions=[bad]),
        SuggestionEnvelope(suggestions=[bad]),
        SuggestionEnvelope(suggestions=[bad]),
        SuggestionEnvelope(suggestions=[bad]),
        SuggestionEnvelope(suggestions=[bad]),
    ])
    reviewer.policy = "Testpolicy"
    reviewer.max_chars = 14_000
    reviewer.configured_min_runs = 5
    reviewer.min_runs = 5
    reviewer.max_runs = 10
    reviewer.saturation_ratio = 0.10
    reviewer.saturation_streak = 2
    reviewer.last_diagnostics = {}

    reviewer.review([unit], model="fake")

    # Runs 1-4 may be fully saturated, but the streak is not allowed to begin
    # before run 5. Run 5 is streak 1 and run 6 is streak 2.
    assert reviewer.client.responses.calls == 6
    runs = reviewer.last_diagnostics["batches"][0]["runs"]
    assert len(runs) == 6
    assert reviewer.last_diagnostics["min_runs"] == 5
    assert reviewer.last_diagnostics["version"] == "3.4"


def test_consensus_gate_requires_high_confidence_and_type_specific_hits():
    from app.reviewer import consensus_rejection_reason, required_consensus_hits

    spelling = ModelSuggestion(
        unit_id="unit_00001", old="fäll", new="fel", error_type="stavning",
        motivation="Tydligt stavfel.", confidence="hög"
    )
    grammar = ModelSuggestion(
        unit_id="unit_00001", old="är fel", new="är rätt", error_type="grammatik",
        motivation="Tydligt grammatikfel.", confidence="hög"
    )
    punctuation = ModelSuggestion(
        unit_id="unit_00001", old="ord,", new="ord.", error_type="interpunktion",
        motivation="Tydligt interpunktionsfel.", confidence="hög"
    )
    medium = spelling.model_copy(update={"confidence": "medel"})

    assert required_consensus_hits("stavning") == 2
    assert required_consensus_hits("grammatik") == 3
    assert required_consensus_hits("interpunktion") == 4
    assert consensus_rejection_reason(spelling, 1) == "otillräcklig_multipass_konsensus"
    assert consensus_rejection_reason(spelling, 2) is None
    assert consensus_rejection_reason(grammar, 2) == "otillräcklig_multipass_konsensus"
    assert consensus_rejection_reason(grammar, 3) is None
    assert consensus_rejection_reason(punctuation, 3) == "otillräcklig_multipass_konsensus"
    assert consensus_rejection_reason(punctuation, 4) is None
    assert consensus_rejection_reason(medium, 10) == "multipass_konsensus_kräver_hög_säkerhet"
