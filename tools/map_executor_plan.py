"""Fingerprint a pinned SQLGlot plan at the Python executor boundary (offline audit)."""
import argparse
import hashlib
import json
import os
import pickle
import sys
from enum import Enum
from types import ModuleType
from pathlib import Path
from weakref import WeakKeyDictionary


def digest(value):
    raw = json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def checkout_digest(source):
    signature = hashlib.sha256()
    files = list((source / "sqlglot").rglob("*.py")) + list((source / "tests").rglob("*.py"))
    files.extend(p for p in (source / "tests" / "fixtures").rglob("*") if p.is_file() and p.suffix != ".py")
    for path in sorted(files):
        signature.update(str(path.relative_to(source)).encode() + b"\0")
        signature.update(hashlib.sha256(path.read_bytes()).digest())
    return signature.hexdigest()


def executor_settings(executor):
    def encode(value):
        if isinstance(value, Enum):
            return [type(value).__qualname__, value.name]
        if isinstance(value, ModuleType):
            return ["module", value.__name__]
        if callable(value):
            return ["callable", value.__module__, value.__qualname__]
        if isinstance(value, dict):
            return [[encode(k), encode(v)] for k, v in value.items()]
        if isinstance(value, (list, tuple)):
            return [encode(v) for v in value]
        if value is None or type(value) in (int, str, bool):
            return value
        raise TypeError(f"unsupported executor setting: {type(value)}")

    generator = executor.generator
    dialect = generator.dialect
    options = {key: getattr(generator, key) for key in generator.__slots__ if key != "dialect"}
    return digest([encode(options), type(dialect).__qualname__, encode(vars(dialect)), encode(executor.env),
                   encode(executor._subquery_plans), encode(executor._plan_names_by_sql),
                   encode(executor._ctes), encode(executor._outer_scope)])


def plan_state(plan, creation):
    from sqlglot import exp
    from sqlglot.planner import Step

    nodes = sorted(plan.dag, key=creation.__getitem__)
    ids = {node: index for index, node in enumerate(nodes)}

    def encode(value):
        if isinstance(value, Step):
            return ["step", ids[value]]
        if isinstance(value, exp.Expr):
            # SQLGlot serialization omits explicit None and empty list arguments.
            empty_args = [[i, key, arg is None] for i, node in enumerate(value.walk())
                          for key, arg in node.args.items()
                          if arg is None or (type(arg) is list and not arg)]
            return ["expr", value.dump(), empty_args]
        if isinstance(value, type) and issubclass(value, exp.Expr):
            return ["class", value.__module__, value.__qualname__]
        if type(value) in (tuple, list):
            return [type(value).__name__, [encode(v) for v in value]]
        if type(value) is set and all(isinstance(v, Step) for v in value):
            return ["edges", sorted(ids[v] for v in value)]
        if type(value) is dict:
            return ["dict", [[encode(k), encode(v)] for k, v in value.items()]]
        if type(value) is float:
            return ["float", value.hex()]
        if value is None or type(value) in (bool, str, int):
            return value
        raise TypeError(f"unsupported plan field: {type(value)}")

    attributes = dict(vars(plan))
    dag = attributes.pop("_dag")
    graph = [[ids[n], encode(dag[n])] for n in nodes]
    return [encode(attributes), graph, [[type(n).__name__, encode(vars(n))] for n in nodes]]


class PlanMismatch(Exception):
    """The original executor test must stay scheduled."""


