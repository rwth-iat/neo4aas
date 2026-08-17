"""Parse ECLASS XML 5.0 (OntoML) dictionary documents into typed models.

ECLASS BASIC/ADVANCED exports nest the payload under
``<dic:eclass_dictionary> / <ontoml:ontoml>`` as ``ontoml:class`` /
``ontoml:property`` / ``ontoml:datatype`` elements. Parsing is done
namespace-agnostically (matching on local tag names) since the documents mix
several ISO/ECLASS namespaces and the prefixes are not load-bearing here.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree as ET

from aas_mapping.eclass.models import (
    EclassClass,
    EclassProperty,
    EclassUnit,
    LangText,
    ParseResult,
)

logger = logging.getLogger(__name__)

_XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"


def _local(tag: str) -> str:
    """Strip a ``{namespace}`` prefix from an ElementTree tag."""
    return tag.rsplit("}", 1)[-1]


# ``ITEM_CLASS_CASE_OF_Type`` classes (and their superclass refs) carry a dictionary
# qualifier wedged into the IRDI between the ICD/OPI and the item code, e.g.
# ``0173-1---BASIC_1_1#01-AGX566#005`` for the canonical IRDI ``0173-1#01-AGX566#005``.
# Strip it so these reference classes share their id with the real class CD (enabling
# dedup and correct isCaseOf / HAS_PROPERTY linking).
_IRDI_DICT_QUALIFIER = re.compile(r"^(\d{4}-\d)---[^#]+(#.*)$")


def _canonical_irdi(value: str | None) -> str | None:
    """Strip a dictionary qualifier from an ECLASS IRDI; pass other values through."""
    if not value:
        return value
    m = _IRDI_DICT_QUALIFIER.match(value)
    return f"{m.group(1)}{m.group(2)}" if m else value


def _strip_xsi_prefix(value: str | None) -> str | None:
    """``ontoml:REAL_MEASURE_TYPE_Type`` -> ``REAL_MEASURE_TYPE_Type``."""
    if value is None:
        return None
    return value.rsplit(":", 1)[-1]


def _find(elem: ET.Element, name: str) -> ET.Element | None:
    for child in elem:
        if _local(child.tag) == name:
            return child
    return None


def _lang_texts(parent: ET.Element | None, child_name: str) -> list[LangText]:
    """Collect ``<label>`` / ``<text>`` children as LangText entries."""
    if parent is None:
        return []
    out: list[LangText] = []
    for child in parent:
        if _local(child.tag) == child_name:
            out.append(LangText(language=child.get("language_code", ""), text=child.text or ""))
    return out


def _is_true(elem: ET.Element, name: str) -> bool:
    node = _find(elem, name)
    return node is not None and (node.text or "").strip().lower() == "true"


def _described_by_refs(described_by: ET.Element | None) -> list[str]:
    """Property IRDIs a class is described by (ADVANCED class→property links).

    Kept in document order; ECLASS emits ``<property>`` entries already sorted by
    ``order_number``.
    """
    if described_by is None:
        return []
    return [
        _canonical_irdi(child.get("property_ref", ""))
        for child in described_by
        if _local(child.tag) == "property"
    ]


class EclassOntomlParser:
    """Parses ECLASS OntoML dictionary documents into a :class:`ParseResult`."""

    def parse_string(self, xml: str) -> ParseResult:
        return self._parse_root(ET.fromstring(xml))

    def parse_file(self, path: str | Path) -> ParseResult:
        return self._parse_root(ET.parse(str(path)).getroot())

    def parse_units_string(self, xml: str) -> list[EclassUnit]:
        return self._parse_units_root(ET.fromstring(xml))

    def parse_units_file(self, path: str | Path) -> list[EclassUnit]:
        return self._parse_units_root(ET.parse(str(path)).getroot())

    def parse_directory(
        self, package_dir: str | Path, sg_filter: Iterable[int] | None = None
    ) -> ParseResult:
        """Parse a package directory: ``*_SG_*.xml`` segments plus UnitsML.

        ``sg_filter`` (e.g. ``{13, 27}``) restricts to those segment numbers.
        The companion ``*UnitsML*.xml`` file (if present) supplies the units.
        """
        wanted = set(sg_filter) if sg_filter is not None else None
        merged = ParseResult()
        for xml_path in sorted(Path(package_dir).rglob("*_SG_*.xml")):
            sg = _segment_number(xml_path.name)
            if wanted is not None and sg not in wanted:
                continue
            logger.info("parsing %s", xml_path.name)
            part = self.parse_file(xml_path)
            merged.classes.extend(part.classes)
            merged.properties.extend(part.properties)
        for units_path in sorted(Path(package_dir).rglob("*UnitsML*.xml")):
            logger.info("parsing %s", units_path.name)
            merged.units.extend(self.parse_units_file(units_path))
        return merged

    # -- internals ---------------------------------------------------------

    def _parse_root(self, root: ET.Element) -> ParseResult:
        result = ParseResult()
        for elem in root.iter():
            tag = _local(elem.tag)
            # ``id`` distinguishes real ``ontoml:class`` / ``ontoml:property``
            # definitions from the bare ``<property property_ref=...>`` link
            # descriptors nested in a class's ``<described_by>``.
            if not elem.get("id"):
                continue
            if tag == "class":
                result.classes.append(self._parse_class(elem))
            elif tag == "property":
                result.properties.append(self._parse_property(elem))
        return result

    def _parse_class(self, elem: ET.Element) -> EclassClass:
        superclass = _find(elem, "its_superclass")
        hier = _find(elem, "hierarchical_position")
        return EclassClass(
            irdi=_canonical_irdi(elem.get("id", "")),
            preferred_name=_lang_texts(_find(elem, "preferred_name"), "label"),
            definition=_lang_texts(_find(elem, "definition"), "text"),
            superclass_irdi=_canonical_irdi(superclass.get("class_ref")) if superclass is not None else None,
            hierarchical_position=(hier.text if hier is not None else None),
            is_deprecated=_is_true(elem, "is_deprecated"),
            property_refs=_described_by_refs(_find(elem, "described_by")),
        )

    def _parse_units_root(self, root: ET.Element) -> list[EclassUnit]:
        units: list[EclassUnit] = []
        for elem in root.iter():
            if _local(elem.tag) == "eClassUnit":
                unit = self._parse_unit(elem)
                if unit is not None:
                    units.append(unit)
        return units

    def _parse_unit(self, elem: ET.Element) -> EclassUnit | None:
        irdi = None
        for child in elem:
            if _local(child.tag) == "CodeListValue" and child.get("codeListName") == "IRDI":
                irdi = child.get("unitCodeValue")
                break
        if not irdi:
            return None
        pref = _lang_texts(_find(elem, "preferred_name"), "label")
        short = _lang_texts(_find(elem, "short_name"), "label")
        return EclassUnit(
            irdi=irdi,
            preferred_name=pref[0].text if pref else "",
            short_name=short[0].text if short else "",
        )

    def _parse_property(self, elem: ET.Element) -> EclassProperty:
        domain = _find(elem, "domain")
        data_type = unit_irdi = None
        if domain is not None:
            data_type = _strip_xsi_prefix(domain.get(_XSI_TYPE))
            unit = _find(domain, "unit")
            if unit is not None:
                unit_irdi = _canonical_irdi(unit.get("unit_ref"))
        return EclassProperty(
            irdi=_canonical_irdi(elem.get("id", "")),
            preferred_name=_lang_texts(_find(elem, "preferred_name"), "label"),
            short_name=_lang_texts(_find(elem, "short_name"), "label"),
            definition=_lang_texts(_find(elem, "definition"), "text"),
            data_type=data_type,
            unit_irdi=unit_irdi,
            is_deprecated=_is_true(elem, "is_deprecated"),
        )


def _segment_number(filename: str) -> int | None:
    """Extract NN from ``..._SG_NN.xml`` (None if absent)."""
    stem = Path(filename).stem
    marker = "_SG_"
    idx = stem.rfind(marker)
    if idx == -1:
        return None
    tail = stem[idx + len(marker):]
    return int(tail) if tail.isdigit() else None
