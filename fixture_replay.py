"""Replay only the fixture entries a patch can affect for heavy fixture-driven tests.

A recorded file (tools/fixture_entries.py) lists, for each supported test, the
source lines every fixture entry executes when run alone after class setup, the
lines executed outside entries (imports, class setup, an empty run), the CPU of
each entry and a fingerprint of process-global state after each entry.

For a selected test, the affected entries are replayed on the patched checkout
in forked children, each with the test's real assertions. The test is dropped
only if every affected entry passes and leaves the same global state as on the
pinned checkout, so entries that were not replayed cannot observe a difference.
Any error, timeout, skip, unknown or mismatch keeps the test. Entries are never
sampled.
"""
from __future__ import annotations

import ast
import concurrent.futures
import functools
import gzip
import hashlib
import importlib
import json
import os
import pickle
import resource
import select
import signal
import sys
import time
import types
from pathlib import Path

VERSION = 1
# Global state compared after each entry: the pinned library and the shared test constants.
STATE_PACKAGES = ("sqlglot",)
STATE_MODULES = ("tests.helpers",)
STATE_LIMIT = 400_000


def load(path):
    try:
        opener = gzip.open if str(path).endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8") as f:
            data = json.load(f)
        return data if data.get("version") == VERSION else None
    except (OSError, ValueError, TypeError):
        return None


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ---------------------------------------------------------------- child side

class InlineExecutor(concurrent.futures.Executor):
    """Process-pool stand-in: runs work in this process with pickle round-trips.

    Spawned workers would import the unpatched files from disk, and their CPU
    could escape the limits. Arguments and results still cross pickle.
    """

    def __init__(self, max_workers=None, mp_context=None, initializer=None, initargs=(), **kwargs):
        if initializer is not None:
            initializer(*pickle.loads(pickle.dumps(tuple(initargs))))

    def submit(self, fn, /, *args, **kwargs):
        future = concurrent.futures.Future()
        try:
            fn, args, kwargs = pickle.loads(pickle.dumps((fn, args, kwargs)))
            future.set_result(pickle.loads(pickle.dumps(fn(*args, **kwargs))))
        except BaseException as error:  # noqa: BLE001 - delivered through the future
            future.set_exception(error)
        return future

    def shutdown(self, wait=True, *, cancel_futures=False):
        pass


def _summary(value, depth, budget):
    budget[0] -= 1
    if budget[0] < 0:
        raise OverflowError("state too large")
    if value is None or isinstance(value, (bool, int, float, complex, str, bytes)):
        return repr(value)
    kind = type(value)
    if isinstance(value, types.ModuleType):
        return f"module:{value.__name__}"
    if isinstance(value, type):
        return f"type:{value.__module__}.{value.__qualname__}"
    if isinstance(value, functools._lru_cache_wrapper):
        return f"cache:{getattr(value, '__qualname__', '?')}:{value.cache_info().currsize}"
    if callable(value) and hasattr(value, "__qualname__"):
        return f"fn:{getattr(value, '__module__', '')}.{value.__qualname__}"
    name = f"{kind.__module__}.{kind.__qualname__}"
    if depth <= 0:
        size = len(value) if isinstance(value, (list, tuple, set, frozenset, dict)) else ""
        return f"{name}#{size}"
    if isinstance(value, (list, tuple)):
        return f"{name}[" + ",".join(_summary(v, depth - 1, budget) for v in value) + "]"
    if isinstance(value, (set, frozenset)):
        return f"{name}{{" + ",".join(sorted(_summary(v, depth - 1, budget) for v in value)) + "}"
    if isinstance(value, dict):
        items = sorted(_summary(k, depth - 1, budget) + ":" + _summary(v, depth - 1, budget)
                       for k, v in list(value.items()))
        return f"{name}{{" + ",".join(items) + "}"
    attributes = getattr(value, "__dict__", None)
    if isinstance(attributes, dict):
        return f"{name}(" + _summary(attributes, depth - 1, budget) + ")"
    return name


def state_fingerprint():
    """Deterministic digest of module and class state in the compared modules (None if too large)."""
    digest = hashlib.sha256()
    budget = [STATE_LIMIT]
    try:
        for module_name in sorted(sys.modules):
            if not (module_name in STATE_MODULES
                    or any(module_name == p or module_name.startswith(p + ".") for p in STATE_PACKAGES)):
                continue
            namespace = getattr(sys.modules[module_name], "__dict__", None)
            if not isinstance(namespace, dict):
                continue
            for key in sorted(k for k in namespace if isinstance(k, str)):
                if key.startswith("__") and key.endswith("__"):
                    continue
                value = namespace[key]
                if isinstance(value, type) and value.__module__ == module_name:
                    members = {k: v for k, v in vars(value).items()
                               if not (k.startswith("__") and k.endswith("__"))}
                    text = _summary(members, 3, budget)
                else:
                    text = _summary(value, 3, budget)
                digest.update(f"{module_name}.{key}={text}\n".encode("utf-8", "surrogatepass"))
    except (OverflowError, RecursionError, RuntimeError, TypeError, ValueError):
        return None
    return digest.hexdigest()


