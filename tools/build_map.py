#!/usr/bin/env python3
"""Build a dependency map exclusively from the unmodified, pinned checkout.

Run at image build time. Test outcomes never become selection rules. Coverage
contexts, observed class-attribute reads, fixture reads, and static symbol
references are the only inputs to the map.
"""
from __future__ import annotations

import argparse
import ast
import gzip
import importlib
import json
import os
from pathlib import Path
import sys
from collections import defaultdict

REVISION = "eb11aec4be8cb2122afe713a4816662c3f0fb2fa"


def module_name(path):
    return path.removesuffix(".py").replace("/", ".").removesuffix(".__init__")


class SourceModel:
    def __init__(self, root):
        self.root = root
        self.symbols, self.imports, self.stars = {}, defaultdict(dict), defaultdict(list)
        self.modules, self.trees, self.class_fields = {}, {}, {}
        for folder in (root / "sqlglot", root / "tests"):
            for path in sorted(folder.rglob("*.py")):
                name = path.relative_to(root).as_posix()
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=name)
                self.trees[name], self.modules[module_name(name)] = tree, name
                self.scan(name, tree.body)
        self.attr_names = {field for _, _, field in self.class_fields}

    def scan(self, path, body, scope="", inside_function=False):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{scope}.{node.name}".strip(".")
                kind = "class" if isinstance(node, ast.ClassDef) else "function"
                start = min([node.lineno] + [d.lineno for d in node.decorator_list])
                self.symbols[(path, name)] = {"start": start, "end": node.end_lineno, "node": node, "kind": kind}
                if kind == "class":
                    self.scan(path, node.body, name)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)) and not inside_function:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    for n in ast.walk(target):
                        if isinstance(n, ast.Name):
                            name = f"{scope}.{n.id}".strip(".")
                            self.symbols[(path, name)] = {"start": node.lineno, "end": node.end_lineno, "node": node, "kind": "value"}
                            if scope:
                                self.class_fields[(module_name(path), scope, n.id)] = (path, name)
            elif isinstance(node, ast.ImportFrom):
                package = module_name(path) if path.endswith("/__init__.py") else module_name(path).rpartition(".")[0]
                try:
                    origin = importlib.util.resolve_name("." * node.level + (node.module or ""), package) if node.level else node.module
                except (ValueError, ImportError):
                    continue
                for alias in node.names:
                    if alias.name == "*":
                        self.stars[path].append(origin)
                    else:
                        self.imports[path][alias.asname or alias.name] = (origin, alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    self.imports[path][alias.asname or alias.name.split(".")[0]] = (alias.name if alias.asname else alias.name.split(".")[0], "")
            elif isinstance(node, (ast.If, ast.Try, ast.With)):
                for field in ("body", "orelse", "finalbody"):
                    self.scan(path, getattr(node, field, []), scope)
                for handler in getattr(node, "handlers", []):
                    self.scan(path, handler.body, scope)

    def resolve(self, path, name, seen=None):
        seen = set() if seen is None else seen
        if (path, name) in seen:
            return None
        seen.add((path, name))
        if (path, name) in self.symbols:
            return path, name
        head, _, tail = name.partition(".")
        if head in self.imports[path]:
            mod, imported = self.imports[path][head]
            full = ".".join(x for x in (mod, imported) if x)
            if full in self.modules:
                if not tail:
                    return self.modules[full], ""
                return self.resolve(self.modules[full], tail, seen)
            if mod in self.modules:
                return self.resolve(self.modules[mod], ".".join(x for x in (imported, tail) if x), seen)
        for mod in self.stars[path]:
            if mod in self.modules:
                found = self.resolve(self.modules[mod], name, seen)
                if found:
                    return found
        return None

    def references(self, path, node):
        for n in ast.walk(node):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
                target = self.resolve(path, n.id)
                if target in self.symbols:
                    yield target
            elif isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Load):
                names, base = [n.attr], n.value
                while isinstance(base, ast.Attribute):
                    names.append(base.attr)
                    base = base.value
                if isinstance(base, ast.Name):
                    target = self.resolve(path, ".".join([base.id] + list(reversed(names))))
                    if target in self.symbols:
                        yield target


