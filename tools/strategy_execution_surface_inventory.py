"""Offline strategy execution-surface inventory, without importing trading modules.

This is a conservative composition map, not a deletion authorization.
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path


SURFACES = {
    "desktop_warrior": ("app/composition/desktop.py", "app.strategies.warrior_momentum.autonomous_paper"),
    "desktop_quick_scalper": ("app/composition/desktop.py", "app.strategies.warrior_momentum.quick_scalper_runtime"),
    "desktop_crypto_research": ("app/composition/desktop.py", "app.crypto_research"),
    "optional_crypto_research": ("app/composition/desktop_optional_research.py", "app.crypto_research"),
}
RESEARCH_SOURCE = "app/crypto_research/strategies.py"
EQUITY_RELEASE_SOURCE = "app/equity_overhaul/admission.py"


def _read_tree(root, rel):
    path = root / rel
    if not path.is_file() or path.is_symlink():
        raise ValueError("MISSING_OR_LINKED_SOURCE:" + rel)
    return ast.parse(path.read_text(encoding="utf-8-sig"), filename=rel)


def _imports(tree):
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module)
    return found


def _empty_promotions(tree):
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "RELEASE_PROMOTIONS":
            return isinstance(node.value, ast.Tuple) and not node.value.elts
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "RELEASE_PROMOTIONS" for t in node.targets):
            return isinstance(node.value, ast.Tuple) and not node.value.elts
    return False


def _research_guard(tree):
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "CryptoStrategyDefinition":
            flags = {}
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    if item.target.id in {"research_only", "selection_authorized", "execution_authorized"}:
                        if isinstance(item.value, ast.Constant):
                            flags[item.target.id] = item.value.value
            return flags == {
                "research_only": True,
                "selection_authorized": False,
                "execution_authorized": False,
            }
    return False


def inspect(root: Path):
    root = Path(root).resolve()
    sources = {p for p, _ in SURFACES.values()} | {RESEARCH_SOURCE, EQUITY_RELEASE_SOURCE}
    parsed = {rel: _read_tree(root, rel) for rel in sorted(sources)}
    surfaces = {}
    for key, (rel, module) in SURFACES.items():
        imports = _imports(parsed[rel])
        surfaces[key] = {"source": rel, "module": module, "import_present": module in imports}
    gate_empty = _empty_promotions(parsed[EQUITY_RELEASE_SOURCE])
    crypto_nonexecuting = _research_guard(parsed[RESEARCH_SOURCE])
    return {
        "schema": "ATLAS_STRATEGY_EXECUTION_SURFACE_V1",
        "desktop_surfaces": surfaces,
        "equity_replacement_registry_empty": gate_empty,
        "crypto_discovery_research_only_defaults": crypto_nonexecuting,
        "review_required": not (all(v["import_present"] for v in surfaces.values()) and gate_empty and crypto_nonexecuting),
        "deletions_authorized": False,
        "limitations": "Static imported module map and default gate definitions only; not runtime wiring proof, dynamic imports, broker authority, or profitability evidence.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    print(json.dumps(inspect(args.repo), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
