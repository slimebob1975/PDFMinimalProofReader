from __future__ import annotations

import difflib
import re
from collections import Counter

from .models import ModelSuggestion, TextUnit, ValidatedSuggestion


# Fotnotsmarkörer i käll-PDF:erna förekommer ofta direkt efter ett ord, t.ex.
# "Kristi1", "splittringar1" eller "Helige1 Ande". De är referensdata och
# ska inte bli korrekturförslag.
FOOTNOTE_MARKER_RE = re.compile(r"(?<=[A-Za-zÅÄÖåäö])\d{1,2}(?=\s|$|[.,;:!?])")
FOOTNOTE_LABEL_RE = re.compile(
    r"(?:^|\s)\d{1,2}(?:KJV|KXII|KJV/NKJV|NKJV|SFB|B2000|B1917)?\s*:",
    re.IGNORECASE,
)
FOOTNOTE_SOURCE_RE = re.compile(
    r"\b\d{1,2}(?:KJV/NKJV|KJV|KXII|NKJV|SFB|B2000|B1917)\b",
    re.IGNORECASE,
)

# Avsiktligt konservativ igenkänning av bibelhänvisningar. Vi skyddar även
# hänvisningar som PDF-layouten har skjutit in mitt i löptexten.
BIBLE_REFERENCE_TOKEN_RE = re.compile(
    r"(?:[1-3]\s+)?[A-ZÅÄÖ][A-Za-zÅÄÖåäö]{1,15}\.?\s+"
    r"\d{1,3}:\d{1,3}(?:[-–]\d{1,3})?(?:f{1,2})?\.?"
)

# Skydda även fristående bokförkortningar när modellen bara vill ändra deras
# punkt, t.ex. "Dom." -> "Dom" eller "Upp." -> "Upp".
BIBLE_BOOK_ABBREVIATIONS = {
    "mos", "jos", "dom", "sam", "kung", "krön", "neh", "est", "ps",
    "ords", "pred", "jes", "jer", "klag", "hes", "dan", "hos", "oba",
    "nah", "hab", "sef", "hagg", "sak", "mal", "matt", "mark", "luk",
    "joh", "apg", "rom", "kor", "gal", "ef", "fil", "kol", "tess",
    "tim", "tit", "filem", "hebr", "jak", "petr", "jud", "upp",
}

WORD_RE = re.compile(r"[A-Za-zÅÄÖåäö]+")
DIGIT_RE = re.compile(r"\d+")
SENTENCE_LINKERS = {"för", "men", "och", "eller", "utan", "ty"}
NEGATIONS = {"inte", "icke", "ej"}
RISKY_PRONOUNS = {
    "jag", "mig", "min", "mitt", "mina",
    "du", "dig", "din", "ditt", "dina",
    "han", "honom", "hans",
    "hon", "henne", "hennes",
    "den", "det", "dess",
    "vi", "oss", "vår", "vårt", "våra",
    "ni", "er", "ert", "era",
    "de", "dem", "deras",
    "sin", "sitt", "sina",
}
AUXILIARIES = {
    "är", "var", "vara", "blir", "blev", "bli",
    "har", "hade", "ha",
    "kan", "kunde", "ska", "skulle", "må", "måste",
    "vill", "ville", "bör", "borde", "får", "fick",
    "kommer", "kom",
}


def _contains_bible_reference(text: str) -> bool:
    return bool(BIBLE_REFERENCE_TOKEN_RE.search(text))


def _changes_standalone_bible_abbreviation(old: str, new: str) -> bool:
    """Protect punctuation-only edits to standalone Bible-book abbreviations."""
    old_raw = re.sub(r"\s+", " ", old.strip())
    new_raw = re.sub(r"\s+", " ", new.strip())
    old_norm = old_raw.casefold()
    new_norm = new_raw.casefold()
    if old_norm == new_norm:
        return False
    # Bibelboksförkortningar i referensapparaten är versaliserade. Detta
    # undviker att t.ex. ett vanligt gemensamt ord "dom." fångas av regeln.
    if not any(char.isupper() for char in old_raw + new_raw if char.isalpha()):
        return False

    def parse(value: str) -> tuple[str | None, bool]:
        match = re.fullmatch(r"(?:(?:[1-3])\s+)?([a-zåäö]+)(\.)?", value)
        if not match:
            return None, False
        return match.group(1), bool(match.group(2))

    old_book, old_dot = parse(old_norm)
    new_book, new_dot = parse(new_norm)
    return (
        old_book is not None
        and old_book == new_book
        and old_book in BIBLE_BOOK_ABBREVIATIONS
        and old_dot != new_dot
    )


