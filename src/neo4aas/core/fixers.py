"""Pre-store fixers that repair non-conformant AAS data on import.

A *fixer* repairs a known class of defect in an AAS JSON dict — either a single
Identifiable or a whole Environment — **in place**, returning how many fixes it
applied. Fixers are collected in ``DEFAULT_FIXERS`` and run by ``apply_fixers()``
at every JSON import entry point, so both bulk upload and the ``Neo4jObjectStore``
add/commit path are covered before anything reaches Neo4j.

To add a fixer: subclass ``AASFixer``, implement ``fix()``, and append an instance
to ``DEFAULT_FIXERS``.

First use case — ``LangStringFixer``: normalize language tags to BCP 47
(e.g. ``en_US`` -> ``en-US``). Some source data (and AASX exporters) emit the
underscore locale form, which basyx's strict reader rejects, breaking read-back.
"""

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


def _walk(node: Any, visit: Callable[[dict], None]) -> None:
    """Depth-first walk of a JSON tree, calling ``visit`` on every dict node."""
    if isinstance(node, dict):
        visit(node)
        for value in node.values():
            _walk(value, visit)
    elif isinstance(node, list):
        for item in node:
            _walk(item, visit)


class AASFixer(ABC):
    """Base class for an import-time AAS data fixer."""

    #: Short identifier used in the fix report / logs.
    name: str = "fixer"

    @abstractmethod
    def fix(self, data: dict) -> int:
        """Repair ``data`` in place; return the number of fixes applied."""
        raise NotImplementedError


class LangStringFixer(AASFixer):
    """Normalize language tags in LangStrings to BCP 47 (``_`` -> ``-``).

    A LangString in AAS JSON is any object carrying a string ``language`` field
    (MultiLanguageProperty ``value``, ``description``, ``displayName``, …). basyx's
    strict reader rejects underscore locale tags such as ``en_US``; rewriting them
    to ``en-US`` keeps the stored data round-trippable.
    """

    name = "language-tag-bcp47"

    def fix(self, data: dict) -> int:
        count = 0

        def visit(node: dict) -> None:
            nonlocal count
            lang = node.get("language")
            if isinstance(lang, str) and "_" in lang:
                node["language"] = lang.replace("_", "-")
                count += 1

        _walk(data, visit)
        return count


class NumericValueTypeFixer(AASFixer):
    """Relax a numeric ``valueType`` whose ``value`` is not actually a number.

    Some source data (e.g. Phoenix Contact JSON) declares ``valueType: xs:float``
    but stores a stringified JSON array such as
    ``"[{\"level\":\"max\",\"value\":0.2},{\"level\":\"min\",\"value\":1.5}]"`` — a
    min/max range smuggled into a scalar Property. basyx then fails to coerce the
    string to a float and the whole file is rejected. Switching such a Property's
    ``valueType`` to ``xs:string`` keeps the value (as text) and lets the file parse.
    """

    name = "numeric-value-type-coerce"

    #: xs numeric primitives whose lexical form must parse as a Python float.
    _NUMERIC = {
        "xs:float", "xs:double", "xs:decimal", "xs:integer", "xs:int", "xs:long",
        "xs:short", "xs:byte", "xs:unsignedInt", "xs:unsignedLong", "xs:unsignedShort",
        "xs:unsignedByte", "xs:nonNegativeInteger", "xs:positiveInteger",
        "xs:nonPositiveInteger", "xs:negativeInteger",
    }

    def fix(self, data: dict) -> int:
        count = 0

        def visit(node: dict) -> None:
            nonlocal count
            value = node.get("value")
            if node.get("valueType") not in self._NUMERIC or not isinstance(value, str) or value == "":
                return
            try:
                float(value)
            except ValueError:
                node["valueType"] = "xs:string"
                count += 1

        _walk(data, visit)
        return count


class IdShortFixer(AASFixer):
    """Sanitize ``idShort`` to the characters AAS allows (Constraint AASd-002).

    Some source data (e.g. ABB nameplates) uses spaces or symbols in an idShort like
    ``"ABB_PMGA11* Control Unit_310320262206"``. basyx's strict reader rejects these on
    read-back (``AASd-002``), which breaks reconstruction of the whole object — and, in a
    Repository, the entire shells/submodels listing.

    AASd-002 is ``[a-zA-Z][a-zA-Z0-9_]+``: the allowed set is letters, digits and ``_``
    only — a hyphen is **not** legal (basyx: "must contain only letters, digits and
    underscore") — and the first character must be a letter. Illegal characters are
    therefore replaced with ``_``, and an idShort that does not start with a letter is
    prefixed with ``x`` (lossless, unlike stripping the leading characters).

    Note: an idShort referenced by a ModelReference *idShort path* (a key ``value``) is not
    rewritten in lockstep, so such a reference could desync. The supplier data here references
    submodels by id and semantics by external IRDI, not by idShort path, so this is safe in
    practice; revisit if idShort-path references appear.
    """

    name = "idshort-aasd002"

    _ILLEGAL = re.compile(r"[^A-Za-z0-9_]")

    def fix(self, data: dict) -> int:
        count = 0

        def visit(node: dict) -> None:
            nonlocal count
            value = node.get("idShort")
            if isinstance(value, str) and value:
                fixed = self._ILLEGAL.sub("_", value)
                if not fixed[0].isalpha():
                    fixed = "x" + fixed
                if fixed != value:
                    node["idShort"] = fixed
                    count += 1

        _walk(data, visit)
        return count


class EmptyLangStringFixer(AASFixer):
    """Drop LangString entries whose ``text`` is empty (or missing).

    A LangString (``{"language": "..", "text": ".."}`` in description / displayName /
    MultiLanguageProperty value) must carry non-empty text: basyx's ``MultiLanguageTextType``
    enforces a minimum length of 1, so an empty-text entry makes read-back raise and breaks
    reconstruction of the whole object (e.g. a Repository ``/submodels`` page 500s). Such an
    entry conveys nothing, so it is removed from its containing list; a list left empty is
    valid (the field is optional).
    """

    name = "empty-langstring"

    def fix(self, data: dict) -> int:
        count = 0

        def is_empty_langstring(item: Any) -> bool:
            return isinstance(item, dict) and "language" in item and not item.get("text")

        def clean(node: Any) -> None:
            nonlocal count
            if isinstance(node, dict):
                for value in node.values():
                    clean(value)
            elif isinstance(node, list):
                kept = []
                for item in node:
                    if is_empty_langstring(item):
                        count += 1
                        continue
                    clean(item)
                    kept.append(item)
                node[:] = kept

        clean(data)
        return count


# Registry applied by apply_fixers(). Append new fixers here.
DEFAULT_FIXERS: list[AASFixer] = [
    LangStringFixer(), NumericValueTypeFixer(), IdShortFixer(), EmptyLangStringFixer(),
]


@dataclass
class FixReport:
    """Summary of fixes applied to one import."""

    counts: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def apply_fixers(data: dict, fixers: list[AASFixer] = DEFAULT_FIXERS) -> FixReport:
    """Run every fixer over ``data`` (in place) and return a ``FixReport``."""
    report = FixReport()
    for fixer in fixers:
        n = fixer.fix(data)
        if n:
            report.counts[fixer.name] = n
            logger.info("Fixer '%s' applied %d fix(es) on import", fixer.name, n)
    return report
