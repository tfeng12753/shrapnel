"""Verify the executor's generated Python code for expensive executor tests.

The image build records, for each supported executor test, every call of the
executor's Python code generator on the pinned checkout (its input expression
and the code it returned), and which function or lambda code of the library
starts executing only inside such calls (`tools/generation_record.py`).

A single-file edit is eligible when everything it changes lies inside the
bodies of functions, lambdas or generator expressions whose code, on the
pinned checkout, started and resumed only inside generator calls during the
test, and those bodies cannot write global or shared state. Until the first
call of changed code, a patched run of the test is identical to the pinned one,
so the edit can only act through generator calls. If the test's calls never
ran changed code, the test never ran it at all. Otherwise every recorded call
is replayed on the patched files in a forked child; the test is dropped only if every replayed call returns
equivalent code (identical text or an identical Python syntax tree), leaves its
input unchanged as recorded, and the replay changes global state exactly as the
baseline replay did. Any error, timeout, mismatch or missing evidence keeps the
test.
"""
from __future__ import annotations

import ast
import gzip
import hashlib
import json
import os
import resource
import sys
import time
from pathlib import Path

VERSION = 1
AUDITED = ("tests/test_executor.py", "tests/helpers.py")
OWNERS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.GeneratorExp)
# Calls that can reach or replace global state, run code or touch files.
BANNED_CALLS = {"setattr", "delattr", "exec", "eval", "globals", "vars", "locals", "__import__", "compile",
                "open", "breakpoint", "input", "exit", "quit"}
_LOADED = {}


def load(path):
    path = Path(path)
    if path in _LOADED:
        return _LOADED[path]
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            data = json.load(f)
        data = data if data.get("version") == VERSION else None
        for record in (data or {}).get("tests", {}).values():
            record["keys"] = set(record["keys"])
    except (OSError, ValueError, TypeError, EOFError):
        data = None
    _LOADED[path] = data
    return data


def cpu_seconds():
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    return time.process_time() + children.ru_utime + children.ru_stime


# ------------------------------------------------------------ static analysis

def code_key(name, node):
    """Key of the code object a function, lambda or generator expression compiles to."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        first = min([node.lineno, *(d.lineno for d in node.decorator_list)])
        return f"{name}:{first}:{node.name}"
    if isinstance(node, ast.Lambda):
        return f"{name}:{node.lineno}:<lambda>"
    return f"{name}:{node.lineno}:<genexpr>"


def _same_value(a, b):
    return type(a) is type(b) and (repr(a) == repr(b) if isinstance(a, float) else a == b)


def changed_owners(before, after):
    """Innermost function/lambda/generator-expression nodes (of `before`) containing every
    difference between the trees, or None if any difference lies outside such a body."""
    owners = []

    def diff(a, b, owner):
        if type(a) is not type(b):
            return mark(owner)
        if isinstance(a, list):
            if len(a) != len(b):
                return mark(owner)
            return all(diff(x, y, owner) for x, y in zip(a, b))
        if not isinstance(a, ast.AST):
            return True if _same_value(a, b) else mark(owner)
        for field in a._fields:
            inner = owner
            if isinstance(a, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)) and field == "body":
                inner = a
            elif isinstance(a, ast.GeneratorExp) and field == "elt":
                inner = a
            if isinstance(a, ast.GeneratorExp) and field == "generators":
                x, y = a.generators, b.generators
                if len(x) != len(y):
                    return mark(owner)
                # The first iterable is evaluated where the expression is created.
                for i, (gx, gy) in enumerate(zip(x, y)):
                    for sub in gx._fields:
                        scope = owner if (i == 0 and sub == "iter") else a
                        if not diff(getattr(gx, sub, None), getattr(gy, sub, None), scope):
                            return False
                continue
            if not diff(getattr(a, field, None), getattr(b, field, None), inner):
                return False
        return True

    def mark(owner):
        if owner is None:
            return False
        if all(owner is not o for o in owners):
            owners.append(owner)
        return True

    if not diff(before, after, None) or not owners:
        return None
    return owners


def counterpart(before, after, node):
    """The node of `after` at the same tree position as `node` in `before`."""
    path = []

    def find(current, target, trail):
        if current is target:
            path.extend(trail)
            return True
        for field, value in ast.iter_fields(current):
            if isinstance(value, list):
                for i, item in enumerate(value):
                    if isinstance(item, ast.AST) and find(item, target, trail + [(field, i)]):
                        return True
            elif isinstance(value, ast.AST) and find(value, target, trail + [(field, None)]):
                return True
        return False

    if not find(before, node, []):
        return None
    current = after
    for field, index in path:
        current = getattr(current, field)
        if index is not None:
            current = current[index]
    return current


def writes_shared_state(node):
    """True if an owner body may write state other than its own fresh locals."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.decorator_list:
        return True  # Caching or registration wrappers can carry results out of a call.
    body = node.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else [node]
    created = set()
    for stmt in body:
        for sub in ast.walk(stmt):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                created.add(sub.id)
    params = set()
    if not isinstance(node, ast.GeneratorExp):
        args = node.args
        params = {a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg] if a}
    for stmt in body:
        for sub in ast.walk(stmt):
            if isinstance(sub, (ast.Global, ast.Nonlocal, ast.Import, ast.ImportFrom, ast.Yield, ast.YieldFrom,
                                ast.Await)):
                return True
            if isinstance(sub, (ast.Attribute, ast.Subscript)) and isinstance(sub.ctx, (ast.Store, ast.Del)):
                base = sub.value
                while isinstance(base, (ast.Attribute, ast.Subscript)):
                    base = base.value
                if not isinstance(base, ast.Name) or base.id not in created or base.id in params:
                    return True
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) and sub.func.id in BANNED_CALLS:
                return True
            if isinstance(sub, ast.Attribute) and sub.attr.startswith("__") and sub.attr.endswith("__"):
                return True
    return False


