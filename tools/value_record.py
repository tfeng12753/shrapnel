#!/usr/bin/env python3
"""Record the operands that reach value comparisons, per test (image build time).

    python3 tools/value_record.py --source /opt/sqlglot --output /app/value-record.json.gz

Runs the pinned checkout's real test suite once, in one process, in collection
order. Every single-operator value comparison (`==`, `!=`, `<`, `<=`, `>`,
`>=`) inside an ordinary function body of the pinned library is compiled into
a call that records, for the test whose call phase is running, the operands it
received, then performs the original comparison. Per site and test the record
holds:

- the relation states (equal, less, greater, identical) of every evaluation,
  when both operands were exactly `int`, `float`, `bool`, `str` or `None`;
- up to a few distinct typed operand pairs, when they are short;
- `unknown` when any evaluation had another operand type (custom comparison
  code may run), when the original comparison raised, or when the pairs
  overflowed (states remain usable).

A site evaluated outside any test's call phase (imports, collection, class or
module setup) is recorded as global, and is never used. Code run in child
processes is not observed, so tests that start a process are recorded as
external. A test that ran in this process without starting one and has no
observation at a site never evaluated it.

Runs on the unmodified pinned checkout only. No sample patch, answer or mutant
is read.
"""
import argparse
import ast
import builtins
import gzip
import importlib.machinery
import json
import os
from pathlib import Path
import sys
import time

import pytest

VERSION = 1
OPERATORS = {ast.Eq: "Eq", ast.NotEq: "NotEq", ast.Lt: "Lt", ast.LtE: "LtE", ast.Gt: "Gt", ast.GtE: "GtE"}
# As in the selector: these run once for many tests, so their comparisons are not narrowed.
SHARED_HOOKS = {"setUpClass", "tearDownClass", "setUpModule", "tearDownModule", "setUp", "tearDown"}
EXACT = (int, float, bool, str, type(None))
TYPE_TAG = {int: "i", float: "f", bool: "b", str: "s", type(None): "n"}
PAIR_LIMIT = 6
STRING_LIMIT = 48


