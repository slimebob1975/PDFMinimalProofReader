from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


@dataclass(slots=True)
class PdfLine:
    page: int
    printed_page: str | None
    column: Literal["vänster", "höger"]
    line_no: int
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    kind: str = "body"


@dataclass(slots=True)
class TextUnit:
    unit_id: str
    document: str
    chapter: int | None
    verse: int | None
    verse_inferred: bool
    reference: str
    page_start: int
    page_end: int
    printed_page_start: str | None
    column_start: str
    line_start: int
    line_end: int
    text: str
    source_lines: list[str] = field(default_factory=list)


class ModelSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unit_id: str = Field(description="Exakt ID för den textenhet där felet finns.")
    old: str = Field(description="Minsta exakta textsegment som ska ersättas.")
    new: str = Field(description="Minsta korrigerade ersättningstext.")
    error_type: Literal[
        "stavning",
        "grammatik",
        "syftning",
        "kommatering",
        "interpunktion",
        "särskrivning_sammanskrivning",
        "dubblerat_saknat_ord",
        "böjning_kongruens",
        "preposition",
        "versalisering",
        "inkonsekvens",
        "annat_entydigt_språkfel",
    ]
    motivation: str = Field(description="Kort saklig förklaring på svenska.")
    confidence: Literal["hög", "medel", "låg"]


class SuggestionEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    suggestions: list[ModelSuggestion]


@dataclass(slots=True)
class ValidatedSuggestion:
    suggestion_id: int
    unit_id: str
    document: str
    chapter: int | None
    verse: int | None
    reference: str
    page: int
    column: str
    line_start: int
    line_end: int
    original_context: str
    old: str
    new: str
    error_type: str
    motivation: str
    confidence: str
    status: str = ""
    warnings: list[str] = field(default_factory=list)
