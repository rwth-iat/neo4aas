"""Extraction is pure, so it is tested without a database.

Two properties carry the whole design and are checked here rather than in the
integration tests, because if they fail nothing downstream can be right:

* **keys are pure functions of their input** — same element, same key, on any
  machine and any run. Without that, ``MERGE`` creates a second node for the same
  shape and every count moves on re-import.
* **nothing that varies per product is extracted** — no values, no ids, no
  descriptions. The graph is the corpus's vocabulary, not its contents.
"""

import gzip
import json

import pytest

from neo4aas.vocabulary.extract import (
    Shape,
    SubmodelGroup,
    VocabularyRecords,
    collect_files,
    extract_file,
    extract_paths,
    iter_environments,
    semantic_id_of,
    shape_key,
    submodel_group_key,
    vendor_of,
    walk_submodel,
)

PROPERTY = {"modelType": "Property", "idShort": "Voltage", "valueType": "xs:float",
            "value": "230", "semanticId": {"type": "ExternalReference",
                                           "keys": [{"type": "GlobalReference",
                                                     "value": "0173-1#02-BAF016#006"}]}}


# -- keys ----------------------------------------------------------------------


def test_shape_key_is_stable_and_precomputed():
    """The key must not depend on anything but the four components."""
    assert shape_key("a", "b", "c", "d") == shape_key("a", "b", "c", "d")
    assert Shape.of(PROPERTY).key == shape_key(
        "Voltage", "0173-1#02-BAF016#006", "Property", "xs:float")


def test_keys_are_pinned_to_known_digests():
    """Pinned literals, because the key is a *storage* key.

    Changing how it is computed does not break a test somewhere — it orphans every
    node in every database that already holds this vocabulary, silently, and the
    idempotence property with it. If this test fails, the change is a migration.
    """
    assert shape_key("Voltage", None, "Property", "xs:float") == \
        "f8f80f9e7e04bdc4138711f7a625a3a3697ab7efb6e0df86f4ed9839082f336f"


def test_shape_key_separates_the_components():
    """No ambiguity when a component contains the separator or is empty."""
    assert shape_key("a,b", None, "P", None) != shape_key("a", "b", "P", None)
    assert shape_key(None, "x", "P", None) != shape_key("x", None, "P", None)


def test_shape_key_does_not_normalise():
    """Verbatim values, by design: normalising would merge two real vocabularies."""
    assert shape_key("Voltage", None, "P", None) != shape_key("voltage", None, "P", None)
    assert shape_key(" Voltage", None, "P", None) != shape_key("Voltage", None, "P", None)


def test_value_is_not_part_of_the_shape():
    """The one rule: nothing that varies per product enters the graph."""
    other = dict(PROPERTY, value="400")
    assert Shape.of(other).key == Shape.of(PROPERTY).key


def test_id_and_description_are_not_part_of_the_layout():
    a = {"modelType": "Submodel", "idShort": "Nameplate", "id": "urn:a",
         "description": [{"language": "en", "text": "one"}], "submodelElements": [PROPERTY]}
    b = {"modelType": "Submodel", "idShort": "Nameplate", "id": "urn:b",
         "description": [{"language": "de", "text": "zwei"}], "submodelElements": [PROPERTY]}
    assert walk_submodel(a).contains == walk_submodel(b).contains
    assert walk_submodel(a).roots == walk_submodel(b).roots


# -- walking -------------------------------------------------------------------


def test_semantic_id_is_the_first_key_value():
    assert semantic_id_of(PROPERTY) == "0173-1#02-BAF016#006"
    assert semantic_id_of({"semanticId": {"keys": []}}) is None
    assert semantic_id_of({}) is None


def test_multi_language_property_value_is_not_a_child():
    """A LangString list has no ``modelType``; that guard is the whole rule."""
    mlp = {"modelType": "MultiLanguageProperty", "idShort": "Name",
           "value": [{"language": "en", "text": "Pump"}]}
    submodel = {"modelType": "Submodel", "idShort": "N", "submodelElements": [mlp]}
    walk = walk_submodel(submodel)
    assert walk.elements == 1
    # The one pair is the submodel's merged parent reaching its top level; the
    # LangStrings underneath contribute nothing.
    assert list(walk.contains) == [(walk.group.key, walk.uses[1].shape.key)]


