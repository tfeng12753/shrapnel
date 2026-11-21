"""Audit positive dict.get observations on literal tuples across Python aliases.

Only executed Python call sites in the production tree are instrumented. An
unobserved key is not proof of independence (native calls, direct subscripts,
reflection, copies and child processes can also consume values). Run this CLI
in its own interpreter; it installs a loader for the duration of the test run.
"""
import argparse
import ast
import builtins
from collections import defaultdict
import importlib.machinery
import json
from pathlib import Path
import sys
import threading


def declarations(tree):
    found = {}
    def scan(body, prefix):
        for node in body:
            if isinstance(node, ast.ClassDef):
                scan(node.body, prefix + node.name + ".")
            if not isinstance(node, (ast.Assign, ast.AnnAssign)) or not isinstance(node.value, ast.Dict):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            literal = node.value
            if (len(targets) != 1 or not isinstance(targets[0], ast.Name) or not literal.keys
                    or not all(isinstance(k, ast.Constant) and type(k.value) is str for k in literal.keys)
                    or not all(isinstance(v, ast.Tuple) and all(isinstance(e, ast.Constant) for e in v.elts)
                               for v in literal.values)):
                continue
            found[literal.lineno, literal.col_offset] = prefix + targets[0].id
    scan(tree.body, "")
    return found


class Trace(ast.NodeTransformer):
    def __init__(self, bindings):
        self.bindings = bindings

    def visit_Dict(self, node):
        symbol = self.bindings.get((node.lineno, node.col_offset))
        self.generic_visit(node)
        if symbol:
            return ast.copy_location(ast.Call(ast.Name("_shrapnel_tuple_bind", ast.Load()),
                                              [node, ast.Constant(symbol)], []), node)
        return node

    def visit_Call(self, node):
        self.generic_visit(node)
        if (isinstance(node.func, ast.Attribute) and node.func.attr == "get" and not node.keywords
                and len(node.args) in (1, 2) and not any(isinstance(a, ast.Starred) for a in node.args)):
            return ast.copy_location(ast.Call(ast.Name("_shrapnel_tuple_get", ast.Load()),
                                              [node.func, *node.args], []), node)
        return node


def observe(source, output):
    import pytest
    import types

    bindings, reads = {}, defaultdict(lambda: defaultdict(set))
    current = None
    original = importlib.machinery.SourceFileLoader.get_code

    def bind(mapping, symbol):
        bindings[id(mapping)] = mapping, symbol, frozenset(mapping)
        return mapping

    def get(method, key, *default):
        if type(method) is types.BuiltinMethodType and type(method.__self__) is dict and method.__name__ == "get":
            binding = bindings.get(id(method.__self__))
            if binding and type(key) is str and key in binding[2]:
                context = current if threading.current_thread() is threading.main_thread() else None
                reads[binding[1]][key].add(context or "<shared>")
        return method(key, *default)

    def code(loader, fullname):
        path = Path(loader.get_filename(fullname)).resolve()
        if path.suffix != ".py" or not path.is_relative_to(source / "sqlglot"):
            return original(loader, fullname)
        tree = ast.parse(path.read_bytes(), filename=str(path))
        names = {loc: f"{path.relative_to(source)}::{name}" for loc, name in declarations(tree).items()}
        return compile(ast.fix_missing_locations(Trace(names).visit(tree)), str(path), "exec", dont_inherit=True)

    class Plugin:
        @pytest.hookimpl(hookwrapper=True)
        def pytest_runtest_call(self, item):
            nonlocal current
            current = item.nodeid
            try:
                yield
            finally:
                current = None

    import os
    os.chdir(source)
    sys.path.insert(0, str(source))
    builtins._shrapnel_tuple_bind, builtins._shrapnel_tuple_get = bind, get
    importlib.machinery.SourceFileLoader.get_code = code
    try:
        status = pytest.main(["tests", "-q", "-p", "no:cacheprovider", "--timeout=180"], plugins=[Plugin()])
    finally:
        importlib.machinery.SourceFileLoader.get_code = original
        del builtins._shrapnel_tuple_bind, builtins._shrapnel_tuple_get
    if status:
        raise RuntimeError(f"baseline suite exited with {status}")
    output.write_text(json.dumps({s: {k: sorted(v) for k, v in d.items()} for s, d in reads.items()},
                                 sort_keys=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    observe(args.source.resolve(), args.output.resolve())
