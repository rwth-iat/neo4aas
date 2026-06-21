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


# Registry applied by apply_fixers(). Append new fixers here.
DEFAULT_FIXERS: list[AASFixer] = [LangStringFixer()]


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
