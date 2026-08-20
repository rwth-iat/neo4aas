"""Gzip-transparent source-file reading (neo4aas.core.io).

Real AAS corpora keep every instance compressed (`x.json.gz`, `x.xml.gz`, `x.aasx.gz`),
so ingest paths that matched on the bare suffix found nothing there and reported "0
files" instead of failing — the quietest possible data loss.
"""
import gzip
import json

import pytest

from neo4aas.core.io import aas_suffix, is_aas_file, list_aas_files, read_bytes


def test_read_bytes_passes_through_plain_file(tmp_path):
    p = tmp_path / "a.json"
    p.write_text('{"x": 1}')
    assert json.loads(read_bytes(p)) == {"x": 1}


def test_read_bytes_decompresses_gzip(tmp_path):
    p = tmp_path / "a.json.gz"
    p.write_bytes(gzip.compress(b'{"x": 1}'))
    assert json.loads(read_bytes(p)) == {"x": 1}


def test_read_bytes_detects_gzip_by_magic_not_by_name(tmp_path):
    """A corpus may compress without renaming; detection is by the gzip magic bytes."""
    p = tmp_path / "misnamed.json"
    p.write_bytes(gzip.compress(b'{"x": 1}'))
    assert json.loads(read_bytes(p)) == {"x": 1}


@pytest.mark.parametrize("name, expected", [
    ("a.json", ".json"),
    ("a.json.gz", ".json"),
    ("A.XML.GZ", ".xml"),
    ("pkg.aasx.gz", ".aasx"),
    ("notes.txt", ".txt"),
    ("README", ""),
])
def test_aas_suffix_ignores_gz(name, expected):
    assert aas_suffix(name) == expected


def test_is_aas_file_and_listing(tmp_path):
    (tmp_path / "a.json").write_text("{}")
    (tmp_path / "b.xml.gz").write_bytes(gzip.compress(b"<environment/>"))
    (tmp_path / "c.aasx").write_bytes(b"PK")
    (tmp_path / "notes.txt").write_text("x")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "d.json.gz").write_bytes(gzip.compress(b"{}"))

    assert is_aas_file(tmp_path / "b.xml.gz")
    assert not is_aas_file(tmp_path / "notes.txt")
    assert [p.name for p in list_aas_files(tmp_path)] == ["a.json", "b.xml.gz", "c.aasx"]
    assert [p.name for p in list_aas_files(tmp_path, suffixes=(".json",))] == ["a.json"]
    assert "d.json.gz" in [p.name for p in list_aas_files(tmp_path, recursive=True)]