def test_nested_containers_are_walked():
    inner = {"modelType": "Property", "idShort": "Deep", "valueType": "xs:string"}
    collection = {"modelType": "SubmodelElementCollection", "idShort": "C",
                  "value": [inner]}
    entity = {"modelType": "Entity", "idShort": "E", "statements": [collection]}
    submodel = {"modelType": "Submodel", "idShort": "S", "submodelElements": [entity]}
    walk = walk_submodel(submodel)
    assert walk.elements == 3
    assert [use.shape.model_type for use in walk.uses] == [
        "Submodel", "Entity", "SubmodelElementCollection", "Property"]
    shapes = {use.shape.model_type: use.shape.key for use in walk.uses}
    assert walk.contains == {
        (walk.group.key, shapes["Entity"]): 1,
        (shapes["Entity"], shapes["SubmodelElementCollection"]): 1,
        (shapes["SubmodelElementCollection"], shapes["Property"]): 1,
    }
    assert list(walk.roots) == [shapes["Entity"]]


def test_operation_variables_are_walked():
    variable = {"value": {"modelType": "Property", "idShort": "In", "valueType": "xs:int"}}
    operation = {"modelType": "Operation", "idShort": "Op", "inputVariables": [variable]}
    submodel = {"modelType": "Submodel", "idShort": "S", "submodelElements": [operation]}
    assert walk_submodel(submodel).elements == 2


def test_the_submodels_own_shape_is_never_a_containment_parent():
    """A layout must not carry a serial number.

    Several vendors put a per-product token in the submodel's idShort — SICK ships
    ``Nameplate_0226…`` — so rooting containment at the submodel's *shape* would
    give every product its own subtree and defeat the merge: 1 670 321 edges on
    the t100k tier against ~2 195. The shape is still recorded, because it declares
    the submodel's semanticId, and the top level is still a count."""
    a = {"modelType": "Submodel", "idShort": "Nameplate_0226303791c8",
         "submodelElements": [PROPERTY]}
    b = {"modelType": "Submodel", "idShort": "Nameplate_04d188107e6b",
         "submodelElements": [PROPERTY]}
    walk_a, walk_b = walk_submodel(a), walk_submodel(b)
    assert walk_a.roots == walk_b.roots
    assert walk_a.uses[0].shape.model_type == "Submodel"
    assert walk_a.uses[0].shape.id_short == "Nameplate_0226303791c8"
    assert walk_a.uses[0].shape.key != walk_b.uses[0].shape.key
    parents = {p for p, _ in walk_a.contains} | {p for p, _ in walk_b.contains}
    assert not parents & {walk_a.uses[0].shape.key, walk_b.uses[0].shape.key}


def test_the_top_level_is_parented_by_a_merged_group():
    """Two products of the same submodel type give **one** parent and one edge."""
    a = {"modelType": "Submodel", "idShort": "Nameplate_0226303791c8",
         "submodelElements": [PROPERTY]}
    b = {"modelType": "Submodel", "idShort": "Nameplate_04d188107e6b",
         "submodelElements": [PROPERTY]}
    walk_a, walk_b = walk_submodel(a), walk_submodel(b)
    assert walk_a.group is not None
    assert walk_a.group == walk_b.group
    assert walk_a.group.id_short_base == "nameplate"
    assert walk_a.contains == walk_b.contains == {
        (walk_a.group.key, walk_a.uses[1].shape.key): 1}


def test_a_group_key_is_not_a_shape_key():
    """Two keys, two payloads. A reader holding one can always tell which it has."""
    group = submodel_group_key("nameplate", "urn:sem")
    assert group != shape_key("nameplate", "urn:sem", "Submodel", None)
    assert group != shape_key(None, "urn:sem", "Submodel", None)
    assert len(group) == 64


