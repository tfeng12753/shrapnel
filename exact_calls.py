"""Replay pinned highlight_sql calls from the full-size parser limit test."""

import ast
import copy
import gzip
import hashlib
import json
from pathlib import Path

from native_probes import _forked, _install_overlays

SOURCE = "sqlglot/errors.py"
TEST = "tests/test_parser.py::TestParser::test_max_nodes"
RECORDING = Path(__file__).with_name("parser_calls.json.gz")
RECORDING_SHA256 = "96455ebb83196d034cc2b8e4da3f1619e1deecc672b8d23a65bb6687159f2ef7"
COMPARISONS = (ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq)


def body_shape(body):
    tree = ast.Module(body=copy.deepcopy(body), type_ignores=[])
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and type(node.value) is int:
            node.value = 0
        elif isinstance(node, ast.Compare):
            node.ops = [
                ast.Eq() if isinstance(op, COMPARISONS) else op for op in node.ops
            ]
    return ast.dump(tree)


def supported(old, new):
    before, after = (
        ast.parse(old, type_comments=True),
        ast.parse(new, type_comments=True),
    )
    index = next(
        i
        for i, node in enumerate(before.body)
        if isinstance(node, ast.FunctionDef) and node.name == "highlight_sql"
    )
    original, updated = before.body[index], after.body[index]
    if not isinstance(updated, ast.FunctionDef) or body_shape(
        original.body
    ) != body_shape(updated.body):
        return False
    updated.body = original.body
    return ast.dump(before) == ast.dump(after)


def refine(selector, patch, mask, parse_patch, apply_change):
    target = selector.test_bits.get(TEST, 0)
    if not selector.complete or not target & mask or SOURCE not in patch:
        return mask
    try:
        changes = parse_patch(patch)
        if (
            len(changes) != 1
            or changes[0]["old"] != SOURCE
            or changes[0]["new"] != SOURCE
        ):
            return mask
        old = selector.read_source(SOURCE)
        new, _, _ = apply_change(old, changes[0])
        if not supported(old, new):
            return mask

        def replay():
            _install_overlays(selector.source, {SOURCE: new})
            from sqlglot.errors import highlight_sql

            payload = gzip.decompress(RECORDING.read_bytes())
            if hashlib.sha256(payload).hexdigest() != RECORDING_SHA256:
                return False
            records = json.loads(payload)
            for args, kwargs, expected in records:
                if "positions" in kwargs:
                    kwargs["positions"] = [tuple(pair) for pair in kwargs["positions"]]
                if list(highlight_sql(*args, **kwargs)) != expected:
                    return False
            return bool(records)

        passed, _ = _forked(replay, False, wall_seconds=4, cpu_seconds=2)
        return mask & ~target if passed is True else mask
    except (
        OSError,
        ValueError,
        SyntaxError,
        IndexError,
        StopIteration,
        RecursionError,
    ):
        return mask
