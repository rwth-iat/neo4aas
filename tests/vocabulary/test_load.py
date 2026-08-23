"""The loader, against a live Neo4j and real corpus files.

The test that matters is :func:`test_reimport_changes_nothing`. Every other
property of this design — bounded node count, honest occurrence counters,
resolution that survives a reload — depends on the import being a function of the
corpus rather than of how many times it has been run.
"""

import pytest

from neo4aas.vocabulary.extract import extract_paths
from neo4aas.vocabulary.load import VocabularyLoader
from neo4aas.vocabulary.schema import (
    KEY_CONSTRAINTS,
    VOCABULARY_LABELS,
    VOCABULARY_RELATIONSHIPS,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def loader(neo4j_params):
    import neo4j

    driver = neo4j.GraphDatabase.driver(
        neo4j_params["uri"], auth=(neo4j_params["user"], neo4j_params["password"]),
        notifications_min_severity="OFF")
    instance = VocabularyLoader(driver, batch_size=5_000)
    instance.create_schema()
    instance.clear()
    yield instance
    instance.clear()
    driver.close()


@pytest.fixture
def corpus_records(json_files, xml_files):
    """A small, real, mixed-serialization, multi-vendor fold."""
    records = extract_paths(json_files, vendor="festo")
    return records.merge(extract_paths(xml_files, vendor="sick"))


def _snapshot(loader: VocabularyLoader) -> dict:
    """Everything an import may not change when it is run a second time."""
    return {
        "nodes": loader.node_counts(),
        "relationships": loader.relationship_counts(),
        "occurrences": {
            r["k"]: r["o"] for r in loader.run(
                "MATCH (s:Shape) RETURN s.shape_key AS k, s.occurrences AS o")
        },
        "uses": {
            (r["v"], r["k"]): r["o"] for r in loader.run(
                "MATCH (v:Vendor)-[u:USES]->(s:Shape) "
                "RETURN v.name AS v, s.shape_key AS k, u.occurrences AS o")
        },
        "contains": {
            (r["p"], r["c"]): (r["names"], r["occ"], r["sub"]) for r in loader.run(
                "MATCH (p:Shape)-[e:CONTAINS]->(c:Shape) "
                "RETURN p.shape_key AS p, c.shape_key AS c, e.vendor_names AS names, "
                "e.vendor_occurrences AS occ, e.vendor_submodels AS sub")
        },
    }


# -- the primary acceptance test ------------------------------------------------


def test_reimport_changes_nothing(loader, corpus_records):
    """Import the same files twice; nothing moves.

    This is what catches a ``CREATE`` where a ``MERGE`` belongs, a key that is not
    a pure function of its input, and a counter that accumulates when it should
    replace.
    """
    loader.load_records(corpus_records)
    loader.finalize()
    first = _snapshot(loader)

    loader.load_records(corpus_records)
    loader.finalize()
    assert _snapshot(loader) == first


def test_reimport_by_a_second_loader_changes_nothing(loader, corpus_records,
                                                     neo4j_params):
    """Keys are stable across processes, not merely within one."""
    import neo4j

    loader.load_records(corpus_records)
    loader.finalize()
    first = _snapshot(loader)

    driver = neo4j.GraphDatabase.driver(
        neo4j_params["uri"], auth=(neo4j_params["user"], neo4j_params["password"]),
        notifications_min_severity="OFF")
    try:
        second = VocabularyLoader(driver, batch_size=5_000)
        second.load_records(corpus_records)
        second.finalize()
    finally:
        driver.close()
    assert _snapshot(loader) == first


# -- the graph the design promises ---------------------------------------------


def test_no_instance_ever_becomes_a_node(loader, corpus_records):
    """Every node carries a vocabulary label and no other kind exists."""
    loader.load_records(corpus_records)
    loader.finalize()
    labels = {row["l"] for row in
              loader.run("MATCH (n) UNWIND labels(n) AS l RETURN DISTINCT l AS l")}
    assert labels <= set(VOCABULARY_LABELS)
    types = {row["t"] for row in
             loader.run("MATCH ()-[r]->() RETURN DISTINCT type(r) AS t")}
    assert types <= set(VOCABULARY_RELATIONSHIPS)


def test_the_graph_is_far_smaller_than_the_corpus_it_describes(loader, corpus_records):
    loader.load_records(corpus_records)
    loader.finalize()
    total = sum(loader.node_counts().values())
    assert total < corpus_records.elements, (
        f"{total} nodes for {corpus_records.elements} elements — instances leaked in")


def test_every_key_has_a_uniqueness_constraint(loader):
    """Without these, ``MERGE`` is a label scan and the import never finishes."""
    existing = {(row["labelsOrTypes"][0], row["properties"][0])
                for row in loader.run("SHOW CONSTRAINTS")
                if row["labelsOrTypes"] and row["properties"]}
    for _, label, prop in KEY_CONSTRAINTS:
        assert (label, prop) in existing


def test_occurrences_are_the_sum_over_vendors(loader, corpus_records):
    loader.load_records(corpus_records)
    loader.finalize()
    mismatched = loader.run("""
        MATCH (s:Shape)
        OPTIONAL MATCH (:Vendor)-[u:USES]->(s)
        WITH s, sum(coalesce(u.occurrences, 0)) AS total
        WHERE s.occurrences <> total
        RETURN count(s) AS c""")[0]["c"]
    assert mismatched == 0


def test_a_shape_used_by_two_vendors_is_one_node(loader, json_files):
    """92 % of shapes are vendor-private; the 8 % that are not must not duplicate."""
    shared = extract_paths(json_files, vendor="a").merge(
        extract_paths(json_files, vendor="b"))
    loader.load_records(shared)
    loader.finalize()
    row = loader.run("""
        MATCH (s:Shape)<-[:USES]-(v:Vendor)
        WITH s, count(DISTINCT v) AS vendors
        WHERE vendors > 1
        RETURN count(s) AS shared, max(vendors) AS most""")[0]
    assert row["shared"] > 0 and row["most"] == 2


def test_semantic_ids_resolve_on_the_version_stripped_base(loader):
    """The join that lifts resolution roughly tenfold."""
    from neo4aas.vocabulary.concepts import concept
    from neo4aas.vocabulary.extract import VocabularyRecords

    records = VocabularyRecords()
    records.add_semantic_id("0173-1#02-BAF016#006", "GlobalReference", "ExternalReference")
    loader.load_records(records)
    # A dictionary that knows a *different version* of the same concept.
    loader.load_concepts([concept("0173-1#02-BAF016#009", "eclass")])
    loader.finalize()
    assert loader.run("""
        MATCH (:SemanticId {value: '0173-1#02-BAF016#006'})
              -[:RESOLVES_TO]->(c:Concept)
        RETURN c.irdi AS irdi""")[0]["irdi"] == "0173-1#02-BAF016#009"


def test_an_unresolvable_semantic_id_stays_in_the_graph(loader):
    """~31 % resolve nowhere. That absence is a finding, not an error."""
    from neo4aas.vocabulary.extract import VocabularyRecords

    records = VocabularyRecords()
    records.add_semantic_id("urn:nobody:defines:this", None, None)
    loader.load_records(records)
    loader.finalize()
    row = loader.run("""
        MATCH (n:SemanticId {value: 'urn:nobody:defines:this'})
        RETURN count { (n)-[:RESOLVES_TO]->() } AS resolved""")[0]
    assert row["resolved"] == 0


def test_accumulate_mode_adds_where_replace_mode_replaces(loader, json_files):
    """The escape hatch for sharded loads, and the reason it is not the default.

    ``replace`` is idempotent and is what every acceptance number is measured with;
    ``accumulate`` is for loading disjoint shards of one vendor in separate runs,
    and re-running it doubles the counters. Both behaviours are deliberate, so both
    are pinned.
    """
    records = extract_paths(json_files, vendor="festo")
    loader.load_records(records)
    loader.finalize()
    replaced = _snapshot(loader)["uses"]

    adding = VocabularyLoader(loader.driver, batch_size=5_000, mode="accumulate")
    adding.load_records(records)
    adding.finalize()
    accumulated = _snapshot(loader)["uses"]
    assert accumulated == {key: value * 2 for key, value in replaced.items()}

    # …and the default puts it back, because it is a function of the run, not of history.
    loader.load_records(records)
    loader.finalize()
    assert _snapshot(loader)["uses"] == replaced


def test_an_unknown_mode_is_refused():
    with pytest.raises(ValueError, match="replace"):
        VocabularyLoader(None, mode="append")


# -- the merged layout ----------------------------------------------------------


def test_two_vendors_share_one_containment_edge_without_clobbering(loader, json_files):
    """The reason support lives in per-vendor arrays rather than in one integer.

    ``:CONTAINS`` is a single edge shared by every vendor that ships the pair. A
    plain counter cannot be replaced (it would erase the other vendors) and cannot
    be accumulated (it would double on re-import). Each run owns one array slot.
    """
    a = extract_paths(json_files, vendor="alpha")
    b = extract_paths(json_files, vendor="beta")
    loader.load_records(a)
    loader.load_records(b)
    loader.finalize()

    shared = loader.run("""
        MATCH (p:Shape)-[e:CONTAINS]->(c:Shape)
        WHERE size(e.vendor_names) = 2
        RETURN e.vendor_names AS names, e.vendor_occurrences AS occ,
               e.occurrences AS total, e.vendors AS vendors
        LIMIT 1""")
    assert shared, "the same files under two vendors must share containment edges"
    row = shared[0]
    assert sorted(row["names"]) == ["alpha", "beta"]
    assert row["occ"][0] == row["occ"][1], "both vendors saw the same files"
    assert row["total"] == sum(row["occ"]) and row["vendors"] == 2

    # Re-importing one vendor replaces only its own slot.
    loader.load_records(a)
    loader.finalize()
    after = loader.run("""
        MATCH (p:Shape)-[e:CONTAINS]->(c:Shape)
        WHERE size(e.vendor_names) = 2
        RETURN e.occurrences AS total LIMIT 1""")[0]
    assert after["total"] == row["total"]


def test_containment_totals_are_derived_from_the_slots(loader, corpus_records):
    """Totals are recomputed from the arrays, so they cannot drift apart."""
    loader.load_records(corpus_records)
    loader.finalize()
    assert loader.run("""
        MATCH ()-[e:CONTAINS]->()
        WHERE e.occurrences <> reduce(t = 0, x IN coalesce(e.vendor_occurrences, []) | t + x)
           OR e.submodels   <> reduce(t = 0, x IN coalesce(e.vendor_submodels, []) | t + x)
        RETURN count(e) AS c""")[0]["c"] == 0


def test_submodel_support_is_not_the_occurrence_count(loader, corpus_records):
    """Two different supports, and the graph keeps both — a pair seen 40 000 times
    across 12 submodels is a repeated list, not a widespread schema relation."""
    loader.load_records(corpus_records)
    loader.finalize()
    row = loader.run("""
        MATCH ()-[e:CONTAINS]->() WHERE coalesce(e.occurrences, 0) > 0
        RETURN count(e) AS edges,
               sum(CASE WHEN e.occurrences > e.submodels THEN 1 ELSE 0 END) AS repeated""")[0]
    assert row["edges"] > 0 and row["repeated"] > 0


def test_top_level_shapes_are_counted_not_edged(loader, corpus_records):
    """Containment has no node to hang the top level off, so roots are a count."""
    loader.load_records(corpus_records)
    loader.finalize()
    row = loader.run("""
        MATCH (s:Shape) WHERE coalesce(s.top_level, 0) > 0
        RETURN count(s) AS roots, max(s.top_level) AS most""")[0]
    assert row["roots"] > 0 and row["most"] > 0
    assert loader.run("""
        MATCH (s:Shape)
        OPTIONAL MATCH (:Vendor)-[u:USES]->(s)
        WITH s, sum(coalesce(u.top_level, 0)) AS total
        WHERE coalesce(s.top_level, 0) <> total
        RETURN count(s) AS c""")[0]["c"] == 0


def test_a_template_pair_nobody_ships_has_no_support(loader, corpus_templates):
    """The conformance question, now answerable per relation instead of per shape."""
    from neo4aas.vocabulary.templates import extract_templates

    templates, records = extract_templates(corpus_templates)
    loader.load_records(records)
    loader.load_templates(templates)
    loader.finalize()
    row = loader.run("""
        MATCH ()-[e:CONTAINS]->()
        RETURN sum(CASE WHEN e.in_template THEN 1 ELSE 0 END) AS declared,
               sum(CASE WHEN e.in_template AND coalesce(e.occurrences, 0) = 0
                        THEN 1 ELSE 0 END) AS unimplemented""")[0]
    assert row["declared"] > 0
    assert row["unimplemented"] == row["declared"], \
        "no vendor was loaded, so every declared pair must be unimplemented"


def test_clear_removes_only_what_this_importer_owns(loader, corpus_records):
    loader.run("CREATE (:SomethingElse {marker: 'keep me'})")
    loader.load_records(corpus_records)
    loader.clear()
    assert sum(loader.node_counts().values()) == 0
    assert loader.run(
        "MATCH (n:SomethingElse) RETURN count(n) AS c")[0]["c"] == 1
    loader.run("MATCH (n:SomethingElse) DETACH DELETE n")


# -- IEC CDD, the second dictionary --------------------------------------------


@pytest.fixture
def cdd_result():
    """The smallest real dictionary in the export: 152 records, every kind."""
    from neo4aas.vocabulary.iec_cdd import parse_iec_cdd
    from tests.paths import CORPUS_IEC_CDD_DIR

    path = CORPUS_IEC_CDD_DIR / "iec63508.jsonl"
    if not path.is_file():
        pytest.skip(f"IEC CDD not available at {path}")
    return parse_iec_cdd([path])


def _load_cdd(loader, result) -> None:
    from neo4aas.vocabulary.iec_cdd import (
        class_broader_pairs,
        concepts_from_iec_cdd,
        property_class_pairs,
    )

    loader.load_concepts(concepts_from_iec_cdd(result))
    loader.load_broader_base_pairs(class_broader_pairs(result), name="BROADER(cdd class)")
    loader.load_broader_base_pairs(property_class_pairs(result),
                                   name="BROADER(cdd property)")
    loader.finalize()


def test_iec_cdd_concepts_and_hierarchy_are_written(loader, cdd_result):
    _load_cdd(loader, cdd_result)
    counts = {r["kind"]: r["c"] for r in loader.run(
        "MATCH (c:Concept {source: 'iec_cdd'}) RETURN c.kind AS kind, count(*) AS c")}
    assert counts == cdd_result.kinds
    assert loader.run("""
        MATCH (:Concept {source: 'iec_cdd'})-[:BROADER]->(:Concept {source: 'iec_cdd'})
        RETURN count(*) AS c""")[0]["c"] > 0


def test_a_cdd_semantic_id_resolves_on_the_version_stripped_base(loader, cdd_result):
    """A vendor cites version ``#001`` of a concept the export ships as ``#002``.

    That is the whole reason the join is on the base — and it is why IEC CDD is
    worth loading at all: those semanticIds resolve nowhere without it.
    """
    from neo4aas.core.utils import irdi_base

    entry = next(r for r in cdd_result.records.values() if r["_type"] == "property")
    declared = f"{irdi_base(entry['irdi'])}#999"

    loader.run("CREATE (:SemanticId {value: $v, base: $b})",
               v=declared, b=irdi_base(declared))
    _load_cdd(loader, cdd_result)

    assert loader.run("""
        MATCH (:SemanticId {value: $v})-[:RESOLVES_TO]->(c:Concept)
        RETURN c.irdi AS irdi""", v=declared)[0]["irdi"] == entry["irdi"]


def test_reimporting_the_dictionary_changes_nothing(loader, cdd_result):
    _load_cdd(loader, cdd_result)
    first = _snapshot(loader)
    _load_cdd(loader, cdd_result)
    assert _snapshot(loader) == first


def test_the_two_dictionaries_do_not_contest_each_other(loader, cdd_result):
    """ECLASS and IEC CDD rank equally in ``SOURCE_PRECEDENCE``, which is only safe
    because they publish disjoint identifier spaces. An overlap would mean a load
    order decided the definitions, and idempotence would be gone."""
    _load_cdd(loader, cdd_result)
    assert loader.run("""
        MATCH (c:Concept {source: 'iec_cdd'})
        WHERE NOT c.irdi STARTS WITH '0112'
        RETURN count(*) AS c""")[0]["c"] == 0
