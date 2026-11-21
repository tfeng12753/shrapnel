"""Audit one optimizer edit against two prepared Python-executor snapshots."""
import argparse
import ast
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import tokenize

from selector import apply_change, parse_patch
from tools.map_executor_plan import checkout_digest, digest
from tools.map_import_replay import staging_exclusions


def operator_edit(before, after, line):
    """Accept one boolean/comparison token edit inside an ordinary function body."""
    def tokens(text):
        return [token.string for token in tokenize.generate_tokens(io.StringIO(text).readline)
                if token.start[0] == line and token.type not in (
                    tokenize.INDENT, tokenize.DEDENT, tokenize.NL, tokenize.NEWLINE, tokenize.ENDMARKER)]

    def in_body(text):
        tree = ast.parse(text)
        for func in tree.body:
            if not isinstance(func, ast.FunctionDef) or func.decorator_list or func.lineno >= line:
                continue
            for stmt in func.body:
                if stmt.lineno <= line <= stmt.end_lineno:
                    return not any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda))
                                   for node in ast.walk(stmt))
        return False

    a, b = tokens(before), tokens(after)
    while a and b and a[0] == b[0]:
        a, b = a[1:], b[1:]
    while a and b and a[-1] == b[-1]:
        a.pop()
        b.pop()
    operators = {"and", "or", "<", "<=", ">", ">=", "==", "!="}
    change = ((len(a) == len(b) == 1 and a[0] in operators and b[0] in operators)
              or (a, b) in [(["not"], []), ([], ["not"])])
    return change and in_body(before) and in_body(after)


def audit(source, patch, prepared, fixture, schema, wall_seconds):
    deadline = time.monotonic() + wall_seconds
    changes = parse_patch(patch)
    if len(changes) != 1:
        return "retain: mixed patch"
    change, = changes
    name = change["old"]
    if (name != change["new"] or not name.startswith("sqlglot/optimizer/") or not name.endswith(".py")
            or ".." in Path(name).parts or not (source / name).resolve().is_relative_to(source)):
        return "retain: outside optimizer"
    before = (source / name).read_text()
    after, removed, inserted = apply_change(before, change)
    if inserted or len(removed) != 1 or not operator_edit(before, after, next(iter(removed))):
        return "retain: unsupported edit"
    checkout = checkout_digest(source)
    for seed in (1, 2):
        evidence = json.loads((prepared / f"plan-deps-{seed}.json").read_text())
        snapshot = json.loads((prepared / f"plan-base-{seed}.json").read_text())
        if evidence["checkout"] != checkout or evidence["snapshot"] != digest(snapshot):
            return "retain: mismatched baseline"
        files = evidence["modules"]
        if "sqlglot/executor/python.py" not in files or name in files:
            return "retain: downstream dependency or missing evidence"

    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "src"
        shutil.copytree(source, root, ignore=staging_exclusions(source, [name]))
        (root / name).write_text(after)
        for seed in (1, 2):
            output = Path(td) / f"check-{seed}.json"
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return "retain: timeout"
            result = subprocess.run(
                [sys.executable, "-B", str(Path(__file__).with_name("map_executor_plan.py").resolve()),
                 "--source", str(root), "--fixture", fixture, "--schema", schema, "--output", str(output),
                 "--reference", str(prepared / f"plan-base-{seed}.json")],
                cwd=root, env=dict(os.environ, PYTHONHASHSEED=str(seed), SKIP_INTEGRATION="0",
                                   OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1"),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=remaining)
            if result.returncode != 0 or not output.is_file():
                return "retain: optimizer or capture error"
            if json.loads(output.read_text()).get("matched") is not True:
                return "retain: execution inputs differ"
    return "candidate: both snapshots matched"


def check(source, patch, prepared, fixture, schema, wall_seconds=20):
    try:
        return audit(source.resolve(), patch, prepared.resolve(), fixture, schema, wall_seconds)
    except (OSError, ValueError, KeyError, TypeError, SyntaxError, tokenize.TokenError,
            RecursionError, subprocess.TimeoutExpired) as exc:
        return f"retain: {type(exc).__name__}"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("source", "patch", "prepared"):
        parser.add_argument(f"--{option}", type=Path, required=True)
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--schema", required=True)
    args = parser.parse_args()
    print(check(args.source, args.patch.read_text(), args.prepared, args.fixture, args.schema))