def deferred_sites(tree):
    """Compare nodes inside ordinary function bodies (not shared hooks), as in the selector."""
    found = []

    def visit(node, deferred=False):
        if isinstance(node, ast.Compare) and deferred and len(node.ops) == 1 and type(node.ops[0]) in OPERATORS:
            found.append(node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.iter_child_nodes(node):
                visit(child, child in node.body and node.name not in SHARED_HOOKS)
        else:
            for child in ast.iter_child_nodes(node):
                visit(child, deferred)

    visit(tree)
    return found


class Recorder:
    def __init__(self, source):
        self.source = source
        self.sites = []          # [(file, "line:col", op)]
        self.data = []           # per site: {test index: [states set, pairs set or None, unknown bool]}
        self.global_sites = set()
        # Tests that started a process (their work may run unobserved), and whether any
        # process started outside a test's call phase (it may serve later tests).
        self.external = set()
        self.outside_spawn = False
        self.current = None

    def site(self, name, node):
        self.sites.append((name, f"{node.lineno}:{node.col_offset}", OPERATORS[type(node.ops[0])]))
        self.data.append({})
        return len(self.sites) - 1

    def helpers(self):
        data, global_sites, recorder = self.data, self.global_sites, self

        def observe(site, a, b):
            test = recorder.current
            if test is None:
                global_sites.add(site)
                return None
            slot = data[site].get(test)
            if slot is None:
                slot = data[site][test] = [set(), set(), False]
            elif slot[2]:
                return None
            ta, tb = type(a), type(b)
            if ta not in TYPE_TAG or tb not in TYPE_TAG:
                slot[2] = True
                return None
            return slot

        def record(slot, a, b, state):
            slot[0].add(state)
            pairs = slot[1]
            if pairs is None:
                return
            if ((type(a) is str and len(a) > STRING_LIMIT) or (type(b) is str and len(b) > STRING_LIMIT)):
                slot[1] = None
                return
            key = (TYPE_TAG[type(a)], a, TYPE_TAG[type(b)], b)
            if key not in pairs:
                if len(pairs) >= PAIR_LIMIT or a != a or b != b:
                    slot[1] = None  # NaN or too many pairs: states only.
                else:
                    pairs.add(key)

        def relation(a, b):
            # Built-in scalars only: these comparisons run no user code.
            eq = a == b
            identical = 8 if a is b else 0
            try:
                lt, gt = a < b, a > b
            except TypeError:
                return f"e:{int(eq) | identical}"
            return f"p:{int(eq) | (2 if lt else 0) | (4 if gt else 0) | identical}"

        def make(compare):
            def helper(site, a, b):
                slot = observe(site, a, b)
                try:
                    result = compare(a, b)
                except BaseException:
                    if slot is not None:
                        slot[2] = True
                    elif recorder.current is not None and recorder.current in data[site]:
                        data[site][recorder.current][2] = True
                    raise
                if slot is not None:
                    record(slot, a, b, relation(a, b))
                return result
            return helper

        import operator
        return {f"_shrapnel_value_{name}": make(getattr(operator, method)) for name, method in (
            ("Eq", "eq"), ("NotEq", "ne"), ("Lt", "lt"), ("LtE", "le"), ("Gt", "gt"), ("GtE", "ge"))}

    def transform(self, name, text):
        tree = ast.parse(text)
        targets = {id(node) for node in deferred_sites(tree)}
        recorder = self

        class Rewrite(ast.NodeTransformer):
            def visit_Compare(self, node):
                self.generic_visit(node)
                if id(node) not in targets:
                    return node
                index = recorder.site(name, node)
                call = ast.Call(func=ast.Name(id=f"_shrapnel_value_{OPERATORS[type(node.ops[0])]}", ctx=ast.Load()),
                                args=[ast.Constant(index), node.left, node.comparators[0]], keywords=[])
                return ast.copy_location(call, node)

        # Identity tests on the original nodes: collect targets before rewriting children.
        tree = Rewrite().visit(tree)
        ast.fix_missing_locations(tree)
        return tree

    def spawned(self):
        if self.current is None:
            self.outside_spawn = True
        else:
            self.external.add(self.current)

    def install(self):
        builtins.__dict__.update(self.helpers())
        recorder = self

        def audit(event, args):
            if event in ("os.fork", "os.forkpty", "os.posix_spawn", "os.exec", "os.system", "subprocess.Popen"):
                recorder.spawned()

        sys.addaudithook(audit)
        import multiprocessing.process
        start = multiprocessing.process.BaseProcess.start

        def started(process):
            recorder.spawned()
            return start(process)

        multiprocessing.process.BaseProcess.start = started
        library = str(self.source / "sqlglot") + os.sep
        original = importlib.machinery.SourceFileLoader.get_code

        def get_code(loader, fullname):
            filename = str(Path(loader.get_filename(fullname)).resolve())
            if filename.startswith(library) and filename.endswith(".py"):
                name = Path(filename).relative_to(recorder.source).as_posix()
                text = Path(filename).read_text(encoding="utf-8")
                return compile(recorder.transform(name, text), filename, "exec", dont_inherit=True)
            return original(loader, fullname)

        importlib.machinery.SourceFileLoader.get_code = get_code


class Plugin:
    """Numbers tests in collection order and attributes call phases to them."""

    def __init__(self, recorder):
        self.recorder = recorder
        self.numbers = {}
        self.seen, self.failed = set(), set()

    def pytest_collection_modifyitems(self, items):
        for item in items:
            self.numbers.setdefault(item.nodeid, len(self.numbers))

    def _number(self, nodeid):
        return self.numbers.get(nodeid)

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_call(self, item):
        self.recorder.current = self._number(item.nodeid)
        self.seen.add(item.nodeid)
        try:
            yield
        finally:
            self.recorder.current = None

    def pytest_runtest_logreport(self, report):
        if report.failed:
            self.failed.add(report.nodeid)


def encode(numbers):
    mask = 0
    for n in numbers:
        mask |= 1 << n
    return format(mask, "x")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("/opt/sqlglot"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--select", nargs="*", default=["tests"], help="pytest arguments selecting tests")
    args = parser.parse_args()
    source = args.source.resolve()
    os.chdir(source)
    sys.path.insert(0, str(source))
    os.environ.setdefault("SKIP_INTEGRATION", "0")
    sys.dont_write_bytecode = True
    recorder = Recorder(source)
    recorder.install()

    plugin = Plugin(recorder)
    started = time.monotonic()
    code = pytest.main(["-q", "-p", "no:cacheprovider", "-p", "no:randomly", *args.select], plugins=[plugin])
    elapsed = time.monotonic() - started
    sites = {}
    for index, (name, site, op) in enumerate(recorder.sites):
        if index in recorder.global_sites:
            continue
        observed = recorder.data[index]
        if not observed:
            continue
        unknown, states, pairs = [], {}, {}
        for test, (test_states, test_pairs, bad) in observed.items():
            # A test that failed on the pinned checkout is never evidence.
            if bad:
                unknown.append(test)
                continue
            for state in test_states:
                states.setdefault(state, []).append(test)
            if test_pairs is not None:
                pairs[str(test)] = sorted([list(p) for p in test_pairs], key=repr)
        entry = {"op": op, "reached": encode(observed), "unknown": encode(unknown),
                 "states": {s: encode(t) for s, t in sorted(states.items())}, "pairs": pairs}
        sites.setdefault(name, {})[site] = entry
    numbers = plugin.numbers
    failed = sorted(numbers[n] for n in plugin.failed if n in numbers)
    data = {"version": VERSION, "tests": sorted(numbers, key=numbers.get), "exit": int(code), "seconds": elapsed,
            "failed": failed, "ran": sorted(numbers[n] for n in plugin.seen if n in numbers),
            "external": sorted(recorder.external), "outside_spawn": recorder.outside_spawn,
            "global": sorted({f"{recorder.sites[i][0]}:{recorder.sites[i][1]}" for i in recorder.global_sites}),
            "files": sites}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(args.output, "wt", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))
    print(f"{len(recorder.sites)} sites instrumented, {sum(map(len, sites.values()))} observed, "
          f"{len(recorder.global_sites)} global, {len(failed)} failed tests, {elapsed:.0f} s", file=sys.stderr)


if __name__ == "__main__":
    main()