class _Outcome:
    """unittest result collecting a single verdict; any non-pass is a failure."""

    def __init__(self):
        import unittest

        class Result(unittest.TestResult):
            def __init__(inner):
                super().__init__()
                inner.bad = False

            def addFailure(inner, test, err):
                super().addFailure(test, err)
                inner.bad = True

            def addError(inner, test, err):
                super().addError(test, err)
                inner.bad = True

            def addSkip(inner, test, reason):
                super().addSkip(test, reason)
                inner.bad = True

            def addSubTest(inner, test, subtest, err):
                super().addSubTest(test, subtest, err)
                if err is not None:
                    inner.bad = True

            def addExpectedFailure(inner, test, err):
                super().addExpectedFailure(test, err)
                inner.bad = True

            def addUnexpectedSuccess(inner, test):
                super().addUnexpectedSuccess(test)
                inner.bad = True

        self.result = Result()

    def passed(self):
        return not self.result.bad and self.result.testsRun == 1


def _split_id(test_id):
    file, class_name, method = test_id.split("::")
    return file[:-3].replace("/", "."), class_name, method


def prepare(source, specs, timings=None):
    """Import the test modules with inline pools; run each class's real setUpClass once."""
    modules, classes = {}, {}
    # A same-named package imported from elsewhere (this project's own tests/)
    # must not shadow the pinned checkout's test package.
    for module_name in [n for n in sys.modules if n == "tests" or n.startswith("tests.")]:
        module = sys.modules[module_name]
        locations = list(getattr(module, "__path__", None) or []) or [getattr(module, "__file__", None)]
        if not all(loc and Path(loc).resolve().is_relative_to(source) for loc in locations):
            del sys.modules[module_name]
    for test_id in specs:
        module_name, class_name, _ = _split_id(test_id)
        if module_name not in modules:
            module = importlib.import_module(module_name)
            location = Path(getattr(module, "__file__", "") or "").resolve()
            if not location.is_relative_to(source):
                raise ImportError("test module outside the pinned checkout")
            if hasattr(module, "ProcessPoolExecutor"):
                module.ProcessPoolExecutor = InlineExecutor
            modules[module_name] = module
        key = (module_name, class_name)
        if key not in classes:
            cls = getattr(modules[module_name], class_name)
            started = time.process_time()
            cls.setUpClass()
            if timings is not None:
                timings[f"{test_id.split('::')[0]}::{class_name}"] = time.process_time() - started
            classes[key] = cls
    return modules, classes


def run_entry(modules, classes, spec, test_id, entry):
    """Run one test method restricted to one fixture entry (None: no entries). True if it passed."""
    module_name, class_name, method = _split_id(test_id)
    module, cls = modules[module_name], classes[(module_name, class_name)]
    count = spec["entries"]
    attribute = spec.get("attribute")
    if attribute:
        rows = list(getattr(cls, attribute))
        if len(rows) != count:
            raise ValueError("fixture entry count changed")
        setattr(cls, attribute, [] if entry is None else [rows[entry]])
    else:
        original = getattr(module, spec["loader"])
        target = spec["loader_argument"]

        def restricted(filename, *args, **kwargs):
            rows = list(original(filename, *args, **kwargs))
            if filename != target:
                return iter(rows)
            if len(rows) != count:
                raise ValueError("fixture entry count changed")
            return iter([] if entry is None else [rows[entry]])

        setattr(module, spec["loader"], restricted)
    outcome = _Outcome()
    cls(method).run(outcome.result)
    return outcome.passed()


def run_isolated(work, deadline, before=None, after=None):
    """Run work() in a forked grandchild; return its JSON result or None."""
    read_fd, write_fd = os.pipe()
    sys.stdout.flush()
    sys.stderr.flush()
    if before:
        before()
    pid = os.fork()
    if pid == 0:
        os.close(read_fd)
        try:
            try:
                result = work()
            except BaseException:  # noqa: BLE001 - any failure is "unknown"
                result = None
            payload = json.dumps(result).encode("utf-8")
            while payload:
                payload = payload[os.write(write_fd, payload):]
            os.close(write_fd)
        finally:
            os._exit(0)
    if after:
        after()
    os.close(write_fd)
    chunks, expired = [], False
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                expired = True
                break
            ready, _, _ = select.select([read_fd], [], [], min(.1, remaining))
            if ready:
                chunk = os.read(read_fd, 65536)
                if not chunk:
                    break
                chunks.append(chunk)
    finally:
        os.close(read_fd)
        if expired:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        _, status = os.waitpid(pid, 0)
    if expired or status:
        return None
    try:
        return json.loads(b"".join(chunks))
    except ValueError:
        return None