def test_both_halves_of_the_group_key_are_load_bearing():
    """``semanticId`` alone strands the submodels that declare none; ``id_short_base``
    alone merges submodels that are genuinely different."""
    sem_a = {"type": "ExternalReference",
             "keys": [{"type": "GlobalReference", "value": "urn:tmpl-a"}]}
    sem_b = {"type": "ExternalReference",
             "keys": [{"type": "GlobalReference", "value": "urn:tmpl-b"}]}
    same_name_other_template = (
        SubmodelGroup.of({"idShort": "Nameplate", "semanticId": sem_a}),
        SubmodelGroup.of({"idShort": "Nameplate", "semanticId": sem_b}),
    )
    assert same_name_other_template[0] != same_name_other_template[1]

    # No semanticId at all: id_short_base still keys it, so its top level is not
    # left parentless — 1 156 submodels on the t100k tier.
    nameless_template = SubmodelGroup.of({"idShort": "Nameplate_0226303791c8"})
    assert nameless_template is not None
    assert nameless_template.id_short_base == "nameplate"
    assert nameless_template.semantic_id is None

    # No idShort either: nothing left to merge on, so the pre-existing behaviour
    # stands — roots counted, no edge. The only case that cannot merge.
    assert SubmodelGroup.of({"modelType": "Submodel"}) is None


def test_a_submodel_that_cannot_merge_keeps_the_old_behaviour():
    submodel = {"modelType": "Submodel", "submodelElements": [PROPERTY]}
    walk = walk_submodel(submodel)
    assert walk.group is None
    assert walk.contains == {}
    assert list(walk.roots) == [walk.uses[1].shape.key]


# -- folding -------------------------------------------------------------------


def test_containment_records_both_supports():
    """Occurrences and submodel support are different numbers, and both matter:
    the second is what tells a schema relation from a one-off."""
    from neo4aas.vocabulary.extract import extract_environment

    element = {"modelType": "Property", "idShort": "Doc", "valueType": "xs:string"}
    collection = {"modelType": "SubmodelElementCollection", "idShort": "Docs",
                  "value": [element, dict(element)]}
    env = {"submodels": [{"modelType": "Submodel", "idShort": "S",
                          "submodelElements": [collection]}]}
    records = VocabularyRecords()
    extract_environment(env, "v", records)
    extract_environment(env, "v", records)

    keys = {rec.shape.id_short: key for key, rec in records.shapes.items()}
    pair = ("v", keys["Docs"], keys["Doc"])
    assert records.contains[pair] == 4           # two children, two submodels
    assert records.contains_submodels[pair] == 2

    # The merged parent's own edge is counted the same way: one top-level
    # occurrence per submodel, two submodels.
    group, = records.submodel_groups.values()
    assert records.contains[("v", group.key, keys["Docs"])] == 2
    assert records.contains_submodels[("v", group.key, keys["Docs"])] == 2


def test_merge_is_associative_over_counters():
    env = {"submodels": [{"modelType": "Submodel", "idShort": "S",
                          "submodelElements": [PROPERTY]}]}
    from neo4aas.vocabulary.extract import extract_environment

    left, right, both = VocabularyRecords(), VocabularyRecords(), VocabularyRecords()
    extract_environment(env, "a", left)
    extract_environment(env, "b", right)
    extract_environment(env, "a", both)
    extract_environment(env, "b", both)
    merged = VocabularyRecords().merge(left).merge(right)
    assert merged.shapes.keys() == both.shapes.keys()
    assert merged.vendor_shapes == both.vendor_shapes
    assert merged.contains == both.contains
    assert merged.submodel_groups == both.submodel_groups
    assert sum(r.occurrences for r in merged.shapes.values()) == \
           sum(r.occurrences for r in both.shapes.values())


def test_vendor_comes_from_the_first_directory_below_the_root(tmp_path):
    root = tmp_path / "vendors"
    path = root / "festo" / "loose" / "a.json"
    path.parent.mkdir(parents=True)
    path.write_text("{}")
    assert vendor_of(path, root, True, "unknown") == "festo"
    assert vendor_of(path, root, False, "given") == "given"


