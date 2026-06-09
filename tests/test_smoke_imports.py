"""Import smoke tests: every module loads and every runtime dependency is
present. The feature tests drive objects through the TestClient and never walk
the real import graph, so a missing wheel (the classic `import uvicorn` ->
ModuleNotFoundError at launch) slips past them. These tests, and `plumb doctor`,
close that gap. They are the CI guard for the diagnostic in plumb.diagnostics."""

import importlib
import pkgutil

import pytest

import plumb.diagnostics as diag


def _walk(pkg_name: str) -> list[str]:
    pkg = importlib.import_module(pkg_name)
    return [pkg_name] + [
        m.name for m in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + ".")
    ]


ALL_MODULES = _walk("plumb") + _walk("web.api")


@pytest.mark.parametrize("module_name", ALL_MODULES)
def test_every_module_imports(module_name):
    """A broken or missing import in any module fails here, by name."""
    importlib.import_module(module_name)


@pytest.mark.parametrize("dep", diag.RUNTIME_IMPORTS)
def test_runtime_dependency_present(dep):
    """Each declared runtime dependency must be importable (uvicorn, fastapi,
    the engine libs...) so the launch path cannot ModuleNotFound at startup."""
    importlib.import_module(dep)


def test_diagnose_all_pass():
    """The full self-check (engine + web app end to end) is green in CI."""
    rows = diag.diagnose()
    failed = [(label, detail) for label, ok, detail in rows if not ok]
    assert not failed, f"self-check failures: {failed}"
