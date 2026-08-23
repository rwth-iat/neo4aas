"""Concepts: the dictionary half of the vocabulary.

A ``:Concept`` is one entry of a semantic dictionary — an ECLASS class, an ECLASS
property, an ECLASS unit, or a ConceptDescription that shipped inside an AAS file.
It is what makes a vendor's ``Betriebsspannung`` and another's
``OperatingVoltage`` the same thing: 92 % of shapes are vendor-private, and the
only thing bridging them is the concept both point at.

Two sources, both idempotent, both merged on ``irdi``:

**ECLASS OntoML** — parsed by :class:`neo4aas.eclass.ontoml_parser.EclassOntomlParser`,
which is basyx-free (the vocabulary importer never needs the SDK). Class hierarchy
comes from the zero-padded 8-digit ``hierarchical_position`` — ECLASS's *CodedName*
— by **prefix containment**, not from the ``superclass`` field: ``27210101`` sits
under ``27210100`` under ``27210000`` under ``27000000``.

**ConceptDescriptions embedded in the AAS files** — for a large share of distinct
semanticIds these are the corpus's only definition source, so they are harvested
during instance extraction and merged here. They never overwrite an ECLASS
definition; they fill fields ECLASS left empty (see
:data:`SOURCE_PRECEDENCE`).

Every concept records where it came from, because "we have no definition for this
identifier" and "ECLASS defines it" are different answers to the same question.
"""

from __future__ import annotations

from typing import Any, Iterable, Iterator

from neo4aas.core.utils import irdi_base

#: Higher wins when the same IRDI arrives from two sources. A dictionary export is
#: authoritative; a CD embedded in a vendor file is a copy of unknown vintage. The
#: two dictionaries rank *equally* — they publish disjoint identifier spaces
#: (ECLASS under ICD ``0173``, IEC CDD under ``0112``) so they never contest a
#: field, and equal precedence is not a win, which keeps a second import a no-op.
SOURCE_PRECEDENCE: dict[str, int] = {"aas": 1, "eclass": 2, "iec_cdd": 2}

#: Languages preferred when an ECLASS/IEC 61360 label exists in several.
LANGUAGE_PREFERENCE: tuple[str, ...] = ("en", "de")

_IEC61360 = "DataSpecificationIec61360"


def _lang_text(entries: Any) -> str | None:
    """Pick one string from a LangString list: English, then German, then the first.

    Deterministic on purpose — an order-dependent pick would make two imports of the
    same corpus produce different property values.
    """
    if isinstance(entries, str):
        return entries or None
    if isinstance(entries, dict):
        entries = [entries]
    if not isinstance(entries, list):
        return None
    texts: list[tuple[str, str]] = []
    for entry in entries:
        if isinstance(entry, dict):
            text = entry.get("text")
            if isinstance(text, str) and text:
                texts.append((str(entry.get("language") or ""), text))
        elif isinstance(entry, str) and entry:
            texts.append(("", entry))
    if not texts:
        return None
    for wanted in LANGUAGE_PREFERENCE:
        for language, text in texts:
            if language.lower().startswith(wanted):
                return text
    return texts[0][1]


def _model_lang_text(entries: Iterable[Any]) -> str | None:
    """:class:`neo4aas.eclass.models.LangText` list → one string, same preference."""
    return _lang_text([{"language": e.language, "text": e.text} for e in entries or ()])


def coded_name_level(coded_name: str | None) -> int | None:
    """ECLASS hierarchy level 1–4 of a CodedName, or None when it is not one.

    A CodedName is four two-digit pairs, right-padded with ``00``: ``27000000`` is
    a segment (level 1), ``27210100`` a group (level 3).
    """
    if not coded_name or len(coded_name) != 8 or not coded_name.isdigit():
        return None
    pairs = [coded_name[i:i + 2] for i in range(0, 8, 2)]
    level = 0
    for pair in pairs:
        if pair == "00":
            break
        level += 1
    return level or None


def coded_name_parent(coded_name: str | None) -> str | None:
    """The CodedName one level up, or None at level 1.

    Pure prefix containment: ``27210101 -> 27210100 -> 27210000 -> 27000000``.
    """
    level = coded_name_level(coded_name)
    if not level or level < 2:
        return None
    return coded_name[: (level - 1) * 2].ljust(8, "0")


