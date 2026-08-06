from app.models import ModelSuggestion, TextUnit
from app.validator import SuggestionValidator


def unit() -> TextUnit:
    return TextUnit("unit_00001", "Test", 1, 1, False, "Test 1:1", 1, 1, None, "vänster", 1, 2, "Det är fel stavat.", ["Det är fel stavat."])


def test_accepts_local_anchored_change():
    suggestion = ModelSuggestion(unit_id="unit_00001", old="stavat", new="stavad", error_type="böjning_kongruens", motivation="Predikativ kongruens.", confidence="hög")
    accepted, rejected = SuggestionValidator().validate([suggestion], [unit()])
    assert len(accepted) == 1
    assert not rejected


def test_rejects_unanchored_change():
    suggestion = ModelSuggestion(unit_id="unit_00001", old="saknas", new="finns", error_type="annat_entydigt_språkfel", motivation="Test.", confidence="hög")
    accepted, rejected = SuggestionValidator().validate([suggestion], [unit()])
    assert not accepted
    assert rejected[0]["reasons"] == ["originaltext_saknas_i_kontext"]
