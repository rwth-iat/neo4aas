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
        "slots": loader.run(
            "MATCH ()-[r:HAS_SLOT]->() RETURN count(r) AS c")[0]["c"],
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


def test_structure_slots_are_written_once(loader, corpus_records):
    """The slot write is the bulk of the load; a re-import must skip it entirely."""
    loader.load_records(corpus_records)
    written = loader.counts.written.get("HAS_SLOT", 0)
    assert written > 0
    second = VocabularyLoader(loader.driver, batch_size=5_000)
    second.load_records(corpus_records)
    assert second.counts.written.get("HAS_SLOT", 0) == 0


def test_clear_removes_only_what_this_importer_owns(loader, corpus_records):
    loader.run("CREATE (:SomethingElse {marker: 'keep me'})")
    loader.load_records(corpus_records)
    loader.clear()
    assert sum(loader.node_counts().values()) == 0
    assert loader.run(
        "MATCH (n:SomethingElse) RETURN count(n) AS c")[0]["c"] == 1
    loader.run("MATCH (n:SomethingElse) DETACH DELETE n")
