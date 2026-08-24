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
    r"(?:^|\s)\d{1,2}(?:KJV|KXII|KJV/NKJV|NKJV|SFB|B2000|B1917|UN)?\s*:",
    re.IGNORECASE,
)
FOOTNOTE_SOURCE_RE = re.compile(
    r"\b(?:\d{1,2})?(?:KJV/NKJV|KJV|KXII|NKJV|SFB|B2000|B1917|UN)\b",
    re.IGNORECASE,
)

# Numeric fragments are also reference data when the model only sees the middle
# of a reference sequence, e.g. "22:28 78:8" or "19:16.-18".
BIBLE_REFERENCE_FRAGMENT_RE = re.compile(
    r"\b\d{1,3}:\d{1,3}(?:\.?(?:[-–]\d{1,3}|f{1,2}))?\.?",
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
SENTENCE_LINKERS = {"för", "men", "och", "eller", "utan", "ty", "så"}
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

# Sentence-initial function/linking words are capitalized by position, not because
# they are names or titles. They must not trigger the proper-name protection.
SENTENCE_INITIAL_COMMON_WORDS = {
    "för", "men", "och", "eller", "utan", "ty", "så",
    "på", "i", "av", "till", "från", "med", "om", "när",
    "då", "efter", "före", "under", "över", "inte", "även", "såsom",
}

UNCERTAIN_OR_STYLISTIC_MOTIVATION_RE = re.compile(
    r"\b(?:brukar|sannolikt|idiomatiskt|mer naturligt|bättre|föredras|verkar)\b",
    re.IGNORECASE,
)

PREPOSITIONS = {
    "av", "bakom", "bland", "efter", "enligt", "för", "från", "före", "genom",
    "hos", "i", "inifrån", "inom", "intill", "kring", "med", "mellan", "mot",
    "om", "på", "till", "under", "ur", "utan", "vid", "över",
}


def _contains_bible_reference(text: str) -> bool:
    return bool(BIBLE_REFERENCE_TOKEN_RE.search(text))


def _contains_bible_reference_fragment(text: str) -> bool:
    """Protect numeric reference fragments even when the book name lies outside old/new."""
    return bool(BIBLE_REFERENCE_FRAGMENT_RE.search(text))


def _touches_reference_or_note_apparatus(context: str, old: str) -> bool:
    """Fail closed for edits in, or immediately adjacent to, references/notes.

    The reviewer deliberately receives prose units that may also contain reference
    apparatus. A model edit must not repair punctuation inside that apparatus or at
    the prose/reference boundary.
    """
    if not old:
        return False
    start = context.find(old)
    if start < 0 or context.find(old, start + 1) >= 0:
        return False
    end = start + len(old)
    window = context[max(0, start - 80): min(len(context), end + 80)]
    if FOOTNOTE_LABEL_RE.search(window) or FOOTNOTE_SOURCE_RE.search(window):
        # Require the edited span to be reasonably close to the note token.
        local = context[max(0, start - 35): min(len(context), end + 35)]
        if FOOTNOTE_LABEL_RE.search(local) or FOOTNOTE_SOURCE_RE.search(local):
            return True

    # Directly inside a numeric Bible reference fragment.
    if _contains_bible_reference_fragment(old):
        return True

    # Directly adjacent to an upcoming book+chapter reference, e.g.
    # "jubla Jes. 55:12" -> "jubla. Jes. 55:12".
    after = context[end:].lstrip()
    if BIBLE_REFERENCE_TOKEN_RE.match(after):
        return True
    return False


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
    """Return True when a lexical change alters a plausible proper name/title.

    Capitalization caused solely by sentence position (e.g. "För", "Men",
    "Och") is explicitly excluded from this heuristic.
    """
    old_words = WORD_RE.findall(old)
    new_words = WORD_RE.findall(new)
    matcher = difflib.SequenceMatcher(
        None, [word.casefold() for word in old_words], [word.casefold() for word in new_words]
    )
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        changed = old_words[i1:i2] + new_words[j1:j2]
        for word in changed:
            if not word[:1].isupper():
                continue
            if word.casefold() in SENTENCE_INITIAL_COMMON_WORDS:
                continue
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


def _adds_optional_comma_after_initial_phrase(unit_text: str, old: str, new: str) -> bool:
    """Protect optional stylistic comma insertion after a short initial phrase.

    Swedish normally does not require an English-style comma after a short initial
    adverbial such as "På den dagen". In a minimal proofreader this should not be
    exported as an error merely because the model prefers a pause.
    """
    old_stripped = old.strip()
    new_stripped = new.strip()
    if new_stripped != old_stripped + ",":
        return False
    if not unit_text.startswith(old_stripped):
        return False
    words = WORD_RE.findall(old_stripped)
    if not 1 <= len(words) <= 6:
        return False
    return words[0].casefold() in {
        "på", "i", "efter", "före", "under", "över", "vid",
        "från", "till", "då", "sedan", "nu", "här", "där",
    }


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


def _canonicalize_protected_variants(value: str) -> str:
    """Canonicalize accepted variants even when embedded in a longer phrase."""
    value = re.sub(r"\s+", " ", value.strip().casefold())
    substitutions = (
        (r"\bi\s+stället\b", "istället"),
        (r"\bi\s+väg\b", "iväg"),
        (r"\bvar\s+sin\b", "varsin"),
        (r"\bkommer\s+att\s+vara\b", "kommer vara"),
        (r"\bsade\b", "sa"),
        (r"\bemot\b", "mot"),
        (r"\bgråa\b", "grå"),
    )
    for pattern, replacement in substitutions:
        value = re.sub(pattern, replacement, value)
    return value


def _only_normalizes_sa_sade(old: str, new: str) -> bool:
    """Protect 'sa'/'sade' also inside a longer otherwise unchanged phrase."""
    if not (re.search(r"\bsa(?:de)?\b", old, re.IGNORECASE) or re.search(r"\bsa(?:de)?\b", new, re.IGNORECASE)):
        return False
    return old != new and _canonicalize_protected_variants(old) == _canonicalize_protected_variants(new)


def _only_normalizes_protected_variant(old: str, new: str) -> bool:
    """Protect accepted style/usage variants, including embedded occurrences."""
    if old == new:
        return False
    return _canonicalize_protected_variants(old) == _canonicalize_protected_variants(new)


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


def _punctuation_already_present(context: str, old: str, new: str) -> bool:
    """Reject adding punctuation that already follows the source span."""
    if not old or not new.startswith(old) or len(new) <= len(old):
        return False
    added = new[len(old):]
    if not added or any(char not in ".,;:!?…" for char in added):
        return False
    start = 0
    while True:
        index = context.find(old, start)
        if index < 0:
            return False
        if context[index + len(old): index + len(old) + len(added)] == added:
            return True
        start = index + 1


def _only_deletes_lexical_material(old: str, new: str) -> bool:
    """Protect deletion-only lexical simplifications in a minimal proofreader."""
    old_words = [word.casefold() for word in WORD_RE.findall(old)]
    new_words = [word.casefold() for word in WORD_RE.findall(new)]
    if len(old_words) <= len(new_words) or not new_words:
        return False
    pos = 0
    for word in old_words:
        if pos < len(new_words) and word == new_words[pos]:
            pos += 1
    if pos != len(new_words):
        return False
    if any(old_words[i] == old_words[i + 1] for i in range(len(old_words) - 1)):
        return False
    return True


def _only_changes_comma_to_semicolon(old: str, new: str) -> bool:
    """Protect comma/semicolon preference when no lexical material changes."""
    if len(old) != len(new) or old == new:
        return False
    diffs = [(a, b) for a, b in zip(old, new) if a != b]
    return len(diffs) == 1 and diffs[0] == (",", ";")


def _optional_punctuation_normalization(context: str, old: str, new: str) -> bool:
    """Protect punctuation preferences that are not mandatory corrections.

    Keep deterministic cases such as vocatives and comma-before-att removal available,
    but reject optional discourse commas and comma insertion before conjunctions.
    """
    old_norm = re.sub(r"\s+", " ", old.strip())
    new_norm = re.sub(r"\s+", " ", new.strip())

    # Optional comma after a short initial adverb/discourse marker, both insertion
    # and deletion: "Därför," <-> "Därför" and "Ja" <-> "Ja,".
    pairs = {(old_norm, new_norm), (new_norm, old_norm)}
    for plain, punct in pairs:
        if punct == plain + ",":
            words = WORD_RE.findall(plain)
            if 1 <= len(words) <= 4 and words and words[0].casefold() in {
                "därför", "däremot", "således", "alltså", "dock", "nu", "då", "ja", "nej"
            }:
                return True

    # Pure comma insertion immediately before a coordinating conjunction is
    # normally stylistic in Swedish prose, not a minimal-proofreading certainty.
    if new_norm != old_norm and new_norm.replace(",", "") == old_norm.replace(",", ""):
        if re.search(r",\s+(?:och|men|eller|utan)\b", new_norm, re.IGNORECASE) and not re.search(
            r",\s+(?:och|men|eller|utan)\b", old_norm, re.IGNORECASE
        ):
            return True
    return False


def _normalizes_possible_standard_variant(old: str, new: str) -> bool:
    """Protect a small set of observed, grammatical usage variants.

    These are deliberately asymmetric only in spelling; either direction is protected
    because a minimal proofreader must not modernize one accepted form into another.
    """
    old_norm = re.sub(r"\s+", " ", old.strip().casefold())
    new_norm = re.sub(r"\s+", " ", new.strip().casefold())
    protected_pairs = {
        frozenset({"full med", "full av"}),
        frozenset({"även fast", "även om"}),
        frozenset({"församlade", "samlade"}),
        frozenset({"i famn", "i sin famn"}),
    }
    if frozenset({old_norm, new_norm}) in protected_pairs:
        return True

    # "kommer (att) + infinitiv" occurs in both forms in Swedish usage.
    if re.fullmatch(r"kommer(?: att)? [a-zåäö]+", old_norm) and re.fullmatch(
        r"kommer(?: att)? [a-zåäö]+", new_norm
    ):
        return old_norm.replace("kommer att ", "kommer ") == new_norm.replace("kommer att ", "kommer ")

    # Historical/literary auxiliary ellipsis after "som om" should not be
    # normalized automatically: "Som om det varit" -> "... hade varit".
    if old_norm.startswith("som om ") and " varit" in old_norm and " hade varit" in new_norm:
        return True

    # Relative participle/perfect alternatives: "de som somnat in" ->
    # "de som har somnat in". This is a usage choice, not an indisputable error.
    if " som " in old_norm and " som har " in new_norm:
        return old_norm.replace(" som ", " som har ", 1) == new_norm
    return False


def _changes_genitive_compound_spacing(old: str, new: str) -> bool:
    """Protect genitive-like compounds from stylistic joining/hyphenation."""
    old_norm = re.sub(r"\s+", " ", old.strip().casefold())
    new_norm = re.sub(r"\s+", " ", new.strip().casefold())
    m = re.fullmatch(r"([a-zåäö]+s) ([a-zåäö]+)", old_norm)
    if not m:
        return False
    joined = m.group(1) + m.group(2)
    hyphen = m.group(1) + "-" + m.group(2)
    return new_norm in {joined, hyphen}


def _adds_preposition_before_existing_preposition(context: str, old: str, new: str) -> bool:
    """Reject local replacements that create stacked prepositions in full context."""
    if not old or old not in context:
        return False
    start = context.find(old)
    if context.find(old, start + 1) >= 0:
        return False
    old_words = [w.casefold() for w in WORD_RE.findall(old)]
    new_words = [w.casefold() for w in WORD_RE.findall(new)]
    if len(new_words) != len(old_words) + 1 or new_words[:len(old_words)] != old_words:
        return False
    added = new_words[-1]
    if added not in PREPOSITIONS:
        return False
    after = context[start + len(old):].lstrip()
    m = WORD_RE.match(after)
    return bool(m and m.group(0).casefold() in PREPOSITIONS)


def _adds_auxiliary_to_possible_ellipsis(
    context: str, suggestion: ModelSuggestion
) -> bool:
    """Protect possible literary ellipsis from automatic finite-verb insertion.

    This is deliberately narrow: the source span must begin immediately after a
    comma, the proposed correction must add an auxiliary, and the model must
    explicitly argue that a finite/helper verb is missing.
    """
    old = suggestion.old
    new = suggestion.new
    if suggestion.error_type != "dubblerat_saknat_ord" or not old or old not in context:
        return False
    start = context.find(old)
    if start < 0 or context.find(old, start + 1) >= 0:
        return False
    if not context[:start].rstrip().endswith(","):
        return False
    old_words = [word.casefold() for word in WORD_RE.findall(old)]
    new_words = [word.casefold() for word in WORD_RE.findall(new)]
    added_aux = any(word in AUXILIARIES and word not in old_words for word in new_words)
    if not added_aux:
        return False
    motivation = (suggestion.motivation or "").casefold()
    return bool(re.search(r"(?:saknar (?:ett )?finitt verb|hjälpverb|grammatiskt fullständig)", motivation))


def _minimal_changed_span(old: str, new: str) -> tuple[int, int]:
    prefix = 0
    max_prefix = min(len(old), len(new))
    while prefix < max_prefix and old[prefix] == new[prefix]:
        prefix += 1
    suffix = 0
    max_suffix = min(len(old) - prefix, len(new) - prefix)
    while suffix < max_suffix and old[len(old) - 1 - suffix] == new[len(new) - 1 - suffix]:
        suffix += 1
    old_end = len(old) - suffix if suffix else len(old)
    return prefix, old_end



def _is_uniquely_anchored(context: str, old: str) -> bool:
    """Require a model suggestion to identify one unambiguous source span."""
    if not old:
        return False
    first = context.find(old)
    return first >= 0 and context.find(old, first + 1) < 0


def _has_uncertain_or_stylistic_motivation(suggestion: ModelSuggestion) -> bool:
    """Precision-first guard for medium-confidence, non-rule-based motivations."""
    if suggestion.confidence.casefold() == "hög":
        return False
    return bool(UNCERTAIN_OR_STYLISTIC_MOTIVATION_RE.search(suggestion.motivation or ""))

def _accepted_span(item: ValidatedSuggestion, unit: TextUnit) -> tuple[int, int] | None:
    first = unit.text.find(item.old)
    if first < 0 or unit.text.find(item.old, first + 1) >= 0:
        return None
    rel_start, rel_end = _minimal_changed_span(item.old, item.new)
    return first + rel_start, first + rel_end


def _spans_conflict(a: tuple[int, int], b: tuple[int, int]) -> bool:
    a_start, a_end = a
    b_start, b_end = b
    tolerance = 2 if a_start == a_end or b_start == b_end else 0
    return a_start <= b_end + tolerance and b_start <= a_end + tolerance


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
            elif not _is_uniquely_anchored(unit.text, suggestion.old):
                reasons.append("originaltext_inte_entydigt_lokaliserbar")
            elif suggestion.confidence.casefold() == "låg":
                reasons.append("låg_säkerhet_ska_inte_exporteras")
            elif _has_uncertain_or_stylistic_motivation(suggestion):
                reasons.append("osäker_eller_stilistisk_motivering")
            elif suggestion.error_type in {"versalisering", "inkonsekvens"}:
                reasons.append("versalisering_eller_inkonsekvens_skyddas")
            elif _changes_digits(suggestion.old, suggestion.new):
                reasons.append("siffer_eller_fotnotsdata_skyddas")
            elif _touches_reference_or_note_apparatus(unit.text, suggestion.old):
                reasons.append("referens_eller_notapparat_ska_ignoreras")
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
            elif _normalizes_possible_standard_variant(suggestion.old, suggestion.new):
                reasons.append("grammatiskt_möjlig_variant_skyddas")
            elif _only_changes_layout_spacing(suggestion.old, suggestion.new):
                reasons.append("layoutmellanslag_ska_ignoreras")
            elif _only_changes_case(suggestion.old, suggestion.new):
                reasons.append("ren_versaländring_ska_ignoreras")
            elif _punctuation_already_present(unit.text, suggestion.old, suggestion.new):
                reasons.append("interpunktion_finns_redan_i_kontext")
            elif _only_changes_comma_to_semicolon(suggestion.old, suggestion.new):
                reasons.append("komma_semikolon_stilval_skyddas")
            elif _optional_punctuation_normalization(unit.text, suggestion.old, suggestion.new):
                reasons.append("valfri_interpunktion_skyddas")
            elif _adds_optional_comma_after_initial_phrase(unit.text, suggestion.old, suggestion.new):
                reasons.append("valfri_kommatering_efter_inledande_adverbial_skyddas")
            elif _changes_capitalized_word(suggestion.old, suggestion.new):
                reasons.append("egennamn_eller_titel_skyddas")
            elif _changes_apostrophe_genitive(suggestion.old, suggestion.new):
                reasons.append("apostrofgenitiv_skyddas")
            elif _changes_genitive_chain(suggestion.old, suggestion.new):
                reasons.append("möjlig_genitivkedja_skyddas")
            elif _changes_genitive_compound_spacing(suggestion.old, suggestion.new):
                reasons.append("möjlig_genitivsammansättning_skyddas")
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
            elif _only_deletes_lexical_material(suggestion.old, suggestion.new):
                reasons.append("lexikal_förenkling_skyddas")
            elif _adds_auxiliary_to_possible_ellipsis(unit.text, suggestion):
                reasons.append("möjlig_elliptisk_konstruktion_skyddas")
            elif _adds_preposition_before_existing_preposition(unit.text, suggestion.old, suggestion.new):
                reasons.append("ersättning_skapar_prepositionskrock")
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
        # Precision-first conflict pass: multiple incompatible accepted fixes at
        # the same source locus indicate model uncertainty. Export none of them.
        conflicted: set[int] = set()
        spans: list[tuple[int, str, tuple[int, int]]] = []
        for index, item in enumerate(accepted):
            source_unit = unit_map.get(item.unit_id)
            if source_unit is None:
                continue
            span = _accepted_span(item, source_unit)
            if span is not None:
                spans.append((index, item.unit_id, span))
        for pos, (i, unit_id, span_i) in enumerate(spans):
            for j, unit_id_j, span_j in spans[pos + 1:]:
                if unit_id == unit_id_j and _spans_conflict(span_i, span_j):
                    conflicted.add(i)
                    conflicted.add(j)

        if conflicted:
            kept: list[ValidatedSuggestion] = []
            for index, item in enumerate(accepted):
                if index in conflicted:
                    rejected.append({
                        "suggestion": {
                            "unit_id": item.unit_id,
                            "old": item.old,
                            "new": item.new,
                            "error_type": item.error_type,
                            "motivation": item.motivation,
                            "confidence": item.confidence,
                        },
                        "reasons": ["konkurrerande_korrigeringshypoteser_samma_felställe"],
                    })
                else:
                    kept.append(item)
            accepted = kept

        for index, item in enumerate(accepted, start=1):
            item.suggestion_id = index
        return accepted, rejected