def snapshot(source, fixture, schema_name, output, downstream_output=None, reference=None):
    # This experiment mirrors the pinned worker's parse/transport and optimizer
    # path. The original pytest item is the independent validation oracle.
    sys.path.insert(0, str(source))
    from sqlglot import parse_one, transpile
    from sqlglot.executor.table import Table
    from sqlglot.executor.python import PythonExecutor
    from sqlglot.planner import Step
    from tests import helpers
    from tests import test_executor as suite

    creation = WeakKeyDictionary()
    ordinal = 0
    original_init = Step.__init__
    original_execute = PythonExecutor.execute
    records = []
    downstream = set()
    observed_modules = set()

    def profile(frame, event, arg):
        if event == "call":
            namespace = frame.f_globals
            module = namespace.get("__name__", "")
            if module.startswith("sqlglot.") and module not in observed_modules:
                path = Path(namespace["__file__"]).resolve().relative_to(source)
                downstream.add(path.as_posix())
                observed_modules.add(module)

    def init(step):
        nonlocal ordinal
        original_init(step)
        ordinal += 1
        creation[step] = ordinal

    def capture(executor, plan, outer_scope=None):
        if type(executor) is not PythonExecutor or outer_scope is not None:
            raise ValueError("unexpected executor settings")
        tables = []
        for name, table in executor.tables.mapping.items():
            if type(table) is not Table:
                raise ValueError("unsupported table implementation")
            data = pickle.dumps((table.columns, table.column_range, table.rows), protocol=5)
            tables.append([name, hashlib.sha256(data).hexdigest()])
        records.append(digest([plan_state(plan, creation), tables, executor_settings(executor)]))
        if downstream_output is not None:
            previous = sys.getprofile()
            PythonExecutor.execute = original_execute
            sys.setprofile(profile)
            try:
                return original_execute(executor, plan)
            finally:
                sys.setprofile(previous)
                PythonExecutor.execute = capture
        return None

    cls = suite.TestExecutor
    cls.setUpClass()
    try:
        schema = getattr(helpers, schema_name)
        context = [fixture, schema_name, digest(schema), os.environ.get("PYTHONHASHSEED"),
                   list(sys.version_info[:3])]
        if reference is not None and reference["context"] != context:
            raise PlanMismatch("snapshot context differs")
        tables = getattr(cls, fixture + "_tables")
        queries = getattr(cls, fixture + "_sqls")
        suite.initializer(schema, tables)
        Step.__init__, PythonExecutor.execute = init, capture
        trace = []
        for meta, sql, _ in queries:
            expression = pickle.loads(pickle.dumps(parse_one(sql)))
            count = len(records)
            suite.mp_execute(expression, meta)
            if len(records) != count + bool(meta.get("execute")):
                raise ValueError("unexpected number of executor calls")
            reference_sql = transpile(sql, write="duckdb") if len(records) > count else None
            row = [meta, sql, records[count:], reference_sql]
            if reference is not None and (len(trace) >= len(reference["queries"])
                                          or row != reference["queries"][len(trace)]):
                raise PlanMismatch(f"query {len(trace) + 1} differs")
            trace.append(row)
        if not records:
            raise ValueError("fixture never reached the executor boundary")
        if reference is None:
            output.write_text(json.dumps({"context": context, "queries": trace}))
        elif len(trace) != len(reference["queries"]):
            raise PlanMismatch("query count differs")
        else:
            output.write_text(json.dumps({"matched": True, "queries": len(trace)}))
        if downstream_output is not None:
            downstream_output.write_text(json.dumps({"checkout": checkout_digest(source),
                                                     "snapshot": digest({"context": context, "queries": trace}),
                                                     "modules": sorted(downstream)}))
    finally:
        Step.__init__, PythonExecutor.execute = original_init, original_execute
        cls.tearDownClass()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--observe-downstream", type=Path, help="Run the real executor and record SQLGlot modules it calls")
    parser.add_argument("--reference", type=Path, help="Compare the full stream and stop at the first mismatch")
    args = parser.parse_args()
    if not os.environ.get("PYTHONHASHSEED", "").isdigit():
        parser.error("set a fixed PYTHONHASHSEED for reproducible table bindings")
    if args.reference and args.observe_downstream:
        parser.error("observe downstream calls on the unmodified baseline")
    try:
        snapshot(args.source.resolve(), args.fixture, args.schema, args.output.resolve(),
                 args.observe_downstream.resolve() if args.observe_downstream else None,
                 json.loads(args.reference.read_text()) if args.reference else None)
    except PlanMismatch as exc:
        args.output.write_text(json.dumps({"matched": False, "reason": str(exc)}))
