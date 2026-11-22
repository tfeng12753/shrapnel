"""Observe a short-circuited length comparison in a disposable baseline checkout."""
import argparse
import ast
import gzip
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


OBSERVER = """
from collections import defaultdict
import json
import os
from pathlib import Path

events = defaultdict(set)

def note(key, state):
    if state not in events[key]:
        events[key].add(state)
        folder = os.environ.get("LENGTH_AUDIT_WORKER_DIR")
        if folder:
            with (Path(folder) / str(os.getpid())).open("a") as out:
                print(json.dumps([key, state]), file=out)

def evaluate(gate, length):
    key = os.environ.get("PYTEST_CURRENT_TEST", "outside")
    if not gate:
        note(key, "short")
        return False
    try:
        size = length()
    except Exception:
        note(key, "unknown")
        raise
    if type(size) is not int or size < 0:
        note(key, "unknown")
    else:
        note(key, "zero" if size == 0 else "one" if size == 1 else "many")
    return size == 1

def pytest_sessionfinish(session, exitstatus):
    Path(os.environ["LENGTH_AUDIT_OUTPUT"]).write_text(json.dumps({k: sorted(v) for k, v in events.items()}))
"""


def instrument(source, line):
    guards = [node.test for node in ast.walk(ast.parse(source)) if isinstance(node, ast.If) and node.lineno == line]
    if len(guards) != 1:
        raise ValueError("expected one if statement")
    guard, = guards
    match guard:
        case ast.BoolOp(op=ast.And(), values=[ast.Name(id=gate), ast.Compare(
            left=ast.Call(func=ast.Name(id="len"), args=[ast.Name(id=items)], keywords=[]),
            ops=[ast.Eq()], comparators=[ast.Constant(value=1)])]):
            pass
        case _:
            raise ValueError("expected a flag followed by a length equality against one")
    if guard.end_lineno != line:
        raise ValueError("expected a single-line guard")
    lines = source.splitlines(keepends=True)
    raw = lines[line - 1].encode()
    replacement = f"__import__('length_audit_observer').evaluate({gate}, lambda: len({items}))".encode()
    lines[line - 1] = (raw[:guard.col_offset] + replacement + raw[guard.end_col_offset:]).decode()
    return "".join(lines)


def main(args):
    with gzip.open(args.map, "rt") as stream:
        data = json.load(stream)
    if not data["complete"]:
        raise ValueError("a complete baseline map is required")
    bits = int(data["files"][args.module]["lines"][str(args.line)], 16)
    identities = {name for i, name in enumerate(data["tests"]) if bits >> i & 1}
    catalog = sorted(json.loads(args.catalog.read_text())["tests"], key=lambda row: row["number"])
    tests = [row["id"] for row in catalog if row["id"] in identities]
    if not tests or len(tests) != len(identities):
        raise ValueError("line coverage and catalog disagree")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "src"
        shutil.copytree(args.source, root, ignore=shutil.ignore_patterns("__pycache__", ".git"))
        module = (root / args.module).resolve()
        if not module.is_relative_to(root):
            raise ValueError("module outside staged checkout")
        module.write_text(instrument(module.read_text(), args.line))
        observer = root / "length_audit_observer.py"
        with observer.open("x") as stream:
            stream.write(OBSERVER)
        output = Path(tmp) / "events.json"
        env = dict(os.environ, PYTHONPATH=str(root), PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
                   LENGTH_AUDIT_OUTPUT=str(output))
        cmd = [sys.executable, "-m", "pytest", "-q", "-p", observer.stem]
        subprocess.run([*cmd, *tests], cwd=root, env=env, check=True, stdout=sys.stderr)
        events = json.loads(output.read_text())
        isolated = {}
        if args.resolve_workers:
            for index, test in enumerate(tests):
                key = test + " (call)"
                if events.get(key):
                    continue
                folder = Path(tmp) / str(index)
                folder.mkdir()
                subprocess.run([*cmd, test], cwd=root, env=dict(env, LENGTH_AUDIT_WORKER_DIR=str(folder)),
                               check=True, stdout=sys.stderr)
                seen = set()
                for path in folder.iterdir():
                    for line in path.read_text().splitlines():
                        phase, value = json.loads(line)
                        seen.add(value if phase == key else "unknown")
                isolated[test] = sorted(seen)

    groups = {"changed": [], "unchanged": [], "unattributed": []}
    prefix = []
    for test in tests:
        seen = set(events.get(test + " (call)", []))
        if not seen or "unknown" in seen:
            group = "unattributed"
        elif seen & {"zero", "one"}:
            group = "changed"
        else:
            group = "unchanged"
        groups[group].append(test)
        if not groups["changed"] and not groups["unattributed"]:
            prefix.append(test)
    print(json.dumps({"candidates": len(tests), "groups": groups, "before_first_uncertain": prefix,
                      "outside_call": [k for k in events if not k.endswith(" (call)")],
                      "isolated_observations": isolated}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--module", required=True)
    parser.add_argument("--line", type=int, required=True)
    parser.add_argument("--map", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--resolve-workers", action="store_true",
                        help="Rerun each unattributed item alone and collect its worker observations")
    main(parser.parse_args())