def _only_repositions_footnote_marker(old: str, new: str) -> bool:
    """Protect layout-only movement of a footnote digit around punctuation."""
    marker_attached = re.compile(r"(?<=[A-Za-zÅÄÖåäö])\d{1,2}")
    if not (marker_attached.search(old) or marker_attached.search(new)):
        return False

    alnum = lambda value: re.sub(r"[^A-Za-zÅÄÖåäö0-9]", "", value)
    punct = lambda value: sorted(re.findall(r"[.,;:!?]", value))
    return old != new and alnum(old) == alnum(new) and punct(old) == punct(new)


def _only_removes_footnote_marker(old: str, new: str) -> bool:
    """Return True when the proposed change only removes footnote digits."""
    stripped = FOOTNOTE_MARKER_RE.sub("", old)
    return stripped != old and stripped == new


def _changes_digits(old: str, new: str) -> bool:
    """Protect verse, footnote and other numeric data from model edits."""
    return DIGIT_RE.findall(old) != DIGIT_RE.findall(new)


def _contains_footnote_source_token(text: str) -> bool:
    """Protect explicit source labels such as '1KJV' even without a colon."""
    return bool(FOOTNOTE_SOURCE_RE.search(text))


def _is_in_footnote_text(context: str, old: str) -> bool:
    """Detect edits inside common footnote/reference text such as '1KJV: ...'."""
    if not old:
        return False
    start = context.find(old)
    if start < 0:
        return False
    before = context[:start]
    # A footnote label/source token should be fairly close to the edited span.
    # Restricting the look-back avoids classifying later ordinary prose as a footnote.
    tail = before[-120:]
    return bool(FOOTNOTE_LABEL_RE.search(tail) or FOOTNOTE_SOURCE_RE.search(tail))


def _whitespace_gaps(value: str) -> tuple[str, set[int]]:
    """Return non-whitespace text and gap positions between its characters."""
    chars: list[str] = []
    gaps: set[int] = set()
    pending_space = False
    for char in value:
        if char.isspace():
            if chars:
                pending_space = True
            continue
        if pending_space and chars:
            gaps.add(len(chars))
        pending_space = False
        chars.append(char)
    return "".join(chars), gaps


def _only_changes_layout_spacing(old: str, new: str) -> bool:
    """Protect spacing changes adjacent to punctuation/digits from PDF layout."""
    old_base, old_gaps = _whitespace_gaps(old)
    new_base, new_gaps = _whitespace_gaps(new)
    if old == new or old_base != new_base:
        return False
    changed_gaps = old_gaps.symmetric_difference(new_gaps)
    if not changed_gaps:
        # The non-whitespace text is identical, so this is leading/trailing
        # whitespace only. Treat it as a layout artifact.
        return True
    for gap in changed_gaps:
        left = old_base[gap - 1] if gap > 0 else ""
        right = old_base[gap] if gap < len(old_base) else ""
        if left.isalpha() and right.isalpha():
            return False
    return True


def _only_changes_case(old: str, new: str) -> bool:
    """Pure capitalization changes are too risky in theological/historical text."""
    return old != new and old.casefold() == new.casefold()


def _changes_capitalized_word(old: str, new: str) -> bool:
    """Return True when a lexical change alters a capitalized word/name."""
    old_words = WORD_RE.findall(old)
    new_words = WORD_RE.findall(new)
    matcher = difflib.SequenceMatcher(
        None, [word.casefold() for word in old_words], [word.casefold() for word in new_words]
    )
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        changed = old_words[i1:i2] + new_words[j1:j2]
        if any(word[:1].isupper() for word in changed):
            return True
    return False


def _changes_genitive_chain(old: str, new: str) -> bool:
    """Protect plausible genitive chains such as 'Davids sons'."""
    old_words = WORD_RE.findall(old)
    new_words = WORD_RE.findall(new)
    if len(old_words) != len(new_words) or len(old_words) < 2:
        return False
    if not old_words[0][:1].isupper() or not old_words[0].endswith("s"):
        return False
    differences = [(a, b) for a, b in zip(old_words, new_words) if a != b]
    return (
        len(differences) == 1
        and differences[0][0].endswith("s")
        and differences[0][0][:-1] == differences[0][1]
    )


