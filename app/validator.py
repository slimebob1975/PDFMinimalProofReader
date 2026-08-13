from __future__ import annotations

import difflib
import re

from .models import ModelSuggestion, TextUnit, ValidatedSuggestion


# Fotnotsmarkörer i käll-PDF:erna förekommer ofta direkt efter ett ord, t.ex.
# "Kristi1", "splittringar1" eller "Helige1 Ande". De är referensdata och
# ska inte bli korrekturförslag.
FOOTNOTE_MARKER_RE = re.compile(r"(?<=[A-Za-zÅÄÖåäö])\d{1,2}(?=\s|$|[.,;:!?])")

# Avsiktligt konservativ igenkänning av bibelhänvisningar. Vi skyddar även
# hänvisningar som PDF-layouten har skjutit in mitt i löptexten.
BIBLE_REFERENCE_TOKEN_RE = re.compile(
    r"(?:[1-3]\s+)?[A-ZÅÄÖ][A-Za-zÅÄÖåäö]{1,15}\.?\s+"
    r"\d{1,3}:\d{1,3}(?:[-–]\d{1,3})?(?:f{1,2})?\.?"
)

WORD_RE = re.compile(r"[A-Za-zÅÄÖåäö]+")
SENTENCE_LINKERS = {"för", "men", "och", "eller", "utan", "ty"}


def _contains_bible_reference(text: str) -> bool:
    return bool(BIBLE_REFERENCE_TOKEN_RE.search(text))


def _only_removes_footnote_marker(old: str, new: str) -> bool:
    """Return True when the proposed change only removes footnote digits."""
    stripped = FOOTNOTE_MARKER_RE.sub("", old)
    return stripped != old and stripped == new


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
        return False
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
    return len(differences) == 1 and differences[0][0].endswith("s") and differences[0][0][:-1] == differences[0][1]


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
            elif suggestion.confidence != "hög":
                reasons.append("endast_hög_säkerhet_tillåts")
            elif suggestion.error_type in {"versalisering", "inkonsekvens"}:
                reasons.append("versalisering_eller_inkonsekvens_skyddas")
            elif _only_removes_footnote_marker(suggestion.old, suggestion.new):
                reasons.append("fotnotsmarkör_ska_ignoreras")
            elif _contains_bible_reference(suggestion.old) or _contains_bible_reference(suggestion.new):
                reasons.append("bibelhänvisning_ska_ignoreras")
            elif _only_changes_layout_spacing(suggestion.old, suggestion.new):
                reasons.append("layoutmellanslag_ska_ignoreras")
            elif _only_changes_case(suggestion.old, suggestion.new):
                reasons.append("ren_versaländring_ska_ignoreras")
            elif _changes_capitalized_word(suggestion.old, suggestion.new):
                reasons.append("egennamn_eller_titel_skyddas")
            elif _changes_genitive_chain(suggestion.old, suggestion.new):
                reasons.append("möjlig_genitivkedja_skyddas")
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