def compiled_keys(name, text):
    """{code key: count} for every code object compiled from a file, or None."""
    try:
        pending = [compile(text, name, "exec", dont_inherit=True)]
    except (SyntaxError, ValueError, RecursionError):
        return None
    counts = {}
    while pending:
        code = pending.pop()
        key = f"{name}:{code.co_firstlineno}:{code.co_name}"
        counts[key] = counts.get(key, 0) + 1
        pending.extend(c for c in code.co_consts if hasattr(c, "co_consts"))
    return counts


def eligible_keys(name, old, new):
    """Code keys an edit is confined to, or None if it is not eligible."""
    try:
        before, after = ast.parse(old), ast.parse(new)
    except (SyntaxError, ValueError, RecursionError):
        return None
    owners = changed_owners(before, after)
    if not owners:
        return None
    keys = set()
    for owner in owners:
        other = counterpart(before, after, owner)
        if other is None or type(other) is not type(owner):
            return None
        if writes_shared_state(owner) or writes_shared_state(other):
            return None
        keys.add(code_key(name, owner))
    return keys


# ------------------------------------------------------------------- planning

def _audited_files_match(source, checkout):
    """The executor test module and its helpers are the ones recorded."""
    for audited in AUDITED:
        try:
            if hashlib.sha256((source / audited).read_bytes()).hexdigest() != checkout.get(audited):
                return False
        except OSError:
            return False
    return True


def plan(selector, data, mask, name, old, new):
    """{test id: whether its generator calls must be replayed} for selected tests this edit
    can only reach through generator calls; {} if nothing applies."""
    if not (name.startswith("sqlglot/") and name.endswith(".py")):
        return {}
    checkout = data.get("checkout", {})
    if checkout.get(name) != hashlib.sha256(old.encode("utf-8")).hexdigest():
        return {}
    if not _audited_files_match(selector.source, checkout):
        return {}
    keys = eligible_keys(name, old, new)
    if not keys:
        return {}
    # Every changed code object must exist exactly once on the pinned file (so its key
    # names it alone) and must never have run outside generator calls. Code that never
    # ran at all in the recorded tests cannot have affected them.
    compiled = compiled_keys(name, old)
    if compiled is None or any(compiled.get(key) != 1 for key in keys):
        return {}
    if keys & set(data.get("outside", ())):
        return {}
    entry = selector.files.get(name) or {}
    unsafe = entry.get("readers", 0) | selector.external | selector.child_work
    candidates = {}
    for test_id, record in data.get("tests", {}).items():
        bit = selector.test_bits.get(test_id, 0)
        if not bit or not mask & bit or unsafe & bit:
            continue
        # A test whose generator calls never ran changed code never ran it at all (the
        # code ran nowhere else): it needs no replay. Otherwise every call is replayed,
        # so the global-state change compares with the recorded baseline replay.
        candidates[test_id] = bool(keys & set(record["keys"]))
    return candidates


