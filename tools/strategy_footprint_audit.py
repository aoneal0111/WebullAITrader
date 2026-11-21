"""Read-only inventory of strategy Python modules and their static import consumers.

This tool is a review aid, NOT proof that any file can be deleted. Python also
supports dynamic imports, CLI entry points, fixture patching, and string names.
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

STRATEGY_DIRS = (
    "app/strategy",
    "app/strategy_engine",
    "app/strategies",
    "app/strategy_lab",
    "app/strategy_research",
    "app/equity_overhaul",
    "app/crypto_research",
)


def inspect(root: Path) -> dict:
    root = root.resolve()
    app = root / "app"
    tests = root / "tests"
    if not app.is_dir() or not tests.is_dir():
        raise ValueError("Repository root must contain app and tests")

    # Do not traverse arbitrary symlinks outside the repository.
    files = sorted(
        path for directory in (app, tests, root / "tools")
        if directory.is_dir()
        for path in directory.rglob("*.py")
        if path.is_file() and not path.is_symlink()
    )
    modules: dict[str, str] = {}
    dependencies: dict[str, set[str]] = {}
    parse_errors: list[str] = []

    for path in files:
        rel = path.relative_to(root).as_posix()
        module = rel[:-3].replace("/", ".")
        if module.endswith(".__init__"):
            module = module[:-9]
        modules[module] = rel

    for path in files:
        rel = path.relative_to(root).as_posix()
        current = rel[:-3].replace("/", ".")
        is_package = current.endswith(".__init__")
        package = current[:-9] if is_package else current.rpartition(".")[0]
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=rel)
        except (SyntaxError, UnicodeError) as exc:
            parse_errors.append(rel + ": " + type(exc).__name__)
            continue
        referenced: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                referenced.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    parts = package.split(".")
                    up = node.level - 1
                    base = ".".join(parts[:len(parts) - up]) if up <= len(parts) else ""
                    target = ".".join(filter(None, (base, node.module or "")))
                else:
                    target = node.module or ""
                if target:
                    referenced.add(target)
                    referenced.update(target + "." + alias.name for alias in node.names)
        dependencies[rel] = referenced

    candidates = []
    for name, rel in sorted(modules.items()):
        if not any(rel.startswith(prefix + "/") for prefix in STRATEGY_DIRS):
            continue
        consumers = sorted(
            source for source, imported in dependencies.items()
            if source != rel and any(
                item == name or item.startswith(name + ".")
                for item in imported
            )
        )
        candidates.append({
            "path": rel,
            "static_import_consumers": len(consumers),
            "app_consumers": sum(p.startswith("app/") for p in consumers),
            "test_consumers": sum(p.startswith("tests/") for p in consumers),
            "tool_consumers": sum(p.startswith("tools/") for p in consumers),
            "sample_consumers": consumers[:12],
            "review_only": True,
        })
    return {
        "schema": "STRATEGY_STATIC_FOOTPRINT_V1",
        "scanned_python_files": len(files),
        "strategy_files": len(candidates),
        "parse_errors": parse_errors,
        "no_static_import_consumers": sum(not x["static_import_consumers"] for x in candidates),
        "candidates": candidates,
        "deletions_authorized": False,
        "notice": "No import reference is NOT deletion proof: inspect dynamic imports, test fixtures, CLI, docs, and runtime composition before any removal.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, help="Optional JSON output path; otherwise stdout")
    args = parser.parse_args(argv)
    report = json.dumps(inspect(args.repo), indent=2)
    if args.output is None:
        print(report)
    else:
        args.output.write_text(report + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