def test_collect_files_is_sorted_and_limited(tmp_path):
    for name in ("b.json", "a.json", "c.xml", "ignored.txt"):
        (tmp_path / name).write_text("{}")
    files = collect_files([tmp_path])
    assert [p.name for _, p in files] == ["a.json", "b.json", "c.xml"]
    assert len(collect_files([tmp_path], limit=2)) == 2


def test_stratify_interleaves_vendors(tmp_path):
    for vendor in ("a", "b"):
        directory = tmp_path / vendor
        directory.mkdir()
        for index in range(3):
            (directory / f"{index}.json").write_text("{}")
    names = [f"{p.parent.name}{p.name}" for _, p in
             collect_files([tmp_path], stratify=True)]
    assert names == ["a0.json", "b0.json", "a1.json", "b1.json", "a2.json", "b2.json"]


# -- real corpus ---------------------------------------------------------------


@pytest.mark.parametrize("fixture", ["json_files", "xml_files", "aasx_files"])
def test_every_corpus_serialization_extracts(fixture, request):
    """One rule, three serializations: gzipped JSON, gzipped XML, gzipped AASX."""
    files = request.getfixturevalue(fixture)
    records = extract_paths(files, vendor="v")
    assert not records.failures
    assert records.submodels > 0
    assert records.elements > records.submodels
    assert 0 < len(records.shapes) < records.elements


def test_layout_is_far_smaller_than_the_documents(json_files, xml_files):
    """The merged containment graph is bounded by the vocabulary, not the corpus."""
    records = extract_paths(list(json_files) + list(xml_files), vendor="v")
    pairs = {(parent, child) for _, parent, child in records.contains}
    assert 0 < len(pairs) < records.elements


def test_shapes_are_reused_hundreds_of_times(json_files, xml_files):
    """The measurement the design rests on: ~300x reuse, so shapes << elements."""
    records = extract_paths(list(json_files) + list(xml_files), vendor="v")
    assert records.elements / len(records.shapes) > 5


def test_gzip_is_detected_by_magic_bytes_not_by_name(tmp_path, json_files):
    """A corpus may strip the .gz suffix; the bytes are what say it is compressed."""
    raw = json_files[0].read_bytes()
    disguised = tmp_path / "still-gzipped.json"
    disguised.write_bytes(raw)
    assert next(iter_environments(disguised))


