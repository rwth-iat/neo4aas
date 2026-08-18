"""Typed models for parsed ECLASS dictionary elements.

These are plain data carriers produced by the OntoML parser and consumed by the
ConceptDescription writer. They intentionally hold only the fields the M1
ingestion targets (ITEM_CLASS, PROPERTY, UNIT_OF_MEASURE per IEC 61360) — not
the full OntoML model.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class LangText:
    """One language-tagged string (an ECLASS ``label`` / ``text``)."""

    language: str
    text: str


@dataclass
class EclassClass:
    """An ECLASS classification class (OntoML ``ontoml:class``)."""

    irdi: str
    preferred_name: list[LangText] = field(default_factory=list)
    definition: list[LangText] = field(default_factory=list)
    superclass_irdi: str | None = None
    hierarchical_position: str | None = None
    is_deprecated: bool = False
    #: IRDIs of properties applicable to this class (filled by edge derivation,
    #: not present inside the class element itself).
    property_refs: list[str] = field(default_factory=list)


@dataclass
class EclassProperty:
    """An ECLASS property / feature definition (OntoML ``ontoml:property``)."""

    irdi: str
    preferred_name: list[LangText] = field(default_factory=list)
    short_name: list[LangText] = field(default_factory=list)
    definition: list[LangText] = field(default_factory=list)
    #: The ``domain`` xsi:type local name, e.g. ``REAL_MEASURE_TYPE_Type``.
    data_type: str | None = None
    #: IRDI of the unit of measure, when the domain carries one.
    unit_irdi: str | None = None
    is_deprecated: bool = False


@dataclass
class EclassUnit:
    """An ECLASS unit of measure (from the UnitsML companion file)."""

    irdi: str
    short_name: str = ""
    preferred_name: str = ""


@dataclass
class ParseResult:
    """Everything pulled from one or more parsed dictionary documents."""

    classes: list[EclassClass] = field(default_factory=list)
    properties: list[EclassProperty] = field(default_factory=list)
    units: list[EclassUnit] = field(default_factory=list)
