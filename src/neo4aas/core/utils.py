import hashlib
import json
import logging
import re
import time
from collections import abc
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# An IRDI starts with a 4-digit ICD and ends in "#<version>". Two families appear in
# real AAS data and both must be version-stripped:
#   ECLASS   "0173-1#02-AAO677#002"          (ICD "0173-")
#   IEC CDD  "0112/2///61360_4#AAF120#001"   (ICD "0112/")
# The version suffix is what differs between releases of the *same* concept, so the
# version-agnostic identity is the IRDI without it. _IRDI_PREFIX_RE gates on the ICD —
# now with either separator, since matching only "\d{4}-" left every IEC CDD semanticId
# versioned and therefore unmatchable across releases — so plain URIs (which may also
# contain a '#') are left untouched.
_IRDI_PREFIX_RE = re.compile(r"^\d{4}[-/]")
_IRDI_VERSION_RE = re.compile(r"#\d+$")

# ECLASS-CDP serves the same IRDIs as URLs with the two '#' separators replaced by
# '-', e.g. "https://api.eclass-cdp.com/0173-1-01-AHX837-002" is the IRDI
# "0173-1#01-AHX837#002". Normalizing the URL back to canonical IRDI form lets the
# CDP-URL and plain-IRDI references of the same property share one base.
_ECLASS_CDP_HOST_RE = re.compile(r"^https?://api\.eclass-cdp\.com/\s*(.+?)\s*$")
_ECLASS_CDP_DASHED_RE = re.compile(r"^(\d{4}-\d)-(\d{2}-[0-9A-Za-z]+)-(\d+)$")

# The IDTA Battery Passport templates write an IRDI inside a URN: the wrapper states
# outright that the payload is one, so unwrapping it invents nothing. Misspellings of
# the scheme (urn:iridi:, urn:idi:) are deliberately *not* matched — repairing a typo
# would be inventing an identifier, and urn:samm: is a different identifier system
# altogether (Eclipse SAMM aspect models), not an IRDI in a wrapper.
_URN_IRDI_RE = re.compile(r"(?i)^urn:irdi:(.+)$")


def canonical_irdi(value: str | None) -> str | None:
    """Strip a known wrapper off an IRDI, leaving canonical ``ICD#item#version`` form.

    Some sources do not write an IRDI plainly. The wrappers seen in real AAS data are
    a URN (``urn:irdi:0173-1#02-AAN920#003``) and the ECLASS-CDP URL, which serves the
    same IRDIs with the two ``#`` separators replaced by ``-`` — and, in the IDTA
    templates, sometimes with a stray space after the host or with the separators left
    as ``#`` after all.

    Only whole, unambiguous wrappers are removed. Anything else is returned unchanged,
    so a caller can canonicalise every value it sees.
    """
    if not value:
        return value
    urn = _URN_IRDI_RE.match(value)
    if urn:
        value = urn.group(1).strip()
    host = _ECLASS_CDP_HOST_RE.match(value)
    if host:
        payload = host.group(1)
        # Already canonical apart from the host: the separators were never encoded.
        if "#" in payload:
            return payload
        dashed = _ECLASS_CDP_DASHED_RE.match(payload)
        if dashed:
            return f"{dashed.group(1)}#{dashed.group(2)}#{dashed.group(3)}"
    return value


def irdi_base(value: str) -> str:
    """Return the version-agnostic IRDI base: the IRDI minus its trailing '#<version>'.

    Known wrappers are removed first by :func:`canonical_irdi` — an ECLASS-CDP URL or a
    ``urn:irdi:`` prefix — so a wrapped IRDI collapses to the same base as the plain
    IRDI of that property, rather than splitting the concept in two.

    **This function's output is stored** (``Reference.target_id_base``,
    ``ConceptDescription.id_base``, ``SemanticId.base``, ``Concept.irdi_base``) *and*
    recomputed by query-time callers. Changing what it normalizes therefore invalidates
    those stored values: a caller computes the new base and misses the stale stored one,
    which is worse than either state alone. Such a change must ship with a re-import.

    Non-IRDI values (no 4-digit ICD prefix, or no trailing numeric version) are
    returned unchanged, so callers can store the base unconditionally — for a
    non-IRDI id the base simply equals the id and equality behaves as before.
    """
    value = canonical_irdi(value)
    if value and _IRDI_PREFIX_RE.match(value) and _IRDI_VERSION_RE.search(value):
        return _IRDI_VERSION_RE.sub("", value)
    return value


def is_irdi(value: str | None) -> bool:
    """Whether a value is an IRDI at all — i.e. carries the 4-digit ICD prefix.

    :func:`irdi_base` deliberately returns a non-IRDI unchanged so callers can
    store a base unconditionally. A caller that must *reject* a malformed
    identifier needs the question asked directly: the IEC CDD export ships 1 547
    properties scraped without their prefix (``AAE930#001``), which no amount of
    version-stripping makes joinable.
    """
    canonical = canonical_irdi(value)
    return bool(canonical and _IRDI_PREFIX_RE.match(canonical))


def rm_quotes(s: str):
    return s.replace("'", "")


def add_quotes(s: str):
    return f"'{s}'"


def is_iterable(obj):
    return isinstance(obj, abc.Iterable) and not isinstance(obj, (str, bytes, bytearray))


@dataclass
class UploadStats:
    overall_start_time: float
    total_files: int = 0
    total_batches: int = 0
    batch_size: int = 0
    total_nodes_created: int = 0
    total_relationships_created: int = 0
    total_time: float = 0.0
    total_processing_time: float = 0.0
    total_node_creation_time: float = 0.0
    total_relationship_creation_time: float = 0.0

    def __init__(self):
        super().__init__()
        self.overall_start_time = time.time()

    def finish(self):
        self.total_time = time.time() - self.overall_start_time
        logger.info(f"Total processing time: {self.total_time:.2f} seconds")
        logger.info(f"Total nodes created: {self.total_nodes_created}")
        logger.info(f"Total relationships created: {self.total_relationships_created}")
        logger.info(f"Total batches processed: {self.total_batches}")
        logger.info(f"Total files processed: {self.total_files}")
        logger.info(f"Total processing time: {self.total_processing_time:.2f} seconds")
        logger.info(f"Total node creation time: {self.total_node_creation_time:.2f} seconds")
        logger.info(f"Total relationship creation time: {self.total_relationship_creation_time:.2f} seconds")


NEO4J_INTERNAL_NODE_KEYS = frozenset({"uid", "hash", "target_id", "target_id_base", "id_base"})


def hash_dict_obj(obj: dict) -> str:
    # Sort keys to ensure deterministic hash
    json_string = json.dumps(obj, sort_keys=True)
    return hashlib.sha256(json_string.encode()).hexdigest()


def cypher_as_list(expression: str) -> str:
    """Wrap a flattened list-property access so Cypher sees a list whatever the encoding.

    A single-entry flattened list is stored as a scalar (see
    ``Neo4jModelConfig.compact_single_entry_lists``: a one-element array costs ~4x the same
    value as a scalar in Neo4j's dynamic array store). ``apoc.convert.toList`` normalises
    both encodings — scalar to ``[scalar]``, list unchanged, ``null`` to ``null`` — so a
    store written before or after the change reads the same way.
    """
    return f"apoc.convert.toList({expression})"
