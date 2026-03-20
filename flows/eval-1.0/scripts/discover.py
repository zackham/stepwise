#!/usr/bin/env python3
"""Discover codebase metadata for eval-1.0.

Collects: version, file counts, LOC, flow inventory, test files, doc files.
"""

import glob
import json
import os
import sys

def count_lines(filepath):
    """Count non-empty lines in a file."""
    try:
        with open(filepath, "r", errors="replace") as f:
            return sum(1 for line in f if line.strip())
    except (OSError, UnicodeDecodeError):
        return 0

def find_files(root, pattern):
    """Recursively find files matching a glob pattern."""
    return sorted(glob.glob(os.path.join(root, pattern), recursive=True))

def main():
    stepwise_path = os.environ.get("stepwise_path", "")
    if not stepwise_path:
        print(json.dumps({"error": "stepwise_path not set"}))
        sys.exit(1)

    # Version from pyproject.toml
    version = "unknown"
    toml_path = os.path.join(stepwise_path, "pyproject.toml")
    if os.path.exists(toml_path):
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        with open(toml_path, "rb") as f:
            data = tomllib.load(f)
        version = data.get("project", {}).get("version", "unknown")

    # File counts by extension
    extensions = {".py": 0, ".ts": 0, ".tsx": 0, ".yaml": 0, ".md": 0}
    for root, _dirs, files in os.walk(stepwise_path):
        # Skip hidden dirs, node_modules, .venv, __pycache__
        parts = root.split(os.sep)
        if any(p.startswith(".") or p in ("node_modules", "__pycache__", ".venv", "venv") for p in parts if p):
            continue
        for fname in files:
            ext = os.path.splitext(fname)[1]
            if ext in extensions:
                extensions[ext] += 1

    # LOC for Python source
    python_loc = 0
    py_src = os.path.join(stepwise_path, "src", "stepwise")
    for fpath in find_files(py_src, "**/*.py"):
        python_loc += count_lines(fpath)

    # LOC for TypeScript source
    typescript_loc = 0
    ts_src = os.path.join(stepwise_path, "web", "src")
    for ext in ("**/*.ts", "**/*.tsx"):
        for fpath in find_files(ts_src, ext):
            typescript_loc += count_lines(fpath)

    # Flow inventory
    flows = []
    for pattern in ("flows/*/FLOW.yaml", "examples/**/*.flow.yaml"):
        flows.extend(find_files(stepwise_path, pattern))
    flows = [os.path.relpath(f, stepwise_path) for f in flows]

    # Test file inventory
    test_files = find_files(os.path.join(stepwise_path, "tests"), "**/test_*.py")
    test_files = [os.path.relpath(f, stepwise_path) for f in test_files]

    # Doc file inventory
    doc_files = find_files(os.path.join(stepwise_path, "docs"), "*.md")
    readme = os.path.join(stepwise_path, "README.md")
    if os.path.exists(readme):
        doc_files.append(readme)
    doc_files = [os.path.relpath(f, stepwise_path) for f in doc_files]

    output = {
        "version": version,
        "project_path": stepwise_path,
        "file_counts": extensions,
        "python_loc": python_loc,
        "typescript_loc": typescript_loc,
        "flows": flows,
        "test_files": test_files,
        "doc_files": doc_files,
    }
    print(json.dumps(output))

if __name__ == "__main__":
    main()
