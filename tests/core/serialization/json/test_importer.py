"""Unit tests for JsonToNeo4jImporter internals (no live Neo4j required)."""
from dataclasses import replace

import pytest
from neo4j.exceptions import TransientError

from neo4aas.core.base import EMPTY_NEO4J_MODEL_CONFIG
from neo4aas.core.serialization.json.importer import JsonToNeo4jImporter


class _RaisingSession:
    """Stub Neo4j session whose `run` always fails with a (retryable) TransientError."""

    def run(self, *args, **kwargs):
        raise TransientError("simulated deadlock")


def test_transient_error_during_graph_write_is_not_swallowed():
    """A TransientError while writing a batch must propagate, not be swallowed.

    Swallowing it would drop the whole batch while reporting success, silently corrupting
    the graph. Correct behaviour is to surface the failure to the caller.
    """
    importer = JsonToNeo4jImporter(uri=None, user="x")  # driver is None; no Neo4j needed
    grouped = {("Property",): [{"uid": 1, "idShort": "a"}, {"uid": 2, "idShort": "b"}]}
    relationships = {"value": [{"from_uid": 1, "to_uid": 2, "rel_props": {}}]}

    with pytest.raises(TransientError):
        importer._write_graph(_RaisingSession(), grouped, relationships)


def test_only_edges_leaving_a_hash_merged_node_are_merged():
    """Relationship writes split by whether the *source* node may already exist.

    A node created in this batch cannot have the edge already, so its edges are CREATEd —
    MERGE on a relationship costs ~4x a CREATE and is the single most expensive part of a
    bulk import. Only a node MERGEd on its content hash can be one the database already
    holds, complete with the edge, so only its outgoing edges need MERGE.
    """
    importer = JsonToNeo4jImporter(uri=None, user="x")
    grouped = {
        ("Property",): [{"uid": 1, "idShort": "a"}],
        ("Reference",): [{"uid": 2, "hash": "h"}],
    }
    relationships = {
        "semanticId": [{"from_uid": 1, "to_uid": 2, "rel_props": {}}],
        "referredSemanticId": [{"from_uid": 2, "to_uid": 1, "rel_props": {}}],
    }

    _, _, _, create_rels, merge_rels = importer._write_buckets(grouped, relationships, {})

    assert list(create_rels) == ["semanticId"]
    assert create_rels["semanticId"] == [{"from": "1", "to": "2", "props": {}}]
    assert merge_rels == [{"type": "referredSemanticId", "from": "2", "to": "1", "props": {}}]


def test_relationship_with_an_unmapped_endpoint_is_dropped():
    """An edge whose endpoint was not written (e.g. a dropped duplicate subtree) is skipped.

    It cannot be created — the server-side uid map has no node for it — and passing it on
    would make the whole batch fail on a null endpoint.
    """
    importer = JsonToNeo4jImporter(uri=None, user="x")
    grouped = {("Property",): [{"uid": 1, "idShort": "a"}]}
    relationships = {"value": [{"from_uid": 1, "to_uid": 99, "rel_props": {}}]}

    _, _, _, create_rels, merge_rels = importer._write_buckets(grouped, relationships, {})

    assert create_rels == {}
    assert merge_rels == []


def test_uid_is_not_part_of_the_written_properties():
    """`uid` is import-internal bookkeeping and must never reach the property bag.

    It used to be written with the node and deleted again by a second pass over every
    node; keeping it out of `props` removes both the write and the pass.
    """
    importer = JsonToNeo4jImporter(uri=None, user="x")
    grouped = {("Property",): [{"uid": 7, "idShort": "a"}]}

    create, _, _, _, _ = importer._write_buckets(grouped, {}, {})

    assert create == {"Property": [{"uid": "7", "props": {"idShort": "a"}}]}


def test_group_nodes_by_label_does_not_mutate_input():
    """Grouping must not strip `labels` from the caller's node dicts.

    `labels` becomes Neo4j node labels, not properties, so it is excluded from each
    grouped bucket — but via a copy, leaving the input reusable. Previously a `pop`
    deleted `labels` in place, so a second grouping pass raised KeyError.
    """
    importer = JsonToNeo4jImporter(uri=None, user="x")
    nodes = [
        {"labels": ["Property", "SubmodelElement"], "uid": 1, "idShort": "a"},
        {"labels": ["Submodel"], "uid": 2, "id": "urn:sm"},
    ]

    grouped = importer._group_nodes_by_label(nodes)

    # Input untouched: labels still present, dicts reusable.
    assert nodes[0]["labels"] == ["Property", "SubmodelElement"]
    assert nodes[1]["labels"] == ["Submodel"]
    # Buckets keyed by sorted label tuple, with labels excluded from the payload.
    assert grouped[("Property", "SubmodelElement")] == [{"uid": 1, "idShort": "a"}]
    assert grouped[("Submodel",)] == [{"uid": 2, "id": "urn:sm"}]
    # Idempotent: a second pass works (no KeyError) and yields the same result.
    assert importer._group_nodes_by_label(nodes) == grouped


