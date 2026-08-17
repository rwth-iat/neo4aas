"""Turn parsed ECLASS models into AAS ConceptDescriptions.

Each ECLASS class / property / unit becomes a ``ConceptDescription`` whose
semantics live in the CD's **native** fields — ``display_name`` (the ECLASS
preferred name) and ``description`` (the definition). Class hierarchy
(``superclass``) is carried on ``ConceptDescription.isCaseOf`` as an external
reference to the parent CD. Class->property and property->unit assignments have
no CD slot; they are modelled downstream as ``:HAS_PROPERTY`` / ``:HAS_UNIT``
graph edges (see ``derive_edges.py``).
"""

from __future__ import annotations

import re

import basyx.aas.model as model

from neo4aas.eclass.models import (
    EclassClass,
    EclassProperty,
    EclassUnit,
    LangText,
)

# AAS string-length constraints on the native lang-string types.
_MAX_DISPLAY_NAME = 64      # MultiLanguageNameType
_MAX_DESCRIPTION = 1023     # MultiLanguageTextType

_ID_SHORT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def _global_ref(value: str) -> model.ExternalReference:
    return model.ExternalReference((model.Key(model.KeyTypes.GLOBAL_REFERENCE, value),))


def _sanitize_id_short(text: str | None) -> str | None:
    """Return a valid AAS idShort (``[A-Za-z][A-Za-z0-9_]*``) or None.

    Unit short names with symbols (``m/s``, ``°C``, ``%``) sanitize to None.
    """
    if not text:
        return None
    s = text.strip()
    return s if _ID_SHORT_RE.match(s) else None


def _lang_dict(texts: list[LangText], max_len: int) -> dict[str, str]:
    """Build a ``{language: text}`` dict, truncated and stripped of empties.

    Later entries win on duplicate languages; empty texts are dropped (the AAS
    lang-string types require min length 1).
    """
    out: dict[str, str] = {}
    for lt in texts:
        text = (lt.text or "").strip()
        if text and lt.language:
            out[lt.language] = text[:max_len]
    return out


def _display_name(texts: list[LangText], fallback: str) -> model.MultiLanguageNameType:
    """display_name carries the ECLASS preferred name; fall back to the IRDI."""
    d = _lang_dict(texts, _MAX_DISPLAY_NAME)
    if not d:
        d = {"en": fallback[:_MAX_DISPLAY_NAME]}
    return model.MultiLanguageNameType(d)


def _description(texts: list[LangText]) -> model.MultiLanguageTextType | None:
    d = _lang_dict(texts, _MAX_DESCRIPTION)
    return model.MultiLanguageTextType(d) if d else None


class SpecCdWriter:
    """Builds AAS ConceptDescriptions from parsed ECLASS models."""

    def property_to_cd(self, prop: EclassProperty) -> model.ConceptDescription:
        return model.ConceptDescription(
            id_=prop.irdi,
            display_name=_display_name(prop.preferred_name, prop.irdi),
            description=_description(prop.definition),
        )

    def class_to_cd(self, cls: EclassClass) -> model.ConceptDescription:
        return model.ConceptDescription(
            id_=cls.irdi,
            id_short=f"eclass_{cls.hierarchical_position}" if cls.hierarchical_position else None,
            is_case_of=[_global_ref(cls.superclass_irdi)] if cls.superclass_irdi else None,
            display_name=_display_name(cls.preferred_name, cls.irdi),
            description=_description(cls.definition),
        )

    def unit_to_cd(self, unit: EclassUnit) -> model.ConceptDescription:
        pref = [LangText("de", unit.preferred_name)] if unit.preferred_name else []
        return model.ConceptDescription(
            id_=unit.irdi,
            id_short=_sanitize_id_short(unit.short_name),
            display_name=_display_name(pref, unit.irdi),
        )
