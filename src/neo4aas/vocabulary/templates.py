"""IDTA submodel templates → ``:Template`` and ``:DECLARES_SHAPE``.

The source is the ``submodel-templates`` submodule of the corpus, and **both**
``published/`` and ``deprecated/`` are read: dropping the deprecated tree costs
around 75 000 declared matches, because vendors keep shipping identifiers from
superseded releases. That is precisely the "who is shipping stale identifiers"
traversal, and it is unanswerable if the deprecated templates are not in the graph.

What the tree actually looks like
---------------------------------

``<status>/<Family>[/<Part>]/<version>/<revision>[/<bugfix>]/<files>``, where the
leaf holds a ``.json`` Environment, an ``.aasx`` of the same content, a PDF and a
README. Three things about it decide the parsing:

* **The path is the authority for family, version and status.** The submodel ``id``
  encodes a version only usually (``…/AssetInterfacesDescription`` carries none,
  ``…/AssetInterfacesMappingConfiguration/1/0/`` has a trailing slash,
  ``https://admin-shell-io/…`` has a typo in the host), ``administration`` is
  present on two thirds of submodels and never carries the bugfix digit, and the
  auto-generated root ``README.md`` still labels moved families as published.
* **A ``.json`` and its sibling ``.aasx`` are the same template**, so the ``.aasx``
  is read only where no ``.json`` stem matches — eight leaves, one of them
  (``deprecated/Hierarchical Structures…/1/0``) with no JSON at all.
* **Not everything that parses is AAS.** One file is an AASX Package Explorer form
  definition (``{"$type": "Options", "Records": […]}``); several are UTF-8 with a
  BOM. Presence of a ``submodels`` key is the test, and the BOM is stripped.

Duplicate submodel ``id`` values are pervasive inside ``published/`` (the base,
the bugfix and the ``_forAASMetamodelV3.1`` copy share one), so the id is *not* the
template key. The path is.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from neo4aas.core.io import read_bytes
from neo4aas.vocabulary.extract import (
    VocabularyRecords,
    loads_aas_json,
    walk_submodel,
)

logger = logging.getLogger(__name__)

#: Top-level directories of the templates submodule, and the ``status`` they imply.
STATUS_DIRS: tuple[str, ...] = ("published", "deprecated")


@dataclass
class TemplateRecord:
    """One template submodel. Keyed by its path, because ids repeat across versions."""

    template_id: str
    family: str
    version: str
    status: str
    source_path: str
    submodel_id: str | None
    kind: str | None
    shape_keys: set[str] = field(default_factory=set)


def parse_version(parts: tuple[str, ...]) -> tuple[str, tuple[str, ...]]:
    """Split a family path into ``(version, family_parts)``.

    Mirrors the submodule's own ``check_templates.parse_version``: trailing
    all-digit segments are the version, three of them meaning
    ``version.revision.bugfix`` and two meaning ``version.revision.0``.
    """
    digits: list[str] = []
    while parts and parts[-1].isdigit() and len(digits) < 3:
        digits.insert(0, parts[-1])
        parts = parts[:-1]
    while len(digits) < 3:
        digits.append("0")
    return ".".join(digits), parts


def _template_files(root: Path) -> Iterator[tuple[str, Path]]:
    """``(status, file)`` for every template document worth parsing.

    A ``.aasx`` is yielded only when its leaf directory holds no ``.json`` of the
    same stem — the two are the same template in two serializations.
    """
    for status in STATUS_DIRS:
        base = root / status
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.json")):
            yield status, path
        for path in sorted(base.rglob("*.aasx")):
            if not path.with_suffix(".json").exists():
                yield status, path


def _environments(path: Path) -> Iterator[dict]:
    if path.suffix.lower() == ".json":
        env = loads_aas_json(read_bytes(path))
        if isinstance(env, dict) and "submodels" in env:
            yield env
        else:
            logger.info("Skipping %s — parses, but is not an AAS Environment", path)
        return
    from neo4aas.core.serialization.aasx import AasxToNeo4jImporter

    yield from AasxToNeo4jImporter().iter_environments(path)


def extract_templates(root: str | Path,
                      into: VocabularyRecords | None = None
                      ) -> tuple[list[TemplateRecord], VocabularyRecords]:
    """Walk the templates tree into ``:Template`` records and their shapes.

    The shapes and the containment pairs go into the *same* vocabulary as the
    instance side, with no vendor attached: a template shape nobody ships ends up
    with ``occurrences = 0``, and a template containment pair no vendor implements
    ends up as a ``:CONTAINS`` edge with ``in_template = true`` and no support
    arrays. That is the "declared but unused" half of the conformance question, and
    it is now answerable per *relation* rather than only per shape.
    """
    root = Path(root)
    records = into if into is not None else VocabularyRecords()
    templates: list[TemplateRecord] = []

    for status, path in _template_files(root):
        relative = path.relative_to(root)
        version, family_parts = parse_version(relative.parts[1:-1])
        family = family_parts[0] if family_parts else relative.parts[1]
        try:
            environments = list(_environments(path))
        except Exception as exc:                                # noqa: BLE001 — boundary
            records.failures.append((str(path), f"{type(exc).__name__}: {exc}"))
            continue

        for env in environments:
            submodels = env.get("submodels")
            if isinstance(submodels, dict):
                submodels = [submodels]
            for index, submodel in enumerate(submodels or ()):
                if not isinstance(submodel, dict):
                    continue
                template = TemplateRecord(
                    template_id=f"{relative.as_posix()}#{index}",
                    family=family,
                    version=version,
                    status=status,
                    source_path=relative.as_posix(),
                    submodel_id=submodel.get("id"),
                    kind=submodel.get("kind"),
                )
                walk = walk_submodel(submodel)
                for use in walk.uses:
                    template.shape_keys.add(use.shape.key)
                    records.add_shape(use.shape, None, use.category, occurrences=0)
                    if use.shape.semantic_id:
                        records.add_semantic_id(use.shape.semantic_id,
                                                use.key_type, use.ref_type)
                    records.add_supplementals(use)
                records.template_contains.update(walk.contains)
                templates.append(template)
        records.documents += 1

    return templates, records