def test_truncated_source_is_reported_not_raised(tmp_path, json_files):
    """Real corpora contain truncated downloads. Report, skip, continue."""
    raw = gzip.decompress(json_files[0].read_bytes())
    broken = tmp_path / "truncated.json"
    broken.write_bytes(raw[: len(raw) // 2])
    records = extract_file(broken, "v")
    assert len(records.failures) == 1
    assert not records.shapes


def test_a_document_that_is_not_aas_yields_no_vocabulary(tmp_path):
    """Parses fine, is not an Environment — must not invent shapes."""
    path = tmp_path / "form.json"
    path.write_bytes(gzip.compress(json.dumps({"$type": "Options",
                                               "Records": [1, 2]}).encode()))
    records = extract_file(path, "v")
    assert not records.failures and not records.shapes


def test_extraction_in_worker_processes_matches_serial(json_files):
    serial = extract_paths(json_files, vendor="v")
    parallel = extract_paths(json_files, vendor="v", workers=2)
    assert serial.shapes.keys() == parallel.shapes.keys()
    assert serial.contains == parallel.contains
    assert serial.vendor_shapes == parallel.vendor_shapes


# -- supplemental semanticIds --------------------------------------------------

def _ref(value, key_type="GlobalReference", ref_type="ExternalReference"):
    return {"type": ref_type, "keys": [{"type": key_type, "value": value}]}


SUPPLEMENTED = dict(PROPERTY, supplementalSemanticIds=[
    _ref("0112/2///61987#ABA286#001"),
    _ref("https://api.eclass-cdp.com/0173-1-02-AAW001-001"),
])


def _env(*elements):
    return {"submodels": [{"modelType": "Submodel", "idShort": "S",
                           "submodelElements": list(elements)}]}


def _folded(*elements):
    from neo4aas.vocabulary.extract import extract_environment

    records = VocabularyRecords()
    extract_environment(_env(*elements), "v", records)
    return records


def test_supplemental_ids_become_semantic_ids_and_their_own_edge():
    records = _folded(SUPPLEMENTED)
    assert set(records.semantic_ids) == {
        "0173-1#02-BAF016#006",
        "0112/2///61987#ABA286#001",
        "https://api.eclass-cdp.com/0173-1-02-AAW001-001",
    }
    key = Shape.of(SUPPLEMENTED).key
    assert records.supplemental_declarations == {
        (key, "0112/2///61987#ABA286#001"),
        (key, "https://api.eclass-cdp.com/0173-1-02-AAW001-001"),
    }


def test_a_supplemental_id_does_not_change_the_shape_key():
    """The migration guard.

    Supplementals hang off the shape; they are never part of its identity. Putting
    them in the key would orphan every node in every database already holding this
    vocabulary — see :func:`test_keys_are_pinned_to_known_digests`.
    """
    assert Shape.of(SUPPLEMENTED).key == Shape.of(PROPERTY).key


def test_two_elements_differing_only_in_supplementals_are_one_shape():
    other = dict(PROPERTY, supplementalSemanticIds=[_ref("0173-1#02-OTHER#001")])
    records = _folded(SUPPLEMENTED, other)
    assert len(records.shapes) == 2, "the Submodel's own shape, and the Property's"
    key = Shape.of(PROPERTY).key
    assert {value for shape, value in records.supplemental_declarations
            if shape == key} == {
        "0112/2///61987#ABA286#001",
        "https://api.eclass-cdp.com/0173-1-02-AAW001-001",
        "0173-1#02-OTHER#001",
    }


def test_the_fold_does_not_depend_on_the_order_supplementals_are_written_in():
    reversed_order = dict(PROPERTY, supplementalSemanticIds=list(
        reversed(SUPPLEMENTED["supplementalSemanticIds"])))
    assert (_folded(SUPPLEMENTED).supplemental_declarations
            == _folded(reversed_order).supplemental_declarations)


def test_key_and_reference_types_of_a_supplemental_are_recorded():
    """Recorded on the node, never used for matching — vendors disagree on them."""
    element = dict(PROPERTY, supplementalSemanticIds=[
        _ref("0112/2///61987#ABA286#001", "ConceptDescription", "ModelReference")])
    record = _folded(element).semantic_ids["0112/2///61987#ABA286#001"]
    assert record.key_types == {"ConceptDescription"}
    assert record.ref_types == {"ModelReference"}


def test_one_value_used_both_ways_is_a_single_semantic_id():
    shared = "0173-1#02-BAF016#006"
    other = {"modelType": "Property", "idShort": "Other", "valueType": "xs:string",
             "supplementalSemanticIds": [_ref(shared)]}
    records = _folded(PROPERTY, other)
    assert len([v for v in records.semantic_ids if v == shared]) == 1
    assert (Shape.of(other).key, shared) in records.supplemental_declarations


def test_an_eclass_cdp_url_supplemental_joins_on_the_canonical_irdi():
    """`id_base` already normalises the dash-encoded CDP URL, which is the form
    harting ships supplementals in — so it lands on the plain ECLASS concept."""
    records = _folded(SUPPLEMENTED)
    assert (records.semantic_ids["https://api.eclass-cdp.com/0173-1-02-AAW001-001"].value_base
            == "0173-1#02-AAW001")


def test_a_supplemental_without_a_usable_key_is_ignored():
    element = dict(PROPERTY, supplementalSemanticIds=[
        {"type": "ExternalReference", "keys": []},
        {"type": "ExternalReference", "keys": [{"type": "GlobalReference", "value": ""}]},
        "not-a-reference",
    ])
    assert not _folded(element).supplemental_declarations


def test_a_single_supplemental_arriving_as_a_dict_is_read():
    """The XML converter emits a one-item list as a bare dict."""
    element = dict(PROPERTY, supplementalSemanticIds=_ref("0112/2///61987#ABA286#001"))
    assert len(_folded(element).supplemental_declarations) == 1
