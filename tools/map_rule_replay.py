"""Capture optimizer calls on the pinned checkout and screen one edited module."""
import argparse
from contextlib import ExitStack
from copy import deepcopy
import gzip
import hashlib
import inspect
import json
import os
from pathlib import Path
import sys

from map_executor_plan import digest


def expression_state(expression):
    # The native expression dump omits empty arguments.
    return [expression.dump(), [[i, k, v is None] for i, node in enumerate(expression.walk())
                                for k, v in node.args.items() if v is None or v == []]]


def record_input(expression):
    layouts = []
    for index, node in enumerate(expression.walk()):
        empty = {k: None if v is None else [] for k, v in node.args.items()
                 if v is None or (type(v) is list and not v)}
        if empty:
            layouts.append([index, list(node.args), empty])
    return [deepcopy(expression.dump()), layouts]


def restore_input(inputs):
    from sqlglot import exp

    payload, layouts = inputs
    expression = exp.Expr.load(payload)
    nodes = list(expression.walk())
    for index, keys, empty in layouts:
        args = nodes[index].args
        args.update(empty)
        nodes[index].args = {key: args[key] for key in keys}
    return expression


def capture(source, snapshot, folder):
    from sqlglot import find_tables, parse_one
    from sqlglot.optimizer.optimizer import RULES, optimize
    from tests.helpers import TPCDS_SCHEMA

    tables = []

    def wrap(rule, stream):
        def observed(expression, **kwargs):
            inputs = record_input(expression)
            arguments = dict(kwargs)
            schema_argument = "schema" in arguments
            if schema_argument:
                del arguments["schema"]
            output = rule(expression, **kwargs)
            row = [inputs, arguments, schema_argument, digest(expression_state(output))]
            stream.write(json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n")
            return output

        observed.__signature__ = inspect.signature(rule)
        return observed

    names = [rule.__module__ + "." + rule.__name__ for rule in RULES]
    inventory = [sql for _, sql, plans, _ in snapshot["queries"] if plans]
    if not inventory or len(set(names)) != len(names):
        raise ValueError("ambiguous rule or query inventory")
    with ExitStack() as stack:
        rules = []
        for rule, key in zip(RULES, names):
            stream = stack.enter_context(gzip.open(folder / (key + ".json.gz"), "wt", compresslevel=1))
            rules.append(wrap(rule, stream))
        for sql in inventory:
            expr = parse_one(sql)
            tables.append(sorted(t.name for t in find_tables(expr)))
            optimize(expr, TPCDS_SCHEMA, leave_tables_isolated=True, dialect=None, rules=rules)

    manifest = {"seed": os.environ["PYTHONHASHSEED"], "snapshot": digest(snapshot),
                "queries": inventory, "tables": tables, "rules": {}}
    for rule, key in zip(RULES, names):
        path = folder / (key + ".json.gz")
        module = Path(inspect.getfile(rule)).resolve().relative_to(source).as_posix()
        manifest["rules"].setdefault(module, []).append([key, hashlib.sha256(path.read_bytes()).hexdigest()])
    (folder / "manifest.json").write_text(json.dumps(manifest))


def matches(folder, snapshot, module):
    from sqlglot import find_tables, parse_one
    from sqlglot.optimizer.optimizer import RULES
    from sqlglot.schema import ensure_schema
    from tests.helpers import TPCDS_SCHEMA

    manifest = json.loads((folder / "manifest.json").read_text())
    inventory = [sql for _, sql, plans, _ in snapshot["queries"] if plans]
    if (not inventory or digest(snapshot) != manifest["snapshot"] or inventory != manifest["queries"]
            or manifest["seed"] != os.environ["PYTHONHASHSEED"] or len(manifest["tables"]) != len(inventory)):
        return False
    rules = {rule.__module__ + "." + rule.__name__: rule for rule in RULES}
    selected = manifest["rules"].get(module, [])
    if not selected:
        return False
    for sql, names in zip(inventory, manifest["tables"]):
        if sorted(t.name for t in find_tables(parse_one(sql))) != names:
            return False
    for key, checksum in selected:
        path = folder / (key + ".json.gz")
        if hashlib.sha256(path.read_bytes()).hexdigest() != checksum:
            return False
        with gzip.open(path, "rt") as stream:
            for _ in inventory:
                line = stream.readline()
                if not line:
                    return False
                inputs, kwargs, needs_schema, expected = json.loads(line)
                if needs_schema:
                    kwargs["schema"] = ensure_schema(TPCDS_SCHEMA)
                if digest(expression_state(rules[key](restore_input(inputs), **kwargs))) != expected:
                    return False
            if stream.readline():
                return False
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--schema", choices=["TPCDS_SCHEMA"], required=True)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--folder", required=True, type=Path)
    parser.add_argument("--module")
    args = parser.parse_args()
    sys.path.insert(0, str(args.source.resolve()))
    snapshot = json.loads(args.snapshot.read_text())
    if args.module:
        sys.exit(0 if matches(args.folder, snapshot, args.module) else 1)
    args.folder.mkdir()
    capture(args.source.resolve(), snapshot, args.folder)