class Recorder:
    def __init__(self, root, model, coverage):
        self.root, self.model, self.coverage = root, model, coverage
        self.current, self.phase = None, "collection"
        self.attrs = defaultdict(set)
        self.reads = defaultdict(set)
        self.cache, self.originals = {}, []
        self.tests = []
        self.items = {}
        self.busy = False
        self.external = set()

    def audit(self, event, args):
        if event == "open":
            file, mode, flags = args
            if mode is None:
                if flags & (os.O_WRONLY | os.O_RDWR):
                    return
                mode = "r"
            self.opened(file, mode)
        if event in ("subprocess.Popen", "os.fork", "os.posix_spawn") and self.current:
            self.external.add(self.current)

    def record_attr(self, cls, name):
        if self.current is None or name not in self.model.attr_names:
            return
        key = (cls, name)
        if key not in self.cache:
            result = None
            for base in type.__getattribute__(cls, "__mro__"):
                if name in type.__getattribute__(base, "__dict__"):
                    result = self.model.class_fields.get((
                        type.__getattribute__(base, "__module__"),
                        type.__getattribute__(base, "__qualname__"), name))
                    break
            self.cache[key] = result
        symbol = self.cache[key]
        if symbol:
            self.attrs[symbol].add(self.current)

    def install_attributes(self):
        roots = [
            ("sqlglot.expressions", "Expr"),
            ("sqlglot.expressions", "Expression"),
            ("sqlglot.dialects.dialect", "Dialect"),
            ("sqlglot.parser", "Parser"),
            ("sqlglot.generator", "Generator"),
            ("sqlglot.tokens", "Tokenizer"),
            ("sqlglot.optimizer.annotate_types", "TypeAnnotator"),
            ("sqlglot.optimizer.scope", "Scope"),
            ("sqlglot.schema", "Schema"),
        ]
        wrapped = set()
        def wrap(cls, is_meta=False):
            if cls in wrapped or cls is type:
                return
            wrapped.add(cls)
            original = cls.__getattribute__
            recorder = self
            def access(obj, name):
                value = original(obj, name)
                recorder.record_attr(obj if is_meta else type(obj), name)
                return value
            try:
                cls.__getattribute__ = access
                self.originals.append((cls, "__getattribute__", original))
            except (TypeError, AttributeError):
                pass
        for module, name in roots:
            obj = getattr(importlib.import_module(module), name, None)
            if obj is not None and obj not in wrapped:
                wrap(obj)
                meta = type(obj)
                if meta.__module__.startswith("sqlglot"):
                    wrap(meta, True)

    def opened(self, file, mode="r"):
        if self.busy or isinstance(file, int) or not isinstance(mode, str) or any(c in mode for c in "wax+"):
            return
        self.busy = True
        try:
            path = Path(os.fsdecode(file)).resolve()
            if not path.is_relative_to(self.root) or not path.is_file():
                return
            name = path.relative_to(self.root).as_posix()
            if self.current and self.phase == "call":
                scope = ("test", self.current)
            elif self.current:
                # Session/class fixtures can feed tests other than the first user.
                scope = ("all", "")
            else:
                scope = None
                frame = sys._getframe(2)
                while frame:
                    source = Path(frame.f_code.co_filename)
                    if source.name.startswith("test_") and source.is_relative_to(self.root):
                        scope = ("file", source.relative_to(self.root).as_posix())
                        break
                    frame = frame.f_back
                if scope is None:
                    return
            self.reads[name].add(scope)
        except (OSError, TypeError, ValueError):
            pass
        finally:
            self.busy = False

    def restore(self):
        for obj, name, original in reversed(self.originals):
            setattr(obj, name, original)


def build(root, destination):
    import coverage
    import pytest
    root = root.resolve()
    os.chdir(root)
    sys.path.insert(0, str(root))
    os.environ["SKIP_INTEGRATION"] = "0"
    model = SourceModel(root)
    cov = coverage.Coverage(data_file=None, source=[str(root / "sqlglot"), str(root / "tests")], config_file=False)
    rec = Recorder(root, model, cov)
    class Plugin:
        def pytest_sessionstart(self, session):
            rec.install_attributes()

        def pytest_collection_modifyitems(self, items):
            rec.tests = sorted(item.nodeid for item in items)
            rec.items = {item.nodeid: item for item in items}

        @pytest.hookimpl(hookwrapper=True)
        def pytest_runtest_protocol(self, item, nextitem):
            rec.current, rec.phase = item.nodeid, "setup"
            cov.switch_context(item.nodeid)
            try:
                yield
            finally:
                cov.switch_context("")
                rec.current, rec.phase = None, "collection"

        @pytest.hookimpl(hookwrapper=True)
        def pytest_runtest_call(self, item):
            rec.phase = "call"
            try:
                yield
            finally:
                rec.phase = "teardown"

    sys.addaudithook(rec.audit)
    cov.start()
    try:
        status = pytest.main(["tests", "-q", "-p", "no:cacheprovider", "--timeout=180"], plugins=[Plugin()])
    finally:
        rec.restore()
        cov.stop()
    if status != 0:
        raise RuntimeError(f"pinned suite did not pass during map generation (pytest exit {status}); refusing incomplete map")
    if len(rec.tests) < 1000:
        raise RuntimeError("unexpectedly small test collection")
    write_map(root, model, rec, cov.get_data(), destination)