def _changes_terminal_s_morphology(old: str, new: str) -> bool:
    """Protect adding/removing a terminal -s, often a genitive in older text."""
    old_words = WORD_RE.findall(old)
    new_words = WORD_RE.findall(new)
    if len(old_words) != len(new_words) or not old_words:
        return False
    diffs = [(a.casefold(), b.casefold()) for a, b in zip(old_words, new_words) if a.casefold() != b.casefold()]
    if len(diffs) != 1:
        return False
    a, b = diffs[0]
    return (len(a) > 2 and a.endswith("s") and a[:-1] == b) or (
        len(b) > 2 and b.endswith("s") and b[:-1] == a
    )


def _changes_apostrophe_genitive(old: str, new: str) -> bool:
    """Protect model-invented apostrophe genitives such as 'Sebedeus' -> 'Sebedeus\''."""
    normalize = lambda value: re.sub(r"[’']", "", value)
    return old != new and normalize(old) == normalize(new) and ("'" in new or "’" in new)


def _removes_sentence_linker(old: str, new: str) -> bool:
    """Preserve sentence-initial conjunctions that may be intentional style."""
    old_words = WORD_RE.findall(old)
    new_words = WORD_RE.findall(new)
    if not old_words or old_words[0].casefold() not in SENTENCE_LINKERS:
        return False
    return not new_words or new_words[0].casefold() != old_words[0].casefold()


def _only_changes_final_punctuation(old: str, new: str) -> bool:
    """Detect punctuation-only changes at the end of a text unit."""
    strip_final = lambda value: re.sub(r"[\s.,;:!?…]+$", "", value)
    return old != new and strip_final(old) == strip_final(new)


def _word_counter(text: str) -> Counter[str]:
    return Counter(word.casefold() for word in WORD_RE.findall(text))


def _changes_negation(old: str, new: str) -> bool:
    old_words = _word_counter(old)
    new_words = _word_counter(new)
    return any(old_words[word] != new_words[word] for word in NEGATIONS)


def _changes_risky_pronoun(old: str, new: str) -> bool:
    """Protect pronoun removal/substitution; allow pure pronoun insertion for recall."""
    old_words = _word_counter(old)
    new_words = _word_counter(new)
    return any(old_words[word] > new_words[word] for word in RISKY_PRONOUNS)


def _only_reorders_words(old: str, new: str) -> bool:
    """Protect pure content-word permutations while allowing common auxiliary inversion."""
    old_words = [word.casefold() for word in WORD_RE.findall(old)]
    new_words = [word.casefold() for word in WORD_RE.findall(new)]
    if len(old_words) < 2 or old_words == new_words or Counter(old_words) != Counter(new_words):
        return False

    # Auxiliary movement is a common, legitimate grammatical correction, e.g.
    # 'Kanske jag kan' -> 'Kanske kan jag'. Do not block that mechanically.
    matcher = difflib.SequenceMatcher(None, old_words, new_words)
    changed_words: set[str] = set()
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            changed_words.update(old_words[i1:i2])
            changed_words.update(new_words[j1:j2])
    if set(old_words) & AUXILIARIES:
        return False
    return True


def _only_normalizes_sa_sade(old: str, new: str) -> bool:
    """Protect the accepted preterite variants 'sa' and 'sade'."""
    old_norm = re.sub(r"\s+", " ", old.strip().casefold())
    new_norm = re.sub(r"\s+", " ", new.strip().casefold())
    return {old_norm, new_norm} == {"sa", "sade"}


def _only_normalizes_protected_variant(old: str, new: str) -> bool:
    """Protect explicit accepted variants such as 'i väg'/'iväg'."""
    variant_re = re.compile(r"\b(?:i\s+väg|iväg|var\s+sin|varsin)\b", re.IGNORECASE)
    if not (variant_re.search(old) or variant_re.search(new)):
        return False

    def canonical(value: str) -> str:
        value = value.casefold()
        value = re.sub(r"\bi\s+väg\b", "iväg", value)
        value = re.sub(r"\bvar\s+sin\b", "varsin", value)
        return re.sub(r"\s+", " ", value).strip()

    return old != new and canonical(old) == canonical(new)


def _changes_causal_for_to_for_att(old: str, new: str) -> bool:
    """Protect causal 'för' before an explicit subject from expansion to 'för att'."""
    old_norm = re.sub(r"\s+", " ", old.strip().casefold())
    new_norm = re.sub(r"\s+", " ", new.strip().casefold())

    if old_norm == "för" and new_norm == "för att":
        return True

    subject_pronouns = {
        "jag", "du", "han", "hon", "den", "det", "vi", "ni", "de", "man", "vem", "vad"
    }
    match = re.fullmatch(r"för ([^ ]+)(.*)", old_norm)
    if not match or match.group(1) not in subject_pronouns:
        return False
    return new_norm == f"för att {match.group(1)}{match.group(2)}"


