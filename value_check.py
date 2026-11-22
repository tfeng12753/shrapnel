"""Narrow single-comparison edits with recorded operand values (`tools/value_record.py`).

The image build runs the pinned suite once with every value comparison
(`==`, `!=`, `<`, `<=`, `>`, `>=`) in a library function body observed per
test: the relation of its operands and, when short, their exact values, but
only when both were built-in `int`, `float`, `bool`, `str` or `None`, so no
user comparison code ran.

An edit is eligible when it changes exactly one such comparison and nothing
else: its operator (to another of the six) and/or a literal operand (to
another built-in literal), with the other operand unchanged. A test may be
dropped only if the record proves that every evaluation of the site in that
test completed on built-in operands and gives the same outcome after the edit
(`<` and `<=` differ only on equal operands, for example), or that the test ran
entirely in the recording process and never evaluated the comparison (the map's
line coverage cannot tell evaluations from short-circuited lines). Every other test that
the map shows executing the comparison, or that the record saw with another
operand type, an exception, too many distinct values or outside a test's call
phase, is kept. Catalog numbers follow the suite's collection order, so only
proven tests that run before the first test the edit may change are dropped:
that test could leave different state behind.
"""
from __future__ import annotations

import ast
import gzip
import json
import operator
from pathlib import Path

VERSION = 1
OPERATORS = {ast.Eq: "Eq", ast.NotEq: "NotEq", ast.Lt: "Lt", ast.LtE: "LtE", ast.Gt: "Gt", ast.GtE: "GtE"}
FUNCTIONS = {"Eq": operator.eq, "NotEq": operator.ne, "Lt": operator.lt, "LtE": operator.le,
             "Gt": operator.gt, "GtE": operator.ge}
TYPES = {"i": int, "f": float, "b": bool, "s": str, "n": type(None)}
TAGS = {v: k for k, v in TYPES.items()}
_LOADED = {}


def load(path):
    path = Path(path)
    if path not in _LOADED:
        try:
            with gzip.open(path, "rt", encoding="utf-8") as f:
                data = json.load(f)
            _LOADED[path] = data if data.get("version") == VERSION and data.get("exit") in (0, 1) else None
        except (OSError, ValueError, TypeError, EOFError):
            _LOADED[path] = None
    return _LOADED[path]


def relation_outcome(state, op):
    """Outcome of `op` for a recorded relation state, or None if the state cannot decide it."""
    kind, _, bits = state.partition(":")
    if kind not in ("e", "p") or not bits.isdigit():
        return None
    value = int(bits)
    if op == "Eq":
        return bool(value & 1)
    if op == "NotEq":
        return not value & 1
    if kind != "p":
        return None  # Ordering raised TypeError on these operands.
    return {"Lt": bool(value & 2), "LtE": bool(value & 3), "Gt": bool(value & 4), "GtE": bool(value & 5)}.get(op)


def _literal(node):
    """(found, value) for a built-in literal operand (negative numbers included)."""
    if isinstance(node, ast.Constant) and type(node.value) in TAGS:
        return True, node.value
    if (isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) and isinstance(node.operand, ast.Constant)
            and type(node.operand.value) in (int, float)):
        return True, -node.operand.value
    return False, None


def _same(a, b):
    return ast.dump(a, include_attributes=False) == ast.dump(b, include_attributes=False)


