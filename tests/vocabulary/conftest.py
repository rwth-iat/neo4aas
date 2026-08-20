"""Corpus-backed fixtures for the vocabulary tests.

The importer's job is to survive real AAS data, so these tests read the reference
corpus rather than hand-written toys: three serializations (``.json.gz``,
``.xml.gz``, ``.aasx.gz``), eight vendors, and files that do not parse. When the
corpus is not checked out beside the repo the tests skip — they do not silently
fall back to a fixture that would prove nothing.
"""

import pytest

from tests.paths import CORPUS_INSTANCES_DIR, CORPUS_TEMPLATES_DIR

#: One real file per serialization the corpus actually ships.
CORPUS_SAMPLES = {
    "json": ("festo", "*.json.gz"),
    "xml": ("sick", "*.xml.gz"),
    "aasx": ("abb", "*.aasx.gz"),
}


def _corpus_files(vendor: str, pattern: str, count: int) -> list:
    root = CORPUS_INSTANCES_DIR / vendor
    if not root.is_dir():
        return []
    return sorted(root.rglob(pattern))[:count]


def corpus_sample(vendor: str, pattern: str, count: int = 5) -> list:
    files = _corpus_files(vendor, pattern, count)
    if not files:
        pytest.skip(f"corpus not available: {CORPUS_INSTANCES_DIR / vendor}/{pattern}")
    return files


@pytest.fixture(scope="session")
def corpus_instances():
    if not CORPUS_INSTANCES_DIR.is_dir():
        pytest.skip(f"corpus not available at {CORPUS_INSTANCES_DIR}")
    return CORPUS_INSTANCES_DIR


@pytest.fixture(scope="session")
def corpus_templates():
    if not CORPUS_TEMPLATES_DIR.is_dir():
        pytest.skip(f"templates not available at {CORPUS_TEMPLATES_DIR}")
    return CORPUS_TEMPLATES_DIR


@pytest.fixture(scope="session")
def json_files():
    return corpus_sample(*CORPUS_SAMPLES["json"])


@pytest.fixture(scope="session")
def xml_files():
    return corpus_sample(*CORPUS_SAMPLES["xml"])


@pytest.fixture(scope="session")
def aasx_files():
    return corpus_sample(*CORPUS_SAMPLES["aasx"])