def concept(irdi: str, source: str, *, kind: str = "concept", **fields: Any) -> dict:
    """One ``:Concept`` row. ``irdi_base`` is what semanticIds are joined on."""
    row = {
        "irdi": irdi,
        "irdi_base": irdi_base(irdi),
        "source": source,
        "kind": kind,
        "coded_name": None,
        "level": None,
        "preferred_name": None,
        "definition": None,
        "unit": None,
        "data_type": None,
        "deprecated": False,
    }
    row.update(fields)
    row["precedence"] = SOURCE_PRECEDENCE.get(source, 0)
    return row


# -- AAS-embedded ConceptDescriptions ------------------------------------------


def concept_from_description(description: Any) -> dict | None:
    """A ConceptDescription found inside an AAS file → a ``:Concept`` row.

    Prefers the IEC 61360 data specification, which is where preferred name,
    definition, unit and data type actually live, and falls back to the CD's own
    ``displayName`` / ``description``.
    """
    if not isinstance(description, dict):
        return None
    identifier = description.get("id")
    if not isinstance(identifier, str) or not identifier:
        return None

    content = _iec61360_content(description)
    preferred = _lang_text(content.get("preferredName")) if content else None
    definition = _lang_text(content.get("definition")) if content else None
    unit = content.get("unit") if content else None
    data_type = content.get("dataType") if content else None

    return concept(
        identifier,
        "aas",
        kind="description",
        preferred_name=preferred or _lang_text(description.get("displayName")),
        definition=definition or _lang_text(description.get("description")),
        unit=unit if isinstance(unit, str) and unit else None,
        data_type=data_type if isinstance(data_type, str) and data_type else None,
    )


def _iec61360_content(description: dict) -> dict:
    specs = description.get("embeddedDataSpecifications")
    if isinstance(specs, dict):
        specs = [specs]
    for spec in specs or ():
        if not isinstance(spec, dict):
            continue
        content = spec.get("dataSpecificationContent")
        if isinstance(content, dict) and content.get("modelType") == _IEC61360:
            return content
    return {}


# -- ECLASS ---------------------------------------------------------------------


def parse_eclass(package_dir: str, segments: Iterable[int] | None = None):
    """Parse an ECLASS OntoML export. Returns the parser's ``ParseResult``.

    Imported here rather than at module scope so that the rest of the vocabulary
    importer stays usable when ECLASS is not being loaded.
    """
    from neo4aas.eclass.ontoml_parser import EclassOntomlParser

    return EclassOntomlParser().parse_directory(package_dir, segments)


def concepts_from_eclass(result) -> Iterator[dict]:
    """``ParseResult`` → ``:Concept`` rows for classes, properties and units."""
    units = {unit.irdi: unit for unit in result.units}
    unit_bases = {irdi_base(unit.irdi): unit for unit in result.units}

    for cls in result.classes:
        yield concept(
            cls.irdi,
            "eclass",
            kind="class",
            coded_name=cls.hierarchical_position,
            level=coded_name_level(cls.hierarchical_position),
            preferred_name=_model_lang_text(cls.preferred_name),
            definition=_model_lang_text(cls.definition),
            deprecated=bool(cls.is_deprecated),
        )

    for prop in result.properties:
        unit = units.get(prop.unit_irdi or "") or unit_bases.get(irdi_base(prop.unit_irdi or ""))
        yield concept(
            prop.irdi,
            "eclass",
            kind="property",
            preferred_name=_model_lang_text(prop.preferred_name),
            definition=_model_lang_text(prop.definition),
            unit=(unit.short_name or unit.preferred_name or None) if unit else None,
            data_type=prop.data_type,
            deprecated=bool(prop.is_deprecated),
        )

    for unit in result.units:
        yield concept(
            unit.irdi,
            "eclass",
            kind="unit",
            preferred_name=unit.preferred_name or unit.short_name or None,
            unit=unit.short_name or None,
        )


def property_class_pairs(result) -> Iterator[tuple[str, str]]:
    """``(property_irdi_base, class_irdi_base)`` from ECLASS's ``described_by`` block.

    This is the one ``:BROADER`` edge that is *not* CodedName prefix containment: a
    property's broader concept is the class that describes it. Without it a
    semanticId that resolves to a property has no siblings, and query expansion —
    the traversal the whole graph exists for — has nothing to expand through.
    Joined on the version-stripped base, because ``described_by`` references carry
    versions that need not match the property's own.
    """
    for cls in result.classes:
        class_base = irdi_base(cls.irdi)
        for ref in cls.property_refs:
            if ref:
                yield irdi_base(ref), class_base