def test_flattened_list_of_dicts_tolerates_heterogeneous_entries():
    """A list-of-dicts property whose entries do not all carry the same keys must not
    abort the import.

    The flattening read every entry with `dict_[key]` for the keys of the *first* entry,
    so one entry missing a key (a LangString without `text`, a Reference key without
    `type`) raised KeyError and rejected the whole file — the opposite of the
    "store non-conformant supplier data faithfully" contract of the ingest path. Keys are
    now the union over all entries, missing ones filled with null, so the parallel lists
    stay aligned and nothing is silently dropped.
    """
    config = replace(
        EMPTY_NEO4J_MODEL_CONFIG,
        list_of_dicts_prop_as_multiple_list_props={"Unknown": ["keys"]},
    )
    importer = JsonToNeo4jImporter(uri=None, user="x", model_config=config)
    nodes, _ = importer._process_dict(
        {"keys": [{"type": "GlobalReference", "value": "a"}, {"value": "b"}, {"extra": 1}]}
    )

    root = nodes[-1]
    assert root["keys_value"] == ["a", "b", None]
    assert root["keys_type"] == ["GlobalReference", None, None]
    assert root["keys_extra"] == [None, None, 1]


def test_upload_all_json_from_dir_finds_gzipped_files(tmp_path, monkeypatch):
    """A directory of `x.json.gz` (how real corpora ship) must be seen as JSON input.

    The listing used `endswith('.json')`, so a gzipped corpus produced "Found 0 JSON
    files" and an empty database instead of an error.
    """
    import gzip

    (tmp_path / "a.json.gz").write_bytes(
        gzip.compress(b'{"submodels": [{"modelType": "Submodel", "id": "urn:a"}]}')
    )
    (tmp_path / "b.json").write_bytes(b'{"submodels": [{"modelType": "Submodel", "id": "urn:b"}]}')
    (tmp_path / "ignore.txt").write_text("x")

    importer = JsonToNeo4jImporter(uri=None, user="x")
    uploaded = []
    monkeypatch.setattr(importer, "_upload_nodes_and_relationships",
                        lambda nodes, rels, *a, **kw: uploaded.append(nodes) or kw.get("stats"))

    stats = importer.upload_all_json_from_dir(str(tmp_path))

    assert stats.total_files == 2
    ids = {n.get("id") for batch in uploaded for n in batch}
    assert {"urn:a", "urn:b"} <= ids


def test_process_json_file_reads_gzipped_source(tmp_path):
    import gzip

    path = tmp_path / "env.json.gz"
    path.write_bytes(gzip.compress(b'{"submodels": [{"modelType": "Submodel", "id": "urn:gz"}]}'))

    importer = JsonToNeo4jImporter(uri=None, user="x")
    nodes, _ = importer._process_json_file(str(path))

    assert any(n.get("id") == "urn:gz" for n in nodes)


def test_overlapped_writes_run_off_the_calling_thread_and_in_order():
    """A bulk loader's batches are written on one background thread, in submission order.

    Overlapping the write with the decoding of the next batch is where the wall-clock win
    comes from; a *single* writer is what keeps deduplication and the duplicate-subtree
    skip correct, so the order must be exactly the submission order.
    """
    import threading

    importer = JsonToNeo4jImporter(uri=None, user="x")
    seen = []
    importer._upload_nodes_and_relationships = (
        lambda nodes, rels, stats=None, **kw: seen.append((nodes, threading.get_ident())))

    main = threading.get_ident()
    with importer._overlapped_writes() as submit:
        for i in range(4):
            submit([i], {}, None, 10)

    assert [n for n, _ in seen] == [[0], [1], [2], [3]]
    writers = {t for _, t in seen}
    assert len(writers) == 1 and main not in writers


def test_a_failed_overlapped_write_is_reported_to_the_caller():
    """A write that fails on the background thread must not pass as a successful load."""
    importer = JsonToNeo4jImporter(uri=None, user="x")

    def boom(*args, **kwargs):
        raise RuntimeError("write failed")

    importer._upload_nodes_and_relationships = boom

    with pytest.raises(RuntimeError, match="write failed"):
        with importer._overlapped_writes() as submit:
            submit([{"uid": 1}], {}, None, 10)
