"""Measure patched lazy-import checks against an existing schedule (offline only)."""
import argparse
import ast
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

from selector import apply_change, classify_python_edit, parse_patch
from tools.import_closure import REVISION, TESTS


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
        before = path.read_text()
        after, _, _ = apply_change(before, change)
        if not cold_comparisons(before, after, records):
            return {}
        updates[name] = after
    return updates


def check_item(source, updates, item):
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder) / "source"
        shutil.copytree(source, root, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"))
        for name, contents in updates.items():
            (root / name).write_text(contents)
        report = Path(folder) / "result.xml"
        env = dict(os.environ, PYTHONPATH=str(root), PYTHONDONTWRITEBYTECODE="1", SKIP_INTEGRATION="0",
                   PATH=str(Path(sys.executable).parent) + os.pathsep + os.defpath)
        with subprocess.Popen(
            [sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", "--timeout=10",
             f"--junitxml={report}", item], cwd=root, env=env, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True,
        ) as proc:
            try:
                if proc.wait(timeout=20):
                    return False
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                return False
        cases = ET.parse(report).findall(".//testcase")
        return len(cases) == 1 and all(cases[0].find(tag) is None for tag in ("skipped", "failure", "error"))


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
