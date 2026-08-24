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


def test_rejects_optional_comma_after_initial_adverbial():
    text = "På den dagen du stod på andra sidan, då var också du en av dem."
    u = TextUnit("unit_00001", "Test", 1, 1, False, "Test 1:1", 1, 1, None, "vänster", 1, 2, text, [text])
    suggestion = ModelSuggestion(
        unit_id="unit_00001",
        old="På den dagen",
        new="På den dagen,",
        error_type="kommatering",
        motivation="Test.",
        confidence="medel",
    )
    accepted, rejected = SuggestionValidator().validate([suggestion], [u])
    assert not accepted
    assert rejected[0]["reasons"] == ["valfri_kommatering_efter_inledande_adverbial_skyddas"]


def test_sentence_initial_linker_is_not_misclassified_as_proper_name():
    text = "För Herrens dag är nära."
    u = TextUnit("unit_00001", "Test", 1, 1, False, "Test 1:1", 1, 1, None, "vänster", 1, 2, text, [text])
    suggestion = ModelSuggestion(
        unit_id="unit_00001",
        old="För",
        new="Ty",
        error_type="annat_entydigt_språkfel",
        motivation="Test.",
        confidence="medel",
    )
    accepted, rejected = SuggestionValidator().validate([suggestion], [u])
    assert not accepted
    assert rejected[0]["reasons"] == ["satsinledande_sambandsord_skyddas"]


def test_rejects_punctuation_that_is_already_present_in_context():
    text = "Hans gömda skatter uppsökta."
    u = TextUnit("unit_00001", "Test", 1, 1, False, "Test 1:1", 1, 1, None, "vänster", 1, 2, text, [text])
    suggestion = ModelSuggestion(unit_id="unit_00001", old="uppsökta", new="uppsökta.", error_type="interpunktion", motivation="Test.", confidence="hög")
    accepted, rejected = SuggestionValidator().validate([suggestion], [u])
    assert not accepted
    assert rejected[0]["reasons"] == ["interpunktion_finns_redan_i_kontext"]


def test_rejects_deletion_only_lexical_simplification():
    text = "Främlingar förde iväg hans styrkor."
    u = TextUnit("unit_00001", "Test", 1, 1, False, "Test 1:1", 1, 1, None, "vänster", 1, 2, text, [text])
    suggestion = ModelSuggestion(unit_id="unit_00001", old="förde iväg", new="förde", error_type="annat_entydigt_språkfel", motivation="Test.", confidence="medel")
    accepted, rejected = SuggestionValidator().validate([suggestion], [u])
    assert not accepted
    assert rejected[0]["reasons"] == ["lexikal_förenkling_skyddas"]


def test_rejects_competing_hypotheses_at_same_locus():
    text = "Hur genomsökt ska inte Esau bli, hans gömda skatter uppsökta."
    u = TextUnit("unit_00001", "Test", 1, 1, False, "Test 1:1", 1, 1, None, "vänster", 1, 2, text, [text])
    suggestions = [
        ModelSuggestion(unit_id="unit_00001", old="uppsökta", new="uppsökas", error_type="grammatik", motivation="A", confidence="hög"),
        ModelSuggestion(unit_id="unit_00001", old="uppsökta", new="uppsökt", error_type="böjning_kongruens", motivation="B", confidence="hög"),
    ]
    accepted, rejected = SuggestionValidator().validate(suggestions, [u])
    assert not accepted
    assert len(rejected) == 2
    assert all(item["reasons"] == ["konkurrerande_korrigeringshypoteser_samma_felställe"] for item in rejected)


def test_sa_som_is_not_classified_as_proper_name():
    text = "Så som du har handlat, så ska du bli behandlad."
    u = TextUnit("unit_00001", "Test", 1, 1, False, "Test 1:1", 1, 1, None, "vänster", 1, 2, text, [text])
    suggestion = ModelSuggestion(unit_id="unit_00001", old="Så som", new="Såsom", error_type="annat_entydigt_språkfel", motivation="Test.", confidence="medel")
    _accepted, rejected = SuggestionValidator().validate([suggestion], [u])
    assert not any("egennamn_eller_titel_skyddas" in item["reasons"] for item in rejected)


def test_rejects_ambiguous_old_span_that_occurs_twice():
    text = "På den dagen du stod där, den dagen kom de."
    u = TextUnit("unit_00001", "Test", 1, 1, False, "Test 1:1", 1, 1, None, "vänster", 1, 2, text, [text])
    suggestion = ModelSuggestion(
        unit_id="unit_00001",
        old="den dagen",
        new="den dag",
        error_type="grammatik",
        motivation="Uttrycket brukar vara obestämt och är sannolikt fel.",
        confidence="medel",
    )
    accepted, rejected = SuggestionValidator().validate([suggestion], [u])
    assert not accepted
    assert rejected[0]["reasons"] == ["originaltext_inte_entydigt_lokaliserbar"]


def test_rejects_medium_confidence_stylistic_or_uncertain_motivation():
    text = "Så som du har handlat, så ska du bli behandlad."
    u = TextUnit("unit_00001", "Test", 1, 1, False, "Test 1:1", 1, 1, None, "vänster", 1, 2, text, [text])
    suggestion = ModelSuggestion(
        unit_id="unit_00001",
        old="Så som",
        new="Såsom",
        error_type="annat_entydigt_språkfel",
        motivation="Den sammansatta formen är idiomatiskt bättre här.",
        confidence="medel",
    )
    accepted, rejected = SuggestionValidator().validate([suggestion], [u])
    assert not accepted
    assert rejected[0]["reasons"] == ["osäker_eller_stilistisk_motivering"]


def test_rejects_comma_to_semicolon_style_preference():
    text = "Se, jag har gjort dig obetydlig bland folken, djupt föraktad är du."
    u = TextUnit("unit_00001", "Test", 1, 1, False, "Test 1:1", 1, 1, None, "vänster", 1, 2, text, [text])
    suggestion = ModelSuggestion(
        unit_id="unit_00001",
        old=text,
        new="Se, jag har gjort dig obetydlig bland folken; djupt föraktad är du.",
        error_type="interpunktion",
        motivation="Här binds två självständiga huvudsatser ihop med komma; semikolon krävs.",
        confidence="hög",
    )
    accepted, rejected = SuggestionValidator().validate([suggestion], [u])
    assert not accepted
    assert rejected[0]["reasons"] == ["komma_semikolon_stilval_skyddas"]


def test_rejects_auxiliary_insertion_in_possible_literary_ellipsis():
    text = "Hur genomsökt ska inte Esau bli, hans gömda skatter uppsökta."
    u = TextUnit("unit_00001", "Test", 1, 1, False, "Test 1:1", 1, 1, None, "vänster", 1, 2, text, [text])
    suggestion = ModelSuggestion(
        unit_id="unit_00001",
        old="hans gömda skatter uppsökta",
        new="hans gömda skatter ska uppsökas",
        error_type="dubblerat_saknat_ord",
        motivation="Satsen saknar finitt verb; passivformen behöver hjälpverb för att bli grammatiskt fullständig.",
        confidence="hög",
    )
    accepted, rejected = SuggestionValidator().validate([suggestion], [u])
    assert not accepted
    assert rejected[0]["reasons"] == ["möjlig_elliptisk_konstruktion_skyddas"]
