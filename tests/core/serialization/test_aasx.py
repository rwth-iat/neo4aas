"""AASX package ingestion.

Findings these guard, all from real corpus packages (aas-corpus `instances/vendors`,
`instances/inhouse`):

* the in-house Pumpwerk packages declare the **V3.1** metamodel namespace
  (``https://admin-shell.io/aas/3/1``) — detection pinned to the V3.0 namespace found
  no AAS part in them and imported nothing, without an error;
* R. Stahl packages carry their environment as ``aasx/data.json`` — a JSON part, which
  a scan restricted to ``.xml`` entries never sees.

Both failure modes are silent, which is the dangerous part: the caller counts the file
as loaded.
"""
import io
import json
import zipfile

import pytest

from neo4aas.core.serialization.aasx import AasxToNeo4jImporter

_ENV_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<aas:environment xmlns:aas="{ns}">'
    "<aas:submodels><aas:submodel><aas:idShort>SM</aas:idShort>"
    "<aas:id>urn:sm/1</aas:id></aas:submodel></aas:submodels>"
    "</aas:environment>"
)

_ENV_JSON = {
    "submodels": [{"modelType": "Submodel", "id": "urn:sm/json", "idShort": "SMjson"}]
}


def _package(tmp_path, parts: dict, name="pkg.aasx"):
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("aasx/aasx-origin", "")
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("_rels/.rels", "<Relationships/>")
        for part_name, content in parts.items():
            zf.writestr(part_name, content)
    return path


@pytest.mark.parametrize("ns", [
    "https://admin-shell.io/aas/3/0",
    "https://admin-shell.io/aas/3/1",
    "https://admin-shell.io/aas/3/2",
])
def test_xml_environment_detected_for_every_aas_3_x_namespace(tmp_path, ns):
    pkg = _package(tmp_path, {"aasx/data.xml": _ENV_XML.format(ns=ns)})
    envs = list(AasxToNeo4jImporter(None).iter_environments(str(pkg)))
    assert len(envs) == 1
    assert envs[0]["submodels"][0]["id"] == "urn:sm/1"


def test_json_environment_part_is_read(tmp_path):
    pkg = _package(tmp_path, {"aasx/data.json": json.dumps(_ENV_JSON)})
    envs = list(AasxToNeo4jImporter(None).iter_environments(str(pkg)))
    assert len(envs) == 1
    assert envs[0]["submodels"][0]["id"] == "urn:sm/json"


def test_opc_metadata_and_foreign_parts_are_ignored(tmp_path):
    pkg = _package(tmp_path, {
        "aasx/data.xml": _ENV_XML.format(ns="https://admin-shell.io/aas/3/0"),
        "aasx/notes.json": json.dumps({"unrelated": True}),
        "docprops/core.xml": "<coreProperties/>",
    })
    envs = list(AasxToNeo4jImporter(None).iter_environments(str(pkg)))
    assert len(envs) == 1


def test_package_without_an_aas_part_warns(tmp_path, caplog):
    """A package we cannot read anything out of must say so, not import silently."""
    pkg = _package(tmp_path, {"docprops/core.xml": "<coreProperties/>"})
    with caplog.at_level("WARNING"):
        envs = list(AasxToNeo4jImporter(None).iter_environments(str(pkg)))
    assert envs == []
    assert any("no AAS" in r.message for r in caplog.records)


def test_gzipped_package_is_read(tmp_path):
    """The corpus stores every package gzipped (`x.aasx.gz`)."""
    import gzip

    pkg = _package(tmp_path, {"aasx/data.xml": _ENV_XML.format(ns="https://admin-shell.io/aas/3/1")})
    gz = tmp_path / "pkg.aasx.gz"
    gz.write_bytes(gzip.compress(pkg.read_bytes()))
    envs = list(AasxToNeo4jImporter(None).iter_environments(str(gz)))
    assert len(envs) == 1


def test_malformed_zip_raises(tmp_path):
    bad = tmp_path / "broken.aasx"
    bad.write_bytes(b"not a zip at all")
    with pytest.raises(zipfile.BadZipFile):
        list(AasxToNeo4jImporter(None).iter_environments(str(bad)))


def test_iter_environments_accepts_bytes_like_source(tmp_path):
    pkg = _package(tmp_path, {"aasx/data.json": json.dumps(_ENV_JSON)})
    envs = list(AasxToNeo4jImporter(None).iter_environments(io.BytesIO(pkg.read_bytes())))
    assert len(envs) == 1