def replay_child(source, overlays, specs, work, wall_seconds):
    """Child entry point: [[test_id, entry, passed, fingerprint], ...] or None."""
    from native_probes import _install_overlays

    deadline = time.monotonic() + wall_seconds
    _install_overlays(source, overlays)
    own_fork = [False]

    def reject_children(event, args):
        if event == "os.fork" and own_fork[0]:
            return
        if event in ("os.fork", "os.forkpty", "os.posix_spawn", "os.exec", "os.system", "subprocess.Popen"):
            raise RuntimeError("fixture replay requires in-process tests")

    sys.addaudithook(reject_children)
    modules, classes = prepare(source, specs)

    def allow():
        own_fork[0] = True

    def deny():
        own_fork[0] = False

    results = []
    for test_id, entry in work:
        spec = specs[test_id]

        def one(test_id=test_id, entry=entry, spec=spec):
            deny()
            passed = run_entry(modules, classes, spec, test_id, entry)
            return [passed, state_fingerprint()]

        outcome = run_isolated(one, deadline, allow, deny)
        if outcome is None:
            return None
        results.append([test_id, entry, outcome[0], outcome[1]])
        if not outcome[0]:
            break  # One failing entry keeps the test; the rest cannot change that.
    return results


# --------------------------------------------------------------- parent side