def comparison_edit(before, after):
    """Describe an edit confined to one comparison, or None.

    Returns (site, old op, new op, side, old literal, new literal) where side is
    None (operator only), 0 (left literal changed) or 1 (right literal changed).
    """
    found = []

    def diff(a, b):
        if type(a) is not type(b):
            return False
        if isinstance(a, list):
            return len(a) == len(b) and all(diff(x, y) for x, y in zip(a, b))
        if not isinstance(a, ast.AST):
            return a == b and type(a) is type(b)
        if isinstance(a, ast.TypeIgnore):
            return a.tag == b.tag
        if isinstance(a, ast.Compare) and not _same(a, b):
            found.append((a, b))
            return True
        return all(diff(getattr(a, f, None), getattr(b, f, None)) for f in a._fields)

    if not diff(before, after) or len(found) != 1:
        return None
    a, b = found[0]
    if len(a.ops) != 1 or len(b.ops) != 1 or type(a.ops[0]) not in OPERATORS or type(b.ops[0]) not in OPERATORS:
        return None
    old_op, new_op = OPERATORS[type(a.ops[0])], OPERATORS[type(b.ops[0])]
    sides = [(a.left, b.left), (a.comparators[0], b.comparators[0])]
    changed = [i for i, (x, y) in enumerate(sides) if not _same(x, y)]
    site = f"{a.lineno}:{a.col_offset}"
    if not changed:
        return (site, old_op, new_op, None, None, None) if old_op != new_op else None
    if len(changed) != 1:
        return None
    side = changed[0]
    ok_old, old_value = _literal(sides[side][0])
    ok_new, new_value = _literal(sides[side][1])
    if not ok_old or not ok_new:
        return None
    return site, old_op, new_op, side, old_value, new_value


class Record:
    """Decoded view of the record for one selector's catalog."""

    def __init__(self, data, test_bits):
        self.data = data
        index_bits = [test_bits.get(t, 0) for t in data["tests"]]
        self.index_bits = index_bits
        self.failed = self.bits_of(data.get("failed", ()))
        self.ran = self.bits_of(data.get("ran", ()))
        # Tests whose whole call phase ran observed in the recording process.
        self.observed = 0 if data.get("outside_spawn", True) or "external" not in data else (
            self.ran & ~self.bits_of(data["external"]) & ~self.failed)
        self.global_sites = set(data.get("global", ()))

    def bits_of(self, indices):
        result = 0
        for i in indices:
            if 0 <= i < len(self.index_bits):
                result |= self.index_bits[i]
        return result

    def decode(self, value):
        mask = int(value, 16)
        result, i = 0, 0
        while mask:
            if mask & 1:
                if i >= len(self.index_bits):
                    raise ValueError("record mask beyond its tests")
                result |= self.index_bits[i]
            mask >>= 1
            i += 1
        return result

    def site(self, name, site):
        if f"{name}:{site}" in self.global_sites:
            return None
        return self.data["files"].get(name, {}).get(site)


def evaluate(record, name, edit, touched):
    """Tests to keep among `touched` (map users of the comparison), or None without evidence."""
    site, old_op, new_op, side, old_value, new_value = edit
    info = record.site(name, site)
    if info is None or info["op"] != old_op:
        return None
    reached = record.decode(info["reached"])
    unknown = record.decode(info["unknown"]) | record.failed
    touched |= reached
    changed = 0
    if side is None:
        for state, users in info["states"].items():
            a, b = relation_outcome(state, old_op), relation_outcome(state, new_op)
            if a is None or b is None or a != b:
                changed |= record.decode(users)
        evidenced = reached
    else:
        # Literal edits need every distinct operand pair of the test.
        evidenced = 0
        old_function, new_function = FUNCTIONS[old_op], FUNCTIONS[new_op]
        for index, pairs in info["pairs"].items():
            bit = record.bits_of([int(index)])
            evidenced |= bit
            for ta, a, tb, b in pairs:
                values = [_typed(ta, a), _typed(tb, b)]
                if None in (values[0][0], values[1][0]) or values[side][1] != old_value \
                        or type(values[side][1]) is not type(old_value):
                    changed |= bit
                    break
                new_values = [values[0][1], values[1][1]]
                new_values[side] = new_value
                try:
                    if bool(old_function(values[0][1], values[1][1])) != bool(new_function(*new_values)):
                        changed |= bit
                        break
                except TypeError:
                    changed |= bit
                    break
    safe = reached & evidenced & ~changed & ~unknown & record.ran
    # Fully observed tests that never evaluated the comparison cannot reach the edit.
    safe |= record.observed & ~reached
    unsafe = touched & ~safe
    if unsafe:
        safe &= (unsafe & -unsafe) - 1
    return touched & ~safe


def _typed(tag, value):
    """(ok, value) restoring a recorded operand; ok is None if the tag is unknown."""
    kind = TYPES.get(tag)
    if kind is None:
        return None, None
    if kind is float and not isinstance(value, float):
        value = float(value)
    if type(value) is not kind:
        return None, None
    return True, value
