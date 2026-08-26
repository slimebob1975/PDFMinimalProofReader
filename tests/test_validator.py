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


def _variant_unit(text: str) -> TextUnit:
    return TextUnit("unit_00001", "Test", 1, 1, False, "Test 1:1", 1, 1, None, "vänster", 1, 2, text, [text])


def test_rejects_sa_sade_variant_inside_longer_phrase():
    text = "Han sa till folket att gå."
    suggestion = ModelSuggestion(
        unit_id="unit_00001", old="Han sa till folket", new="Han sade till folket",
        error_type="annat_entydigt_språkfel", motivation="Normering.", confidence="hög"
    )
    accepted, rejected = SuggestionValidator().validate([suggestion], [_variant_unit(text)])
    assert not accepted
    assert rejected[0]["reasons"] == ["sa_sade_variant_skyddas"]


def test_rejects_embedded_accepted_style_variants():
    cases = [
        ("Han kommer vara där.", "kommer vara", "kommer att vara"),
        ("Han gick istället hem.", "gick istället hem", "gick i stället hem"),
        ("De gick emot staden.", "gick emot staden", "gick mot staden"),
        ("De grå kläderna låg där.", "De grå kläderna", "De gråa kläderna"),
    ]
    for text, old, new in cases:
        suggestion = ModelSuggestion(
            unit_id="unit_00001", old=old, new=new,
            error_type="annat_entydigt_språkfel", motivation="Normering.", confidence="hög"
        )
        accepted, rejected = SuggestionValidator().validate([suggestion], [_variant_unit(text)])
        assert not accepted, (old, new)
        assert rejected[0]["reasons"] == ["accepterad_skrivvariant_skyddas"], (old, new, rejected)


def test_rejects_numeric_bible_reference_fragment_edit():
    text = "Be mig, så ska jag ge dig hedningarna till arv. Ps. 22:28 78:8. Sak. 9:10."
    suggestion = ModelSuggestion(
        unit_id="unit_00001", old="22:28 78:8", new="22:28, 78:8",
        error_type="interpunktion", motivation="Komma saknas mellan hänvisningarna.", confidence="hög"
    )
    accepted, rejected = SuggestionValidator().validate([suggestion], [_variant_unit(text)])
    assert not accepted
    assert rejected[0]["reasons"] == ["referens_eller_notapparat_ska_ignoreras"]


def test_rejects_edit_immediately_before_bible_reference():
    text = "Då ska alla skogens träd jubla Jes. 55:12."
    suggestion = ModelSuggestion(
        unit_id="unit_00001", old="jubla", new="jubla.",
        error_type="interpunktion", motivation="Punkt saknas före hänvisningen.", confidence="hög"
    )
    accepted, rejected = SuggestionValidator().validate([suggestion], [_variant_unit(text)])
    assert not accepted
    assert rejected[0]["reasons"] == ["referens_eller_notapparat_ska_ignoreras"]


def test_rejects_un_note_apparatus_edit():
    text = "På detta sätt upphäver ni Guds bud1 för era traditioners skull. 1UN: ord."
    suggestion = ModelSuggestion(
        unit_id="unit_00001", old="1UN: ord.", new="1UN utelämnar: ord.",
        error_type="annat_entydigt_språkfel", motivation="Noten behöver verb.", confidence="hög"
    )
    accepted, rejected = SuggestionValidator().validate([suggestion], [_variant_unit(text)])
    assert not accepted
    assert any("notapparat" in reason or "fotnot" in reason for reason in rejected[0]["reasons"])


def test_rejects_optional_discourse_and_conjunction_commas():
    cases = [
        ("Därför, allt det ni vill göra ska ni göra.", "Därför,", "Därför"),
        ("Ja du ska få se det.", "Ja", "Ja,"),
        ("De finns inte mer och de återkommer inte.", "mer och", "mer, och"),
    ]
    for text, old, new in cases:
        suggestion = ModelSuggestion(
            unit_id="unit_00001", old=old, new=new,
            error_type="kommatering", motivation="Interpunktion.", confidence="hög"
        )
        accepted, rejected = SuggestionValidator().validate([suggestion], [_variant_unit(text)])
        assert not accepted, (old, new, rejected)
        assert rejected[0]["reasons"] == ["valfri_interpunktion_skyddas"]