def write_map(root, model, rec, data, destination):
    """Combine observations with source dependencies; independently testable."""
    bit = {name: 1 << i for i, name in enumerate(rec.tests)}
    def mask(contexts):
        result = 0
        for context in contexts:
            result |= bit.get(context, 0)
        return result
    all_tests = (1 << len(rec.tests)) - 1
    files = defaultdict(lambda: {"lines": {}, "regions": [], "readers": 0, "executed": 0})
    for filename in data.measured_files():
        path = Path(filename)
        if not path.is_relative_to(root):
            continue
        name = path.relative_to(root).as_posix()
        for line, contexts in data.contexts_by_lineno(filename).items():
            value = mask(contexts)
            if value:
                files[name]["lines"][line] = value
                files[name]["executed"] |= value
    external_mask = mask(rec.external)
    for name, scopes in rec.reads.items():
        for kind, value in scopes:
            if kind == "all":
                files[name]["readers"] |= all_tests
            elif kind == "test":
                files[name]["readers"] |= bit.get(value, 0)
            elif kind == "file":
                files[name]["readers"] |= mask(t for t in rec.tests if t.split("::")[0] == value)
    users = {}
    for key, symbol in model.symbols.items():
        entry = files[key[0]]
        value = mask(rec.attrs.get(key, ()))
        for line, line_mask in entry["lines"].items():
            if symbol["start"] <= line <= symbol["end"]:
                value |= line_mask
        users[key] = value
    # Propagate runtime users backwards through ordinary source dependencies.
    # This includes constants imported under aliases and class-table construction.
    edges = []
    for key, symbol in model.symbols.items():
        node = symbol["node"]
        if symbol["kind"] == "class":
            nodes = [*node.bases, *node.decorator_list, *(k.value for k in node.keywords)]
        else:
            nodes = [node]
        for node in nodes:
            for target in set(model.references(key[0], node)):
                if target != key:
                    edges.append((key, target))
    for _ in range(len(users) + 1):
        changed = False
        for source, target in edges:
            value = users[target] | users[source]
            if value != users[target]:
                users[target] = value
                changed = True
        if not changed:
            break
    for (name, qualname), symbol in model.symbols.items():
        entry = files[name]
        value = users[(name, qualname)]
        kind = symbol["kind"]
        entry["regions"].append([symbol["start"], symbol["end"], kind, value])
        if kind == "class":
            header_end = max(symbol["start"], symbol["node"].body[0].lineno - 1)
            entry["regions"].append([symbol["start"], header_end, "header", value])
        elif kind == "function":
            node = symbol["node"]
            header_end = max(symbol["start"], node.body[0].lineno - 1)
            entry["regions"].append([symbol["start"], header_end, "header", value])
        if kind == "value":
            for line in range(symbol["start"], symbol["end"] + 1):
                entry["lines"][line] = entry["lines"].get(line, 0) | value
        entry["executed"] |= value
    for name, entry in files.items():
        entry["lines"] = {str(line): format(value, "x") for line, value in sorted(entry["lines"].items()) if value}
        entry["regions"] = [[a, b, k, format(v, "x")] for a, b, k, v in entry["regions"]]
        entry["readers"], entry["executed"] = format(entry["readers"], "x"), format(entry["executed"], "x")
    result = {"version": 1, "revision": REVISION, "complete": True, "tests": rec.tests, "external": format(external_mask, "x"), "files": dict(files)}
    opener = gzip.open if destination.suffix == ".gz" else open
    with opener(destination, "wt", encoding="utf-8") as f:
        json.dump(result, f, separators=(",", ":"))
    destination.chmod(0o644)
    print(f"wrote {destination}: {len(rec.tests)} tests, {len(files)} files, {len(edges)} symbol edges", file=sys.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("/opt/sqlglot"))
    parser.add_argument("--output", type=Path, default=Path("/app/dependencies.json.gz"))
    args = parser.parse_args()
    build(args.source, args.output)
