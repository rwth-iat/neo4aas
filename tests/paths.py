"""Single source of truth for the paths tests read data from.

Test data now lives outside the installed package (``tests/data``, ``examples/``),
and the test tree is nested to mirror ``src/``, so a per-file
``Path(__file__).parents[N]`` would differ by depth and silently break whenever a
test moves. Import from here instead.

These are module-level constants rather than fixtures on purpose: several tests
parametrize over the files in a directory, which happens at collection time,
before fixtures exist.
"""

import os
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent

#: Golden expected-output corpora (~20 MB), tracked but not shipped in the wheel.
DATA_DIR = TESTS_DIR / "data"
EXPECTED_JSON_DIR = DATA_DIR / "Expected_JSON"
EXPECTED_XML_DIR = DATA_DIR / "Expected_XML"

#: Example AASQL queries, expected ASTs/Cypher, and IDTA template submodels.
EXAMPLES_DIR = REPO_ROOT / "examples"
QUERIES_DIR = EXAMPLES_DIR / "queries"
AST_DIR = EXAMPLES_DIR / "ast"
SUBMODELS_DIR = EXAMPLES_DIR / "submodels"

#: The reference AAS corpus, a sibling checkout (``AAS_CORPUS_DIR`` overrides).
#: It is 53 GB of real vendor data and is deliberately *not* vendored here — the
#: vocabulary tests read from it when it is present and skip when it is not,
#: because "run against real files, including malformed ones" is the only way to
#: test an importer whose whole job is surviving real data.
CORPUS_DIR = Path(os.environ.get("AAS_CORPUS_DIR", REPO_ROOT.parent / "aas-corpus"))
CORPUS_INSTANCES_DIR = CORPUS_DIR / "instances" / "vendors"
CORPUS_TEMPLATES_DIR = CORPUS_DIR / "specs" / "submodel-templates"
CORPUS_ECLASS_DIR = CORPUS_DIR / "dictionaries" / "eclass"
