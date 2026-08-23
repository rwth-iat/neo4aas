"""IEC CDD: the second dictionary source, beside ECLASS.

The IEC Common Data Dictionary publishes the classes, properties and units of
IEC 61360-4, IEC 61987, IEC 62683 and friends under the ICD ``0112`` — the other
half of the identifier space vendors point their ``semanticId``\\ s at. The corpus
ships it as one JSONL file per dictionary, one record per line, already flattened
out of the CDD's Notes views.

It differs from ECLASS in exactly one thing that matters to this graph, and the
difference is an improvement: **hierarchy is explicit**. ECLASS encodes it
positionally in the 8-digit CodedName and this importer recovers it by prefix
containment (:func:`neo4aas.vocabulary.concepts.coded_name_parent`); IEC CDD names
the parent outright in ``superclass_irdi``. So a CDD concept carries no
``coded_name``, and its ``:BROADER`` edges come from the export's own references
rather than from a naming convention.

Everything else is the same shape as ECLASS: ``:Concept`` rows merged on ``irdi``,
joined to declared semanticIds on the version-stripped :func:`irdi_base`, with
properties linked to the classes that use them so query expansion has something to
expand through.

Two defects of the export are decided here rather than passed on:

* **Relative IRDIs.** 1 547 properties of the 61987 dictionary were scraped without
  their ICD prefix (``AAE930#001``). Such a value joins nothing, and the prefix
  could only be *guessed* from ``published_in``. They are skipped and counted —
  1 511 of them already exist in full form elsewhere in the export, so dropping
  them loses almost nothing, while inventing an identifier would poison the join
  key the whole graph rests on.
* **One IRDI published several times.** 7 117 identifiers appear more than once
  with differing content (up to eight times), 6 445 of them differing in
  ``revision`` — the CDD revises inside a version. The highest revision wins, ties
  broken on ``unid``, so the choice cannot depend on the order lines were read.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

from neo4aas.core.io import read_bytes
from neo4aas.core.utils import irdi_base, is_irdi

#: The dictionary source name, and its rank in
#: :data:`neo4aas.vocabulary.concepts.SOURCE_PRECEDENCE`.
SOURCE = "iec_cdd"

#: The export truncates ``_type`` to nine characters. Mapped to the ``:Concept``
#: kinds the rest of the graph uses; ECLASS contributes ``class``/``property``/
#: ``unit``, and CDD adds the two vocabulary kinds ECLASS has no equivalent of.
KINDS: dict[str, str] = {
    "class": "class",
    "property": "property",
    "unit": "unit",
    "terminolo": "terminology",
    "enumerati": "enumeration",
}

#: Files a directory scan picks up.
SUFFIX = ".jsonl"


@dataclass
class CddResult:
    """One parse. ``records`` is keyed by IRDI, one chosen record per identifier."""

    records: dict[str, dict] = field(default_factory=dict)
    #: ``(path:line, error)`` for every line that would not parse. Reported, never
    #: raised — a dictionary with one bad line must still load the other 41 410.
    failures: list[tuple[str, str]] = field(default_factory=list)
    #: Records dropped for carrying an identifier with no ICD prefix.
    skipped_relative: int = 0
    #: Records dropped because a higher revision of the same IRDI was present.
    superseded_records: int = 0

    @property
    def kinds(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for record in self.records.values():
            kind = KINDS.get(record.get("_type"), "concept")
            counts[kind] = counts.get(kind, 0) + 1
        return counts


# -- parsing -------------------------------------------------------------------


def _revision_rank(record: dict) -> tuple[int, str]:
    """Sort key deciding which copy of a repeated IRDI is kept.

    ``revision`` is numeric in the export but stored as a string, so it is compared
    as an integer where it parses. ``unid`` breaks the tie: it is the CDD's own
    document id, unique and stable, which makes the pick independent of the order
    the lines arrived in — the only reason two runs could otherwise disagree.
    """
    revision = record.get("revision")
    try:
        rank = int(str(revision))
    except (TypeError, ValueError):
        rank = -1
    return rank, str(record.get("unid") or "")


def _iter_lines(path: Path) -> Iterator[tuple[int, str]]:
    """Numbered, non-empty lines of a JSONL file, gzip-transparently."""
    text = read_bytes(path).decode("utf-8-sig")
    for number, line in enumerate(text.splitlines(), start=1):
        if line.strip():
            yield number, line


def _collect_files(paths: Iterable[str | Path]) -> list[Path]:
    """Every ``.jsonl`` under the given files/directories, sorted."""
    found: list[Path] = []
    for entry in paths:
        path = Path(entry)
        if path.is_dir():
            found.extend(sorted(p for p in path.rglob("*") if p.is_file()
                                and p.name.replace(".gz", "").endswith(SUFFIX)))
        else:
            found.append(path)
    return found


def parse_iec_cdd(paths: Iterable[str | Path]) -> CddResult:
    """Parse IEC CDD JSONL files or directories of them into one :class:`CddResult`.

    The whole export is ~80 MB and 41 411 records, so it is materialised — unlike
    the ECLASS OntoML tree, which is gigabytes and has to stream.
    """
    result = CddResult()
    for path in _collect_files(paths):
        for number, line in _iter_lines(path):
            try:
                record = json.loads(line)
            except ValueError as exc:                       # noqa: PERF203 — boundary
                result.failures.append((f"{path}:{number}", f"{type(exc).__name__}: {exc}"))
                continue
            _absorb(result, record)
    return result


def _absorb(result: CddResult, record: Any) -> None:
    if not isinstance(record, dict):
        return
    identifier = record.get("irdi")
    if not isinstance(identifier, str) or not identifier:
        return
    if not is_irdi(identifier):
        result.skipped_relative += 1
        return
    existing = result.records.get(identifier)
    if existing is None:
        result.records[identifier] = record
        return
    result.superseded_records += 1
    if _revision_rank(record) > _revision_rank(existing):
        result.records[identifier] = record


# -- concepts ------------------------------------------------------------------


def _unit_index(result: CddResult) -> dict[str, dict]:
    """Unit records by version-stripped base — a property may cite another version."""
    return {irdi_base(irdi): record
            for irdi, record in result.records.items()
            if record.get("_type") == "unit"}


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def concepts_from_iec_cdd(result: CddResult) -> Iterator[dict]:
    """``:Concept`` rows for every record the export defines."""
    from neo4aas.vocabulary.concepts import concept

    units = _unit_index(result)

    for identifier, record in result.records.items():
        kind = KINDS.get(record.get("_type"), "concept")
        yield concept(
            identifier,
            SOURCE,
            kind=kind,
            preferred_name=_text(record.get("preferred_name_en")),
            definition=_text(record.get("definition_en")),
            unit=_unit_of(record, kind, units),
            data_type=_text(record.get("data_type")),
            # ``status_level`` is 'Superseded' for half the export: that marks an
            # older *version* of a live concept, not a withdrawn one. Only the
            # explicit flag deprecates.
            deprecated=bool(record.get("is_deprecated")),
        )


def _unit_of(record: dict, kind: str, units: dict[str, dict]) -> str | None:
    """A unit's own symbol, or for a property the symbol of the unit it cites."""
    if kind == "unit":
        return _text(record.get("short_name_en")) or _text(record.get("preferred_name_en"))
    symbol = _text(record.get("unit"))
    if symbol:
        return symbol
    referenced = units.get(irdi_base(_text(record.get("unit_irdi")) or ""))
    if not referenced:
        return None
    return (_text(referenced.get("short_name_en"))
            or _text(referenced.get("preferred_name_en")))


