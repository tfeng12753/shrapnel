"""Audit dictionary value reads on literal tuples across Python aliases.

Only executed Python call sites in the production tree are instrumented. An
unobserved key is not proof of independence (native calls, reflection, uninstrumented copies
and child processes can also consume values). Run this CLI
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

    # Preserve deferred annotations, including the strings produced by
    # modules using "from __future__ import annotations".
    def visit_AnnAssign(self, node):
        if node.value is not None:
            node.value = self.visit(node.value)
        return node

    def visit_FunctionDef(self, node):
        node.decorator_list = [self.visit(expr) for expr in node.decorator_list]
        node.args.defaults = [self.visit(expr) for expr in node.args.defaults]
        node.args.kw_defaults = [self.visit(expr) if expr is not None else None for expr in node.args.kw_defaults]
        node.body = [self.visit(stmt) for stmt in node.body]
        return node

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_TypeAlias(self, node):
        return node

    def visit_Subscript(self, node):
        self.generic_visit(node)
        if isinstance(node.ctx, ast.Load) and not isinstance(node.slice, (ast.Slice, ast.Tuple, ast.Starred)):
            return ast.copy_location(ast.Call(ast.Name("_shrapnel_tuple_item", ast.Load()),
                                              [node.value, node.slice], []), node)
        return node

    def visit_Call(self, node):
        self.generic_visit(node)
        if (isinstance(node.func, ast.Attribute) and node.func.attr == "get" and not node.keywords
                and len(node.args) in (1, 2) and not any(isinstance(a, ast.Starred) for a in node.args)):
            return ast.copy_location(ast.Call(ast.Name("_shrapnel_tuple_get", ast.Load()),
                                              [node.func, *node.args], []), node)
        if (isinstance(node.func, ast.Attribute) and node.func.attr in ("items", "values", "copy")
                and not node.args and not node.keywords):
            return ast.copy_location(ast.Call(ast.Name("_shrapnel_tuple_bulk", ast.Load()),
                                              [node.func], []), node)
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

    def record(mapping, key=None):
        binding = bindings.get(id(mapping))
        if binding is None:
            return
        context = current if threading.current_thread() is threading.main_thread() else None
        keys = binding[2] if key is None or type(key) is not str else (key,)
        for candidate in keys:
            if candidate in binding[2]:
                reads[binding[1]][candidate].add(context or "<shared>")

    def get(method, key, *default):
        if type(method) is types.BuiltinMethodType and type(method.__self__) is dict and method.__name__ == "get":
            record(method.__self__, key)
        return method(key, *default)

    def item(mapping, key):
        if type(mapping) is dict:
            record(mapping, key)
        return mapping[key]

    def bulk(method):
        if (type(method) is types.BuiltinMethodType and type(method.__self__) is dict
                and method.__name__ in ("items", "values", "copy")):
            record(method.__self__)
        return method()

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
    builtins._shrapnel_tuple_item, builtins._shrapnel_tuple_bulk = item, bulk
    importlib.machinery.SourceFileLoader.get_code = code
    try:
        status = pytest.main(["tests", "-q", "-p", "no:cacheprovider", "--timeout=180"], plugins=[Plugin()])
    finally:
        importlib.machinery.SourceFileLoader.get_code = original
        del builtins._shrapnel_tuple_bind, builtins._shrapnel_tuple_get
        del builtins._shrapnel_tuple_item, builtins._shrapnel_tuple_bulk
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
