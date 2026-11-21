"""Compare the fixture fields the pinned executor tests actually consume."""
import ast
import hashlib
from pathlib import Path
import runpy
import tempfile

from selector import apply_change

# The audited test submits (metadata, SQL) in fixture order and never reads
# expected SQL. Re-audit its setup, worker and loader if either module changes.
SUITE_HASH = "270a844a7b0b84045efde017135e6208ca07d7d93eba395be17c20c6e918923d"
LOADER_HASH = "c0dc285fccd1b8bee4a451f5962118fe771de005843b56e3af4c7136f74c3ed3"


def fixture_name(suite, fixture):
    """Resolve the file bound to the executor's list by its audited class setup."""
    cls, = (node for node in ast.parse(suite).body
            if isinstance(node, ast.ClassDef) and node.name == "TestExecutor")
    setup, = (node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "setUpClass")
    for node in setup.body:
        if isinstance(node, ast.Assign) and ast.unparse(node.targets[0]) == f"cls.{fixture}_sqls":
            call = node.value.args[0]
            if isinstance(call, ast.Call) and ast.unparse(call.func) == "load_sql_fixture_pairs":
                return ast.literal_eval(call.args[0])
    raise ValueError("executor fixture binding missing")


def executor_inputs(rows):
    # Preserve metadata insertion order as well as the order of the queries.
    return [(tuple(meta.items()), sql) for meta, sql, expected in rows]


def fixture_check(source, change, fixture):
    name = change["old"]
    suite = (source / "tests/test_executor.py").read_bytes()
    helpers = source / "tests/helpers.py"
    if (hashlib.sha256(suite).hexdigest() != SUITE_HASH
            or hashlib.sha256(helpers.read_bytes()).hexdigest() != LOADER_HASH):
        return "retain: unaudited fixture consumer"
    loader = runpy.run_path(str(helpers))
    path = Path(loader["FIXTURES_DIR"]) / fixture_name(suite, fixture)
    if name != change["new"] or name != path.relative_to(source).as_posix() or path.resolve() != path:
        return "retain: different executor fixture"
    before = path.read_text(encoding="utf-8")
    after, _, _ = apply_change(before, change)
    load = loader["load_sql_fixture_pairs"]
    expected = executor_inputs(load(str(path)))
    with tempfile.TemporaryDirectory() as folder:
        patched = Path(folder) / path.name
        patched.write_text(after, encoding="utf-8")
        actual = executor_inputs(load(str(patched)))
    if expected and expected == actual:
        return "candidate: ordered fixture metadata and input SQL matched"
    return "retain: executor fixture inputs differ"
