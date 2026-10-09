"""Import smoke test: every module (and every third-party package the
dashboard imports) must resolve with only requirements.txt installed — the
class of failure that once broke the deployed app."""
import ast
import importlib
import importlib.util
import sys

import pytest

from conftest import PROJECT_ROOT, SRC

MODULES = sorted(
    ".".join(p.relative_to(SRC).with_suffix("").parts)
    for p in SRC.rglob("*.py") if p.name != "__init__.py")


@pytest.mark.parametrize("module", MODULES)
def test_module_imports(module):
    importlib.import_module(module)


def _top_level_imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


@pytest.mark.parametrize("name", sorted(_top_level_imports(PROJECT_ROOT / "app" / "dashboard.py")))
def test_dashboard_dependency_resolves(name):
    if name in sys.stdlib_module_names:
        return
    local = (SRC / name).exists() or (SRC / f"{name}.py").exists()
    assert local or importlib.util.find_spec(name) is not None, \
        f"app/dashboard.py imports '{name}' but it is not installed (requirements.txt?)"
