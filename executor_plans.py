"""Budgeted comparison of one patched optimizer against both pinned TPC-DS snapshots."""
import ast
import io
import json
import math
import os
from pathlib import Path
import resource
import signal
import subprocess
import sys
import tempfile
import time
import tokenize

from tools.map_executor_plan import checkout_digest, digest
from isolated_pytest import cpu_seconds, stage

TOOLS = Path(__file__).resolve().parent / "tools"
ITEM = "tests/test_executor.py::TestExecutor::test_execute_tpcds"


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


def matched(source, name, before, after, line, prepared, budget, *, rule_screen=False):
    """Return False on any incomplete comparison. Budget covers parent and children."""
    start = cpu_seconds()
    wall_deadline = time.monotonic() + 2 * budget + 1

    def run(script, args, seed, root):
        remaining = budget - (cpu_seconds() - start)
        wall = wall_deadline - time.monotonic()
        if remaining < 0.05 or wall <= 0:
            return False

        def limit_cpu():
            # A fresh interpreter runs the single-process capture. ITIMER_PROF also
            # enforces the fractional part of the remaining CPU allowance.
            soft, hard = resource.getrlimit(resource.RLIMIT_CPU)
            cap = math.ceil(remaining)
            if soft != resource.RLIM_INFINITY:
                cap = min(cap, soft)
            resource.setrlimit(resource.RLIMIT_CPU, (cap, hard))
            signal.signal(signal.SIGPROF, signal.SIG_DFL)
            signal.setitimer(signal.ITIMER_PROF, remaining)

        result = subprocess.run([sys.executable, "-B", str(TOOLS / script), "--source", str(root),
                                 "--schema", "TPCDS_SCHEMA", *args], cwd=root, preexec_fn=limit_cpu,
                                env=dict(os.environ, PYTHONHASHSEED=str(seed), SKIP_INTEGRATION="0",
                                         OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1", OMP_NUM_THREADS="1"),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=wall)
        return result.returncode == 0

    try:
        if not operator_edit(before, after, line):
            return False
        checkout = checkout_digest(source)
        for seed in (1, 2):
            evidence = json.loads((prepared / f"plan-deps-{seed}.json").read_text())
            snapshot = json.loads((prepared / f"plan-base-{seed}.json").read_text())
            if evidence["checkout"] != checkout or evidence["snapshot"] != digest(snapshot):
                return False
            modules = evidence["modules"]
            if "sqlglot/executor/python.py" not in modules or name in modules:
                return False
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "src"
            # Only these two trees enter the capture. stage() removes every cached
            # copy of the patched module, so it cannot run from baseline bytecode.
            stage(source, root, name, after, trees=("sqlglot", "tests"))
            screen = prepared / "plan-screen.json"
            if screen.is_file() and not run("map_executor_screen.py",
                    ["--reference", str(screen), "--module", name, "--line", str(line)], 1, root):
                return False
            for seed in (1, 2):
                rules = prepared / f"rules-{seed}"
                if rule_screen:
                    manifest = json.loads((rules / "manifest.json").read_text())
                    if name in manifest["rules"] and not run("map_rule_replay.py",
                            ["--snapshot", str(prepared / f"plan-base-{seed}.json"), "--folder",
                             str(rules), "--module", name], seed, root):
                        return False
                output = Path(directory) / f"result-{seed}.json"
                if not run("map_executor_plan.py", ["--fixture", "tpcds", "--reference",
                           str(prepared / f"plan-base-{seed}.json"), "--output", str(output)], seed, root):
                    return False
                if json.loads(output.read_text()).get("matched") is not True:
                    return False
        return True
    except (OSError, ValueError, TypeError, SyntaxError, KeyError, IndexError, OverflowError,
            RecursionError, tokenize.TokenError, subprocess.SubprocessError):
        return False
