"""Compare the fixture fields the pinned executor tests actually consume."""
import ast
import hashlib
from pathlib import Path
import runpy
import tempfile

CLASS = "tests/test_executor.py::TestExecutor"

# The audited test submits (metadata, SQL) in fixture order and never reads
# expected SQL. Re-audit its setup, worker and loader if either module changes.
SUITE_HASH = "270a844a7b0b84045efde017135e6208ca07d7d93eba395be17c20c6e918923d"
LOADER_HASH = "c0dc285fccd1b8bee4a451f5962118fe771de005843b56e3af4c7136f74c3ed3"


def fixture_names(suite):
    """Resolve the file bound to the executor's list by its audited class setup."""
    cls, = (node for node in ast.parse(suite).body
            if isinstance(node, ast.ClassDef) and node.name == "TestExecutor")
    setup, = (node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "setUpClass")
    names = {}
    for node in setup.body:
        if isinstance(node, ast.Assign) and ast.unparse(node.targets[0]) in ("cls.tpcds_sqls", "cls.tpch_sqls"):
            call = node.value.args[0]
            if isinstance(call, ast.Call) and ast.unparse(call.func) == "load_sql_fixture_pairs":
                fixture = node.targets[0].attr.removesuffix("_sqls")
                names[ast.literal_eval(call.args[0])] = f"{CLASS}::test_execute_{fixture}"
    return names


def executor_inputs(rows):
    # Preserve metadata insertion order as well as the order of the queries.
    return [(tuple(meta.items()), sql) for meta, sql, expected in rows]


def fixture_effect(source, name, patched_text, *, query_check=None):
    """Return (executor item, unchanged inputs) only if the pinned setup's loader succeeds."""
    try:
        source = source.resolve()
        suite = (source / "tests/test_executor.py").read_bytes()
        helpers = source / "tests/helpers.py"
        if (hashlib.sha256(suite).hexdigest() != SUITE_HASH
                or hashlib.sha256(helpers.read_bytes()).hexdigest() != LOADER_HASH):
            return None
        loader = runpy.run_path(str(helpers))
        root = Path(loader["FIXTURES_DIR"])
        path = source / name
        item = fixture_names(suite).get(path.relative_to(root).as_posix())
        if not item or path.resolve() != path:
            return None
        load = loader["load_sql_fixture_pairs"]
        expected = executor_inputs(load(str(path)))
        with tempfile.TemporaryDirectory() as folder:
            patched = Path(folder) / path.name
            patched.write_text(patched_text, encoding="utf-8")
            actual = executor_inputs(load(str(patched)))
        identical = bool(expected) and expected == actual
        if query_check and not identical:
            identical = query_check(source, item, expected, actual)
        return item, identical
    except (OSError, ValueError, KeyError, IndexError, TypeError, SyntaxError, RecursionError):
        return None