def function_spans(tree):
    """[(body_start, end, qualname)] for every function, innermost resolution by span size."""
    spans = []

    def visit(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{prefix}{child.name}"
                spans.append((child.body[0].lineno, child.end_lineno, name))
                visit(child, name + ".<locals>.")
            elif isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")
            else:
                visit(child, prefix)

    visit(tree, "")
    return spans


def innermost(spans, line):
    best = None
    for start, end, name in spans:
        if start <= line <= end and (best is None or end - start < best[1] - best[0]):
            best = (start, end, name)
    return best


def _strip_bodies(tree, names):
    class Stripper(ast.NodeTransformer):
        def __init__(self):
            self.prefix = []

        def visit_ClassDef(self, node):
            self.prefix.append(node.name + ".")
            self.generic_visit(node)
            self.prefix.pop()
            return node

        def _function(self, node):
            name = "".join(self.prefix) + node.name
            if name in names:
                node.body = [ast.Pass()]
                return node
            self.prefix.append(name[len("".join(self.prefix)):] + ".<locals>.")
            self.generic_visit(node)
            self.prefix.pop()
            return node

        visit_FunctionDef = visit_AsyncFunctionDef = _function

    return ast.dump(Stripper().visit(tree), include_attributes=False)


def changed_functions(change, old, new):
    """Old-file body spans of the functions a single-file edit is confined to, or None."""
    removed, added = set(), set()
    for h in change["hunks"]:
        # Context and removed rows count from the old start, added rows from the new start.
        old_line, new_line = h["start"], h["new_start"]
        for row in h["rows"]:
            kind = row[0]
            if kind == " ":
                old_line += 1
                new_line += 1
            elif kind == "-":
                removed.add(old_line)
                old_line += 1
            elif kind == "+":
                added.add(new_line)
                new_line += 1
    if not removed and not added:
        return None
    try:
        before, after = ast.parse(old), ast.parse(new)
    except (SyntaxError, ValueError, RecursionError):
        return None
    old_spans, new_spans = function_spans(before), function_spans(after)
    names = set()
    for line in removed:
        found = innermost(old_spans, line)
        if found is None:
            return None
        names.add(found[2])
    for line in added:
        found = innermost(new_spans, line)
        if found is None:
            return None
        names.add(found[2])
    old_names = {name for _, _, name in old_spans}
    if not names <= old_names or not names <= {name for _, _, name in new_spans}:
        return None
    # Outside these bodies, the two modules must be identical: no def line,
    # decorator, default, annotation or module-level statement may change.
    if _strip_bodies(before, names) != _strip_bodies(after, names):
        return None
    return [(start, end) for start, end, name in old_spans if name in names]


def entries_for_lines(record, path, spans):
    """Entries whose recorded execution touches any line in spans; None if setup touches them."""
    base = set(record["base"].get(path, ()))
    lines = record["lines"].get(path, {})
    affected = set()
    for start, end in spans:
        for line in range(start, end + 1):
            if line in base:
                return None
            affected.update(lines.get(str(line), ()))
    return affected


def fixture_entries(source, fixture, old_text, new_text, loader_name):
    """Indices of entries whose parsed content differs, or None if entries shift or parsing fails."""
    import runpy
    import tempfile

    try:
        helpers = runpy.run_path(str(source / "tests/helpers.py"))
        load_pairs = helpers[loader_name]
        root = Path(helpers["FIXTURES_DIR"]).resolve()
        relative = (source / fixture).resolve().relative_to(root)
        before = list(load_pairs(str(relative)))
        with tempfile.TemporaryDirectory() as folder:
            patched = Path(folder) / "fixture.sql"
            patched.write_text(new_text, encoding="utf-8")
            after = list(load_pairs(str(patched)))
    except (OSError, ValueError, KeyError, TypeError, IndexError, RecursionError):
        return None
    if len(before) != len(after) or not before:
        return None
    return {i for i, (a, b) in enumerate(zip(before, after)) if a != b}


def schedule_cost(tests):
    from selector import split
    if not tests:
        return 0.0
    durations = {t["number"]: t["seconds"] for t in tests}
    workers = split(tests)
    return 2 * len(workers) + max(sum(durations[n] for n in w) for w in workers)


def cpu_seconds():
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    return time.process_time() + children.ru_utime + children.ru_stime


def plan(selector, data, mask, name, change, old, new):
    """{test_id: sorted affected entries} for selected supported tests, or {} if nothing applies."""
    tests = data.get("tests", {})
    candidates = {}
    if name.endswith(".py"):
        if not name.startswith("sqlglot/"):
            return {}
        spans = changed_functions(change, old, new)
        if not spans:
            return {}
        entry = selector.files.get(name) or {}
        unsafe = entry.get("readers", 0) | selector.external | selector.child_work
        for test_id, record in tests.items():
            bit = selector.test_bits.get(test_id, 0)
            if not bit or not mask & bit or unsafe & bit:
                continue
            affected = entries_for_lines(record, name, spans)
            # No recorded entry or setup line explains the map's selection: keep the test.
            if affected:
                candidates[test_id] = sorted(affected)
    else:
        by_fixture = {}
        for test_id, record in tests.items():
            bit = selector.test_bits.get(test_id, 0)
            if bit and mask & bit and record["fixture"] == name:
                by_fixture.setdefault(record["loader"], []).append(test_id)
        for loader_name, test_ids in by_fixture.items():
            affected = fixture_entries(selector.source.resolve(), name, old, new, loader_name)
            if affected:
                for test_id in test_ids:
                    if tests[test_id]["entries"] == len(tests[test_id]["entry_cpu"]):
                        candidates[test_id] = sorted(affected)
    return candidates


def refine(selector, data, mask, name, change, old, new, budget):
    """Return (mask, charged CPU). Drops only tests whose affected entries all verify."""
    start = cpu_seconds()
    try:
        return _refine(selector, data, mask, name, change, old, new, budget, start)
    except Exception:  # noqa: BLE001 - any doubt keeps the selection unchanged
        return mask, cpu_seconds() - start


def _refine(selector, data, mask, name, change, old, new, budget, start):
    candidates = plan(selector, data, mask, name, change, old, new)
    if not candidates:
        return mask, cpu_seconds() - start
    tests = data["tests"]
    chosen = selector.chosen(mask)
    drop_bits = sum(selector.test_bits[t] for t in candidates)
    saving = schedule_cost(chosen) - schedule_cost([t for t in chosen if not drop_bits & (1 << t["number"])])
    classes = {tuple(t.split("::")[:2]) for t in candidates}
    expected = (data.get("import_cpu", 2.0) + sum(data.get("setup_cpu", {}).get("::".join(c), 2.0) for c in classes)
                + sum(tests[t]["entry_cpu"][i] for t, entries in candidates.items() for i in entries)
                + 0.02 * sum(map(len, candidates.values())))
    # Rule: run only when the expected schedule saving exceeds the expected CPU.
    if expected * 1.25 >= saving or expected > budget:
        return mask, cpu_seconds() - start
    pinned = selector.source.resolve()
    specs = {t: tests[t] for t in candidates}
    work = [(t, i) for t, entries in sorted(candidates.items()) for i in entries]
    cpu_limit = min(budget, 2 * expected + .5)
    wall = 3 * cpu_limit + 2
    from native_probes import _forked
    outcome, _ = _forked(lambda: replay_child(pinned, {name: new}, specs, work, wall), None, wall + .5, cpu_limit)
    if not isinstance(outcome, list):
        return mask, cpu_seconds() - start
    verified = {}
    for test_id, entry, passed, fingerprint in outcome:
        recorded = tests[test_id]["fingerprints"][entry]
        ok = passed is True and fingerprint is not None and fingerprint == recorded
        verified.setdefault(test_id, []).append((entry, ok))
    for test_id, entries in candidates.items():
        results = dict(verified.get(test_id, ()))
        if all(results.get(i) is True for i in entries):
            mask &= ~selector.test_bits[test_id]
    return mask, cpu_seconds() - start