def _removes_exact_repetition(old: str, new: str) -> bool:
    """Protect deliberate rhetorical repetition from automatic deletion."""
    old_words = [word.casefold() for word in WORD_RE.findall(old)]
    new_words = [word.casefold() for word in WORD_RE.findall(new)]
    if len(old_words) != len(new_words) + 1:
        return False
    for index in range(len(old_words) - 1):
        if old_words[index] == old_words[index + 1]:
            candidate = old_words[:index] + old_words[index + 1:]
            if candidate == new_words:
                return True
    return False


class SuggestionValidator:
    def __init__(self, max_replacement_chars: int = 220, max_change_ratio: float = 0.60):
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
            elif suggestion.confidence.casefold() == "låg":
                reasons.append("låg_säkerhet_ska_inte_exporteras")
            elif suggestion.error_type in {"versalisering", "inkonsekvens"}:
                reasons.append("versalisering_eller_inkonsekvens_skyddas")
            elif _changes_digits(suggestion.old, suggestion.new):
                reasons.append("siffer_eller_fotnotsdata_skyddas")
            elif _only_removes_footnote_marker(suggestion.old, suggestion.new):
                reasons.append("fotnotsmarkör_ska_ignoreras")
            elif _only_repositions_footnote_marker(suggestion.old, suggestion.new):
                reasons.append("fotnotsmarkörens_layout_ska_ignoreras")
            elif _contains_footnote_source_token(suggestion.old) or _contains_footnote_source_token(suggestion.new):
                reasons.append("fotnotskälla_ska_ignoreras")
            elif _is_in_footnote_text(unit.text, suggestion.old):
                reasons.append("fotnotstext_ska_ignoreras")
            elif _contains_bible_reference(suggestion.old) or _contains_bible_reference(suggestion.new):
                reasons.append("bibelhänvisning_ska_ignoreras")
            elif _changes_standalone_bible_abbreviation(suggestion.old, suggestion.new):
                reasons.append("bibelboksförkortning_ska_ignoreras")
            elif _only_normalizes_sa_sade(suggestion.old, suggestion.new):
                reasons.append("sa_sade_variant_skyddas")
            elif _only_normalizes_protected_variant(suggestion.old, suggestion.new):
                reasons.append("accepterad_skrivvariant_skyddas")
            elif _only_changes_layout_spacing(suggestion.old, suggestion.new):
                reasons.append("layoutmellanslag_ska_ignoreras")
            elif _only_changes_case(suggestion.old, suggestion.new):
                reasons.append("ren_versaländring_ska_ignoreras")
            elif _changes_capitalized_word(suggestion.old, suggestion.new):
                reasons.append("egennamn_eller_titel_skyddas")
            elif _changes_apostrophe_genitive(suggestion.old, suggestion.new):
                reasons.append("apostrofgenitiv_skyddas")
            elif _changes_genitive_chain(suggestion.old, suggestion.new):
                reasons.append("möjlig_genitivkedja_skyddas")
            elif _changes_terminal_s_morphology(suggestion.old, suggestion.new):
                reasons.append("möjlig_genitivform_skyddas")
            elif _changes_negation(suggestion.old, suggestion.new):
                reasons.append("negation_skyddas")
            elif _changes_risky_pronoun(suggestion.old, suggestion.new):
                reasons.append("pronomen_eller_syftning_skyddas")
            elif _changes_causal_for_to_for_att(suggestion.old, suggestion.new):
                reasons.append("kausalt_för_skyddas")
            elif _only_reorders_words(suggestion.old, suggestion.new):
                reasons.append("ren_ordföljdsändring_skyddas")
            elif _removes_exact_repetition(suggestion.old, suggestion.new):
                reasons.append("möjlig_avsiktlig_upprepning_skyddas")
            elif _removes_sentence_linker(suggestion.old, suggestion.new):
                reasons.append("satsinledande_sambandsord_skyddas")
            elif unit.text.endswith(suggestion.old) and _only_changes_final_punctuation(
                suggestion.old, suggestion.new
            ):
                reasons.append("slutinterpunktion_i_textenhet_skyddas")
            elif len(suggestion.old) > self.max_replacement_chars or len(suggestion.new) > self.max_replacement_chars:
                reasons.append("för_lång_ersättning")
            else:
                ratio = difflib.SequenceMatcher(None, suggestion.old, suggestion.new).ratio()
                if ratio < self.max_change_ratio:
                    reasons.append("för_omfattande_eller_osäker_ändring")

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