def test_rejects_possible_standard_grammar_variants():
    cases = [
        ("Jorden var full med orätt.", "full med", "full av"),
        ("Även fast Herren är upphöjd ser han till den ringe.", "Även fast", "Även om"),
        ("De församlade sig.", "församlade", "samlade"),
        ("Han tog dem i famn.", "i famn", "i sin famn"),
        ("Som om det varit min vän.", "Som om det varit", "Som om det hade varit"),
        ("Jag kommer undervisa er.", "kommer undervisa", "kommer att undervisa"),
        ("de som somnat in", "de som somnat in", "de som har somnat in"),
    ]
    for text, old, new in cases:
        suggestion = ModelSuggestion(
            unit_id="unit_00001", old=old, new=new,
            error_type="grammatik", motivation="Normering.", confidence="hög"
        )
        accepted, rejected = SuggestionValidator().validate([suggestion], [_variant_unit(text)])
        assert not accepted, (old, new, rejected)
        assert rejected[0]["reasons"] == ["grammatiskt_möjlig_variant_skyddas"]


def test_rejects_genitive_like_compound_joining():
    text = "Jag gläder mig i min frälsnings Gud."
    suggestion = ModelSuggestion(
        unit_id="unit_00001", old="frälsnings Gud", new="frälsnings-Gud",
        error_type="särskrivning_sammanskrivning", motivation="Sammansättning.", confidence="hög"
    )
    accepted, rejected = SuggestionValidator().validate([suggestion], [_variant_unit(text)])
    assert not accepted
    assert rejected[0]["reasons"] == ["möjlig_genitivsammansättning_skyddas"]


def test_rejects_added_preposition_that_collides_with_following_preposition():
    text = "Om än jag vandrar mitt genom nöd så håller du mig vid liv."
    suggestion = ModelSuggestion(
        unit_id="unit_00001", old="mitt", new="mitt i",
        error_type="dubblerat_saknat_ord", motivation="Uttrycket kräver prepositionen i.", confidence="hög"
    )
    accepted, rejected = SuggestionValidator().validate([suggestion], [_variant_unit(text)])
    assert not accepted
    assert rejected[0]["reasons"] == ["ersättning_skapar_prepositionskrock"]


def test_keeps_clear_vocative_and_att_comma_corrections_available():
    cases = [
        ("Tala Herre, för din tjänare hör.", "Tala Herre", "Tala, Herre", "kommatering"),
        ("Jag ångrar, att jag gjorde det.", "ångrar,", "ångrar", "interpunktion"),
    ]
    for text, old, new, error_type in cases:
        suggestion = ModelSuggestion(
            unit_id="unit_00001", old=old, new=new,
            error_type=error_type, motivation="Tydlig interpunktionsregel.", confidence="hög"
        )
        accepted, rejected = SuggestionValidator().validate([suggestion], [_variant_unit(text)])
        assert len(accepted) == 1, (old, new, rejected)



def test_rejects_inflected_full_med_full_av_variants():
    cases = [
        ("Husen var fulla med folk.", "fulla med", "fulla av"),
        ("Kärlet var fullt med vatten.", "fullt med", "fullt av"),
    ]
    for text, old, new in cases:
        suggestion = ModelSuggestion(
            unit_id="unit_00001", old=old, new=new,
            error_type="grammatik", motivation="Normering.", confidence="hög"
        )
        accepted, rejected = SuggestionValidator().validate([suggestion], [_variant_unit(text)])
        assert not accepted, (old, new, rejected)
        assert rejected[0]["reasons"] == ["grammatiskt_möjlig_variant_skyddas"]