# -- hierarchy -----------------------------------------------------------------


def class_broader_pairs(result: CddResult) -> Iterator[tuple[str, str]]:
    """``(class_base, superclass_base)`` — the direct parent, nothing more.

    ``higher_level_classes`` lists the whole ancestor chain. Emitting it would make
    every ancestor a *direct* parent, flattening the hierarchy the field describes
    and giving a query expansion no level to stop at.
    """
    for identifier, record in result.records.items():
        if record.get("_type") != "class":
            continue
        superclass = _text(record.get("superclass_irdi"))
        if superclass and is_irdi(superclass):
            child, parent = irdi_base(identifier), irdi_base(superclass)
            if child != parent:
                yield child, parent


def property_class_pairs(result: CddResult) -> Iterator[tuple[str, str]]:
    """``(property_base, class_base)`` — a property's broader concept is its class.

    Read from **both** directions, because the export fills them inconsistently: a
    property names its classes in ``applicable_class_irdis`` (86.6 % of properties),
    and a class names its own in ``properties_own``.

    ``properties_inherited`` and ``properties_tree`` are deliberately ignored: they
    repeat what :func:`class_broader_pairs` already carries, and attaching them
    would give a deep class every ancestor's properties as if they were its own.
    """
    for identifier, record in result.records.items():
        kind = record.get("_type")
        if kind == "property":
            for class_irdi in record.get("applicable_class_irdis") or ():
                pair = _pair(identifier, class_irdi)
                if pair:
                    yield pair
        elif kind == "class":
            for reference in record.get("properties_own") or ():
                property_irdi = (reference.get("irdi")
                                 if isinstance(reference, dict) else reference)
                pair = _pair(property_irdi, identifier)
                if pair:
                    yield pair


def _pair(property_irdi: Any, class_irdi: Any) -> tuple[str, str] | None:
    if not (is_irdi(property_irdi) and is_irdi(class_irdi)):
        return None
    property_base, class_base = irdi_base(property_irdi), irdi_base(class_irdi)
    return None if property_base == class_base else (property_base, class_base)