def expected_cpu(data, candidates):
    """Expected replay CPU: fork and pipe overhead plus each test's recorded baseline replay."""
    total = 0.05
    for test_id in candidates:
        record = data["tests"][test_id]
        total += record["replay_cpu"] if "state" in record and "replay_cpu" in record else float("inf")
    return total


def refine(selector, data, mask, name, change, old, new, budget, saving_of):
    """Return (mask, charged CPU). saving_of(mask, drop bits) is the schedule saving of dropping
    those tests from that selection."""
    start = cpu_seconds()
    try:
        return _refine(selector, data, mask, name, old, new, budget, saving_of, start)
    except Exception:  # noqa: BLE001 - any doubt keeps the selection unchanged
        return mask, cpu_seconds() - start


def _refine(selector, data, mask, name, old, new, budget, saving_of, start):
    candidates = plan(selector, data, mask, name, old, new)
    for test_id in [t for t, replay in candidates.items() if not replay]:
        mask &= ~selector.test_bits[test_id]
        del candidates[test_id]
    if not candidates:
        return mask, cpu_seconds() - start
    drop = sum(selector.test_bits[t] for t in candidates)
    expected = expected_cpu(data, candidates)
    saving = saving_of(mask, drop)
    # Run only when the schedule saving of the whole group pays for the expected CPU.
    if expected * 1.5 + .25 >= saving or expected > budget:
        return mask, cpu_seconds() - start
    cpu_limit = min(budget, 2 * expected + .5)
    wall = 3 * cpu_limit + 2
    from native_probes import _forked
    pinned = selector.source.resolve()
    outcome, _ = _forked(lambda: _child(pinned, {name: new}, data, candidates), None, wall, cpu_limit)
    if not isinstance(outcome, dict):
        return mask, cpu_seconds() - start
    for test_id in candidates:
        if outcome.get(test_id) is True:
            mask &= ~selector.test_bits[test_id]
    return mask, cpu_seconds() - start


# ---------------------------------------------------------------- child side

def _child(source, overlays, data, candidates):
    results = {}
    for test_id in candidates:
        matched, fingerprint, _ = replay_in_child(source, overlays, data, test_id, fork=True)
        results[test_id] = bool(matched and fingerprint is not None
                                and fingerprint == data["tests"][test_id].get("state"))
    return results


def equivalent(expected, actual):
    if expected == actual:
        return True
    try:
        return (ast.dump(ast.parse(expected, mode="eval")) == ast.dump(ast.parse(actual, mode="eval")))
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return False


def replay_in_child(source, overlays, data, test_id, *, fork):
    """(all calls equivalent, global-state change digest, CPU). With fork, each test replays in its
    own forked grandchild so tests cannot see each other's state."""
    if fork:
        from fixture_replay import run_isolated
        outcome = run_isolated(lambda: list(_replay(source, overlays, data, test_id)),
                               time.monotonic() + 600)
        return tuple(outcome) if isinstance(outcome, list) else (False, None, 0.0)
    return _replay(source, overlays, data, test_id)


def _replay(source, overlays, data, test_id):
    if any(m == "sqlglot" or m.startswith("sqlglot.") for m in sys.modules):
        return False, None, 0.0  # Overlays apply only to modules imported after they are installed.
    started = time.process_time()
    from native_probes import _install_overlays
    from fixture_replay import state_fingerprint, state_summary
    _install_overlays(source, overlays)
    import sqlglot  # noqa: F401
    from sqlglot import serde
    from sqlglot.executor.python import Python
    for module in [n for n in sys.modules if n == "tests" or n.startswith("tests.")]:
        del sys.modules[module]
    import tests.helpers  # noqa: F401 - state compared like the recording's
    before = state_summary()
    record = data["tests"][test_id]
    generator = Python().generator(identify=True, comments=False)
    for text, expected, _ in record["calls"]:
        expression = serde.load(json.loads(text))
        try:
            actual = generator.generate(expression)
        except Exception:  # noqa: BLE001 - the pinned call returned
            return False, None, time.process_time() - started
        if not isinstance(actual, str) or not equivalent(expected, actual):
            return False, None, time.process_time() - started
        # The executor reuses its expressions after generating code from them.
        if json.dumps(serde.dump(expression), sort_keys=True) != text:
            return False, None, time.process_time() - started
    return True, state_fingerprint(before, state_summary()), time.process_time() - started
