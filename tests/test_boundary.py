"""
Package-boundary lint (CLAUDE.md §1.1, PRD §5.1).

Fails the build if pipeline/ or agents/ imports duckdb, anthropic, or opens
events.ndjson directly outside of project_agent.* APIs.
"""
import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
FORBIDDEN_DIRS = ["pipeline", "agents"]
FORBIDDEN_IMPORTS = {"duckdb", "anthropic"}


def _python_files(dirname: str) -> list[Path]:
    d = REPO_ROOT / dirname
    if not d.exists():
        return []
    return list(d.rglob("*.py"))


def _check_imports(py_file: Path) -> list[str]:
    violations: list[str] = []
    source = py_file.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(py_file))
    except SyntaxError:
        return violations

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in FORBIDDEN_IMPORTS:
                    violations.append(
                        f"{py_file.relative_to(REPO_ROOT)}:{node.lineno} "
                        f"direct '{alias.name}' import forbidden outside package"
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root = node.module.split(".")[0]
                if root in FORBIDDEN_IMPORTS:
                    violations.append(
                        f"{py_file.relative_to(REPO_ROOT)}:{node.lineno} "
                        f"direct '{node.module}' import forbidden outside package"
                    )

    return violations


def _check_raw_ndjson(py_file: Path) -> list[str]:
    """Catch open(..., 'events.ndjson') style access outside the package."""
    violations: list[str] = []
    source = py_file.read_text(encoding="utf-8")
    for lineno, line in enumerate(source.splitlines(), 1):
        if "events.ndjson" in line and "open(" in line:
            violations.append(
                f"{py_file.relative_to(REPO_ROOT)}:{lineno} "
                "direct open() on events.ndjson forbidden; use project_agent.events"
            )
    return violations


@pytest.mark.boundary
def test_no_forbidden_imports_in_pipeline_or_agents() -> None:
    violations: list[str] = []
    for dirname in FORBIDDEN_DIRS:
        for py_file in _python_files(dirname):
            violations.extend(_check_imports(py_file))

    assert not violations, "Package boundary violations:\n" + "\n".join(violations)


@pytest.mark.boundary
def test_no_direct_ndjson_access_in_pipeline_or_agents() -> None:
    violations: list[str] = []
    for dirname in FORBIDDEN_DIRS:
        for py_file in _python_files(dirname):
            violations.extend(_check_raw_ndjson(py_file))

    assert not violations, "Direct NDJSON access violations:\n" + "\n".join(violations)