def test_nearby_punctuation_insertions_are_treated_as_competing_hypotheses():
    text = "Han teg för då visste han svaret."
    suggestions = [
        ModelSuggestion(
            unit_id="unit_00001", old="för då", new="för, då",
            error_type="kommatering", motivation="Komma krävs.", confidence="hög"
        ),
        ModelSuggestion(
            unit_id="unit_00001", old="för då", new="för då,",
            error_type="kommatering", motivation="Komma krävs.", confidence="hög"
        ),
    ]
    accepted, rejected = SuggestionValidator().validate(suggestions, [_variant_unit(text)])
    assert not accepted
    assert sum(
        "konkurrerande_korrigeringshypoteser_samma_felställe" in item["reasons"]
        for item in rejected
    ) == 2


def test_rejects_replacement_that_creates_duplicate_auxiliary_in_context():
    text = "När tiden kommer, då ska det ord fullbordas."
    suggestion = ModelSuggestion(
        unit_id="unit_00001", old="det ord fullbordas", new="det ska fullbordas",
        error_type="grammatik", motivation="Hjälpverb saknas.", confidence="hög"
    )
    accepted, rejected = SuggestionValidator().validate([suggestion], [_variant_unit(text)])
    assert not accepted
    assert rejected[0]["reasons"] == ["ersättning_skapar_lokal_grammatikkrock"]


def test_rejects_historical_possessive_relative_normalization():
    text = "Mannen vilkens namn var känt kom in."
    suggestion = ModelSuggestion(
        unit_id="unit_00001", old="vilkens", new="vilket",
        error_type="grammatik", motivation="Modern relativform.", confidence="hög"
    )
    accepted, rejected = SuggestionValidator().validate([suggestion], [_variant_unit(text)])
    assert not accepted
    assert rejected[0]["reasons"] == ["historisk_possessiv_relativform_skyddas"]


def test_rejects_existing_genitive_expanded_to_definite_genitive():
    text = "Det var en from regents sinnelag."
    suggestion = ModelSuggestion(
        unit_id="unit_00001", old="regents", new="regentens",
        error_type="böjning_kongruens", motivation="Bestämd genitiv krävs.", confidence="hög"
    )
    accepted, rejected = SuggestionValidator().validate([suggestion], [_variant_unit(text)])
    assert not accepted
    assert rejected[0]["reasons"] == ["befintlig_genitivform_skyddas"]


def test_rejects_definite_suffix_added_after_possessive():
    text = "Han lämnade din lägel."
    suggestion = ModelSuggestion(
        unit_id="unit_00001", old="lägel", new="lägeln",
        error_type="böjning_kongruens", motivation="Bestämd form krävs.", confidence="hög"
    )
    accepted, rejected = SuggestionValidator().validate([suggestion], [_variant_unit(text)])
    assert not accepted
    assert rejected[0]["reasons"] == ["possessiv_med_obestämd_substantivform_skyddas"]


def test_rejects_replacement_that_duplicates_following_word_across_boundary():
    text = "Herre, för mig ut ur min nöd!"
    suggestion = ModelSuggestion(
        unit_id="unit_00001", old="för mig", new="för mig ut",
        error_type="dubblerat_saknat_ord", motivation="Ordet ut saknas.", confidence="hög"
    )
    accepted, rejected = SuggestionValidator().validate([suggestion], [_variant_unit(text)])
    assert not accepted
    assert rejected[0]["reasons"] == ["ersättning_dubblerar_angränsande_material"]


def test_rejects_replacement_that_duplicates_preceding_word_across_boundary():
    text = "han är god och trofast."
    suggestion = ModelSuggestion(
        unit_id="unit_00001", old="är god", new="han är god",
        error_type="dubblerat_saknat_ord", motivation="Subjekt saknas.", confidence="hög"
    )
    accepted, rejected = SuggestionValidator().validate([suggestion], [_variant_unit(text)])
    assert not accepted
    assert rejected[0]["reasons"] == ["ersättning_dubblerar_angränsande_material"]


def test_boundary_duplicate_guard_does_not_block_legitimate_expansion():
    text = "Han bad för mig i nöden."
    suggestion = ModelSuggestion(
        unit_id="unit_00001", old="bad för mig", new="bad innerligt för mig",
        error_type="dubblerat_saknat_ord", motivation="Ett ord saknas.", confidence="hög"
    )
    accepted, rejected = SuggestionValidator().validate([suggestion], [_variant_unit(text)])
    assert len(accepted) == 1
    assert not rejected
