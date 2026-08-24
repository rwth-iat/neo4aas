"""Templates: the path is the authority, and both trees are read."""

import pytest

from neo4aas.vocabulary.extract import VocabularyRecords
from neo4aas.vocabulary.templates import extract_templates, parse_version


@pytest.mark.parametrize("parts,version,family", [
    (("Digital nameplate", "3", "0", "1"), "3.0.1", ("Digital nameplate",)),
    (("Digital nameplate", "3", "0"), "3.0.0", ("Digital nameplate",)),
    (("Carbon Footprint", "0", "9"), "0.9.0", ("Carbon Footprint",)),
    (("Digital Battery Passport", "5_Product Condition", "1", "0", "2"), "1.0.2",
     ("Digital Battery Passport", "5_Product Condition")),
])
def test_parse_version_follows_the_submodules_own_rule(parts, version, family):
    assert parse_version(parts) == (version, family)


def test_deprecated_templates_are_read_too(corpus_templates):
    """Skipping ``deprecated/`` costs ~75 000 declared matches — vendors ship stale
    identifiers, and "who ships a superseded release" is unanswerable without them."""
    templates, records = extract_templates(corpus_templates)
    statuses = {t.status for t in templates}
    assert statuses == {"published", "deprecated"}
    assert sum(t.status == "deprecated" for t in templates) > 0
    assert not records.failures


def test_templates_declare_shapes_and_no_instances(corpus_templates):
    templates, records = extract_templates(corpus_templates)
    assert len(templates) > 50
    assert all(t.shape_keys for t in templates)
    # A template shape belongs to no vendor: nobody "ships" a template.
    assert not records.vendor_shapes
    # And every declared shape is in the shared vocabulary, so a structure and a
    # template can be compared shape for shape.
    declared = {key for t in templates for key in t.shape_keys}
    assert declared <= set(records.shapes)


def test_a_json_and_its_aasx_sibling_are_one_template(corpus_templates):
    """The two are the same content in two serializations; counting both doubles it."""
    templates, _ = extract_templates(corpus_templates)
    sources = [t.source_path for t in templates]
    assert len(sources) == len(set(f"{s}#{i}" for i, s in enumerate(sources)))
    aasx = [s for s in sources if s.endswith(".aasx")]
    assert 0 < len(aasx) < 20, "only the leaves with no JSON sibling"


def test_templates_declare_supplemental_semantic_ids_too(corpus_templates):
    """A template declaring a supplemental id is the same statement a vendor makes.

    The IDTA tree is where most of them live — hundreds of distinct values against a
    handful in the instances — mostly IEC CDD IRDIs beside an ECLASS primary.
    """
    _, records = extract_templates(corpus_templates, VocabularyRecords())
    values = {value for _, value in records.supplemental_declarations}
    assert len(values) > 100
    assert any(v.startswith("0112/") for v in values)
    assert values <= set(records.semantic_ids), \
        "every declared supplemental must have its :SemanticId node"
