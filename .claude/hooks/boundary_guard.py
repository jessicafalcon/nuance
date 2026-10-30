#!/usr/bin/env python3
"""Keep each external dependency behind the one module that owns it (ob-craft, module map).

Two entry points, one rule set:

- Claude Code PostToolUse hook: reads the hook payload on stdin, checks the edited
  file, exits 2 with the findings so the model fixes them.
- pre-commit / CI: `python3 .claude/hooks/boundary_guard.py FILE...`, exits 1 on
  findings, so edits made outside Claude are held to the same rule.

AST-based rather than grep: comments and docstrings never match, and aliased imports
(`import os as o`) are resolved.
"""

from __future__ import annotations

import ast
import json
import os
import sys
from pathlib import Path

PACKAGE_PREFIX = "src/objection_brief/"

# Module (or package) -> the one file allowed to import it. Switching model provider,
# chat tool or store then changes exactly one file (PROJECT-BRIEF.md §4).
OWNED_IMPORTS = {
    "anthropic": "llm.py",
    "requests": "notify.py",
    "duckdb": "log.py",
}
# Configuration comes in through config.py only; the rest of the code takes values as
# parameters, so tests never depend on the developer's environment.
ENV_OWNER = "config.py"
ENV_READS = {"os.environ", "os.getenv", "os.environb", "os.getenvb"}
# Only the CLI talks to the terminal; library code returns values.
PRINT_OWNER = "cli.py"
# `uv run --env-file .env` loads secrets; no code reads `.env` (decision in the brief §15).
FORBIDDEN_IMPORTS = {"dotenv": "use `uv run --env-file .env`; no code reads `.env`"}


def _dotted(node: ast.AST) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _aliases(tree: ast.Module) -> dict[str, str]:
    """Map local names to fully qualified ones (`o` -> `os`, `getenv` -> `os.getenv`)."""
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                names[alias.asname or top] = alias.name if alias.asname else top
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                names[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return names


def _resolve(dotted: str, names: dict[str, str]) -> str:
    head, _, rest = dotted.partition(".")
    full = names.get(head, head)
    return f"{full}.{rest}" if rest else full


def check_source(source: str, filename: str) -> list[str]:
    """Findings for one module of the package, by its file name.

    >>> check_source("import anthropic", "detect.py")
    ['line 1: imports anthropic; only llm.py may']
    >>> check_source("import os\\nx = os.getenv('A')", "brief.py")
    ['line 2: reads the environment; only config.py may']
    >>> check_source("import anthropic", "llm.py")
    []
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []  # ruff reports syntax errors; nothing to judge yet
    names = _aliases(tree)
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            modules = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            for module in modules:
                top = module.split(".")[0]
                if top in FORBIDDEN_IMPORTS:
                    found.append((node.lineno, f"imports {top}; {FORBIDDEN_IMPORTS[top]}"))
                owner = OWNED_IMPORTS.get(top)
                if owner and filename != owner:
                    found.append((node.lineno, f"imports {top}; only {owner} may"))
            # `from os import environ` is an environment read wherever it is used.
            if (
                isinstance(node, ast.ImportFrom)
                and filename != ENV_OWNER
                and any(f"{node.module}.{a.name}" in ENV_READS for a in node.names)
            ):
                found.append((node.lineno, f"reads the environment; only {ENV_OWNER} may"))
        elif isinstance(node, ast.Attribute | ast.Name) and filename != ENV_OWNER:
            dotted = _dotted(node)
            if dotted and _resolve(dotted, names) in ENV_READS:
                found.append((node.lineno, f"reads the environment; only {ENV_OWNER} may"))
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "print"
            and "print" not in names
            and filename != PRINT_OWNER
        ):
            found.append((node.lineno, f"prints; only {PRINT_OWNER} writes to the terminal"))
    return [f"line {line}: {msg}" for line, msg in sorted(set(found))]


def in_scope(rel_path: str) -> bool:
    return rel_path.startswith(PACKAGE_PREFIX) and rel_path.endswith(".py")


def main() -> int:
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd()).resolve()
    hook_mode = len(sys.argv) == 1
    if hook_mode:
        payload = json.load(sys.stdin)
        paths = [(payload.get("tool_input") or {}).get("file_path", "")]
    else:
        paths = sys.argv[1:]

    report: list[str] = []
    for raw in filter(None, paths):
        path = Path(raw).resolve()
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if not in_scope(rel) or not path.is_file():
            continue
        for finding in check_source(path.read_text(encoding="utf-8"), path.name):
            report.append(f"{rel} {finding}")

    if not report:
        return 0
    print(
        "boundary-guard: a dependency leaked out of its owning module (ob-craft).", file=sys.stderr
    )
    print("\n".join(f"  - {line}" for line in report), file=sys.stderr)
    print("Move the call into the owning module and pass values in as parameters.", file=sys.stderr)
    return 2 if hook_mode else 1


if __name__ == "__main__":
    sys.exit(main())
