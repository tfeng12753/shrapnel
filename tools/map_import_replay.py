"""Measure patched lazy-import checks against an existing schedule (offline only)."""
import argparse
import ast
from collections import Counter
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

from selector import apply_change, classify_python_edit, parse_patch
from tools.import_closure import COMPARISON_OPERATORS, REVISION, TESTS


def cold_comparisons(before, after, records):
    tree = ast.parse(before, type_comments=True)
    edits = classify_python_edit(tree, ast.parse(after, type_comments=True))
    if not edits:
        return False
    cold = {(name, line) for name, line in records}
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    sites = {f"{n.lineno}:{n.col_offset}": n for n in ast.walk(tree) if isinstance(n, ast.Compare)}
    for site, _, _ in edits:
        node = sites[site]
        if node.lineno != node.end_lineno:
            return False
        while node in parents and not isinstance(parents[node], ast.FunctionDef):
            node = parents[node]
            if isinstance(node, (ast.Lambda, ast.AsyncFunctionDef, ast.ClassDef)):
                return False
        function = parents.get(node)
        if not isinstance(function, ast.FunctionDef) or node not in function.body or function.decorator_list:
            return False
        owner, name = parents.get(function), function.name
        while isinstance(owner, ast.ClassDef):
            name = f"{owner.name}.{name}"
            owner = parents.get(owner)
        if not isinstance(owner, ast.Module) or (name, function.lineno) not in cold:
            return False
    return True


def indexed_change(raw, after, removed, inserted, record):
    if not record or inserted or len(removed) != 1 or hashlib.sha256(raw).hexdigest() != record["digest"]:
        return False
    line, = removed
    old = raw.splitlines(keepends=True)
    new = after.encode().splitlines(keepends=True)
    if old[:line - 1] != new[:line - 1] or old[line:] != new[line:] or len(old) != len(new):
        return False
    before_line, after_line = old[line - 1], new[line - 1]
    for start, end in record["sites"].get(str(line), ()):
        prefix, suffix = before_line[:start], before_line[end:]
        if after_line.startswith(prefix) and after_line.endswith(suffix):
            token = after_line[start:len(after_line) - len(suffix)]
            if token != before_line[start:end] and token in COMPARISON_OPERATORS.values():
                return True
    return False


def patched_sources(source, patch, closure):
    updates = {}
    for change in parse_patch(patch):
        name = change["old"]
        records = closure.get("unentered_functions", {}).get(name)
        path = source / name
        if (Path(name).is_absolute() or ".." in Path(name).parts
                or not records or name in updates or name != change["new"] or name in closure["explicit_reads"]
                or not path.resolve().is_relative_to(source) or not name.endswith(".py")):
            return {}
        raw = path.read_bytes()
        before = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        after, removed, inserted = apply_change(before, change)
        record = closure.get("cold_operators", {}).get(name)
        if not indexed_change(raw, after, removed, inserted, record) and not cold_comparisons(before, after, records):
            return {}
        updates[name] = after
    return updates


def staging_exclusions(source, updates=()):
    # The interpreter validates PEP 552 checked hashes against the staged
    # source. Timestamp caches can survive same-size edits on a coarse clock.
    checked_header = importlib.util.MAGIC_NUMBER + (3).to_bytes(4, "little")
    changed = {source / name for name in updates}
    def ignored(directory, names):
        path = Path(directory)
        if path.name == "__pycache__":
            skipped = set(names)
            for name in names:
                if name.endswith(".pyc"):
                    try:
                        original = Path(importlib.util.source_from_cache(str(path / name)))
                    except ValueError:
                        continue
                    if original in changed:
                        continue
                    with (path / name).open("rb") as cache:
                        if cache.read(8) == checked_header:
                            skipped.remove(name)
            return skipped
        skipped = {".git", ".pytest_cache"}.intersection(names)
        if not path.is_relative_to(source / "sqlglot"):
            skipped.add("__pycache__")
        return skipped
    return ignored


def check_items(source, updates, items, wall_seconds=20):
    """Verify every requested ordinary pytest item in one isolated checkout."""
    deadline = time.monotonic() + wall_seconds
    expected = Counter()
    for item in items:
        file, *parts = item.split("::")
        if not file.endswith(".py") or not parts or not all(part.isidentifier() for part in parts):
            return False
        classname = ".".join([file[:-3].replace("/", "."), *parts[:-1]])
        expected[classname, parts[-1]] += 1
    if not expected or max(expected.values()) != 1:
        return False
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder) / "source"
        shutil.copytree(source, root, ignore=staging_exclusions(source, updates))
        for name, contents in updates.items():
            (root / name).write_text(contents)
        report = Path(folder) / "result.xml"
        env = dict(os.environ, PYTHONPATH=str(root), PYTHONDONTWRITEBYTECODE="1", SKIP_INTEGRATION="0",
                   PATH=str(Path(sys.executable).parent) + os.pathsep + os.defpath)
        if time.monotonic() >= deadline:
            return False
        with subprocess.Popen(
            [sys.executable, "-B", "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", "--timeout=10",
             f"--junitxml={report}", *items], cwd=root, env=env, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True,
        ) as proc:
            try:
                if proc.wait(timeout=max(0, deadline - time.monotonic())):
                    return False
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                return False
        tree = ET.parse(report)
        for suite in tree.iter("testsuite"):
            if any(int(suite.get(status, "0")) for status in ("errors", "failures", "skipped")):
                return False
        cases = list(tree.iter("testcase"))
        actual = Counter((case.get("classname"), case.get("name")) for case in cases)
        return actual == expected and all(
            case.find(tag) is None for case in cases for tag in ("skipped", "failure", "error")
        )


def check_item(source, updates, item):
    return check_items(source, updates, [item])


def replay(args):
    source = args.source.resolve()
    closure = json.loads(args.import_closure.read_text())
    if closure.get("revision") != REVISION or closure.get("tests") != TESTS:
        raise ValueError("closure revision or test differs from pinned observation")
    item, = TESTS
    catalog = json.loads((args.data / "tests.json").read_text())["tests"]
    test, = [t for t in catalog if t["id"] == item]
    commits = {p.stem: p for p in (args.data / "commits").glob("*.patch")}
    verified = []
    for line in args.schedule.read_text().splitlines():
        row = json.loads(line)
        if [n for worker in row["workers"] for n in worker] != [test["number"]]:
            continue
        try:
            updates = patched_sources(source, commits[row["commit"]].read_text(), closure)
            if updates and check_item(source, updates, item):
                verified.append(row["commit"])
        except (OSError, ValueError, TypeError, KeyError, SyntaxError, ET.ParseError, RecursionError):
            continue
    # All eligible schedules consist of one worker running exactly this item.
    return {"verified_passes": verified,
            "removable_simulated_ms": len(verified) * (2000 + round(1000 * test["seconds"]))}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("source", "data", "import-closure", "schedule"):
        parser.add_argument(f"--{option}", required=True, type=Path)
    print(json.dumps(replay(parser.parse_args())))
