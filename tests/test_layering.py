"""The core/apps layering is a convention in a single distribution — this enforces it.

Three properties, checked by walking the source AST (no imports executed):

1. ``neo4aas.core``, ``neo4aas.agent_tools`` and ``neo4aas.vocabulary`` import
   neither ``basyx`` nor any app package. This is what lets pyproject declare
   ``neo4j`` as the only base dependency. ``vocabulary`` reaches into
   :mod:`neo4aas.eclass` for one thing only — the OntoML parser and its
   dataclasses, which are themselves basyx-free — and that exception is named in
   :data:`ECLASS_PARSER_MODULES` and checked rather than waived.
2. No app package imports another app package.
3. ``import neo4aas`` succeeds with basyx absent — the property a static check cannot
   see (a re-export added to ``neo4aas/__init__.py`` would silently reintroduce the
   dependency), so it runs in a subprocess with ``basyx`` blocked at import time.
"""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "neo4aas"

#: Packages that may depend on core but never on each other.
APPS = ("basyx_ext", "eclass", "chatbot")

#: Layers that must stay free of basyx and of every app.
BASYX_FREE = ("core", "agent_tools", "vocabulary")

#: The one cross-package import a basyx-free layer may make: parsing ECLASS OntoML
#: XML into dataclasses. It needs no SDK, and duplicating a 250-line streaming
#: parser to satisfy a directory boundary would be the worse trade. Both modules
#: are asserted basyx-free below, so the allowance cannot become a back door —
#: and ``neo4aas/eclass/__init__.py`` resolves its basyx-dependent re-exports
#: lazily (PEP 562) so that importing a submodule does not execute them.
ECLASS_PARSER_MODULES = ("neo4aas.eclass.models", "neo4aas.eclass.ontoml_parser")


def _module_files(name: str) -> list[Path]:
    """Source files of a package (or of a single top-level module)."""
    pkg = SRC / name
    if pkg.is_dir():
        return sorted(p for p in pkg.rglob("*.py") if "__pycache__" not in p.parts)
    return [SRC / f"{name}.py"]


def _imported_modules(path: Path) -> set[str]:
    """Absolute module names imported by a file.

    Relative imports are skipped: they can only reach inside the importing
    package, so they cannot cross a layer boundary.
    """
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            found.add(node.module)
    return found


def _files_importing(files: list[Path], predicate) -> list[str]:
    violations = []
    for f in files:
        for mod in sorted(_imported_modules(f)):
            if predicate(mod):
                violations.append(f"{f.relative_to(SRC.parent.parent)}: imports {mod}")
    return violations


@pytest.mark.parametrize("layer", BASYX_FREE)
def test_layer_does_not_import_basyx(layer):
    """core/agent_tools stay installable without the basyx extra."""
    bad = _files_importing(
        _module_files(layer),
        lambda m: m == "basyx" or m.startswith("basyx."),
    )
    assert not bad, "basyx must stay out of the base dependency layer:\n" + "\n".join(bad)


@pytest.mark.parametrize("layer", BASYX_FREE)
def test_layer_does_not_import_apps(layer):
    """Dependencies point inward: an app may use core, never the reverse."""
    forbidden = tuple(f"neo4aas.{app}" for app in APPS)
    bad = _files_importing(
        _module_files(layer),
        lambda m: m.startswith(forbidden) and m not in ECLASS_PARSER_MODULES,
    )
    assert not bad, f"{layer} must not depend on an app package:\n" + "\n".join(bad)


def test_the_eclass_parser_exception_is_itself_basyx_free():
    """The allowance above is only sound while these two modules need no SDK."""
    bad = _files_importing(
        [SRC / "eclass" / f"{name.rsplit('.', 1)[-1]}.py" for name in ECLASS_PARSER_MODULES],
        lambda m: m == "basyx" or m.startswith("basyx."),
    )
    assert not bad, "the ECLASS parser stopped being basyx-free:\n" + "\n".join(bad)


@pytest.mark.parametrize("app", APPS)
def test_app_does_not_import_another_app(app):
    """Apps are siblings: they share core, not each other."""
    others = tuple(f"neo4aas.{other}" for other in APPS if other != app)
    bad = _files_importing(_module_files(app), lambda m: m.startswith(others))
    assert not bad, f"{app} must not depend on a sibling app:\n" + "\n".join(bad)


def test_import_neo4aas_works_without_basyx():
    """A bare `import neo4aas` must not need the basyx extra.

    Static analysis cannot catch this: a convenience re-export in
    ``neo4aas/__init__.py`` (say ``Neo4jObjectStore``) would pull basyx in
    transitively. Block the module and try for real.
    """
    script = """
import sys

class _Blocker:
    def find_module(self, name, path=None):
        return self if name == "basyx" or name.startswith("basyx.") else None
    def find_spec(self, name, path=None, target=None):
        if name == "basyx" or name.startswith("basyx."):
            raise ImportError(f"basyx is not installed (blocked): {name}")
        return None

sys.meta_path.insert(0, _Blocker())
for mod in [m for m in sys.modules if m == "basyx" or m.startswith("basyx.")]:
    del sys.modules[mod]

import neo4aas
assert neo4aas.AASNeo4JClient is not None
assert neo4aas.convert_aasql_to_cypher is not None

# The vocabulary importer, its CLI and the ECLASS parser it borrows must all come
# up without the SDK: `neo4aas vocab import` is the entry point a bare install runs.
import neo4aas.vocabulary
import neo4aas.vocabulary.cli
import neo4aas.vocabulary.concepts
from neo4aas.eclass.ontoml_parser import EclassOntomlParser
assert EclassOntomlParser is not None
assert neo4aas.vocabulary.shape_key("a", None, "P", None)
print("OK")
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True
    )
    assert result.returncode == 0, (
        "`import neo4aas` pulled in basyx:\n" + result.stdout + result.stderr
    )
    assert "OK" in result.stdout
