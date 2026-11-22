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
import unittest
import types
from collections import defaultdict

REVISION = "eb11aec4be8cb2122afe713a4816662c3f0fb2fa"


def module_name(path):
    return path.removesuffix(".py").replace("/", ".").removesuffix(".__init__")


class SourceModel:
    def __init__(self, root):
        self.root = root
        self.symbols, self.imports, self.stars = {}, defaultdict(dict), defaultdict(list)
        self.modules, self.trees, self.class_fields = {}, {}, {}
        # (module, class qualname) -> names a class-creation hook defined there reads
        # from the new class, or None when the hook is too dynamic to tell.
        self.creation_hooks = {}
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
                if kind == "class":
                    self.scan_creation_hooks(path, name, node)
                start = min([node.lineno] + [d.lineno for d in node.decorator_list])
                self.symbols[(path, name)] = {"start": start, "end": node.end_lineno, "node": node, "kind": kind}
                if kind == "class":
                    self.scan(path, node.body, name)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)) and not inside_function:
                if isinstance(node, ast.AnnAssign) and node.value is None:
                    continue
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

    def scan_creation_hooks(self, path, qualname, node):
        methods = {n.name: n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        key = (module_name(path), qualname)
        if "__init_subclass__" in methods:
            self.creation_hooks[key] = creation_reads(methods["__init_subclass__"])
        elif any(isinstance(b, ast.Name) and b.id == "type" for b in node.bases) and (
                "__new__" in methods or "__init__" in methods):
            # A metaclass: the new class is the result of type.__new__ (or the
            # first parameter after the metaclass in __init__).
            hooks = [creation_reads(methods[m], metaclass=True) for m in ("__new__", "__init__") if m in methods]
            self.creation_hooks[key] = None if None in hooks else frozenset().union(*hooks)

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

    def references(self, path, node, with_lines=False):
        for n in ast.walk(node):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
                target = self.resolve(path, n.id)
                if target in self.symbols:
                    yield (target, n.lineno) if with_lines else target
            elif isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Load):
                names, base = [n.attr], n.value
                while isinstance(base, ast.Attribute):
                    names.append(base.attr)
                    base = base.value
                if isinstance(base, ast.Name):
                    target = self.resolve(path, ".".join([base.id] + list(reversed(names))))
                    if target in self.symbols:
                        yield (target, n.lineno) if with_lines else target


ALL_NAMES = "*"


def creation_reads(function, metaclass=False):
    """Attribute names a class-creation hook reads from the new class, or None if it may read others.

    Every use of the class must be an attribute access, ``cls.__dict__.get("NAME", ...)``,
    a getattr/hasattr/setattr with a literal name, a returned value or a value stored
    into a container; passing the class anywhere else could read anything.
    """
    params = [a.arg for a in function.args.args]
    if metaclass and function.name == "__new__":
        # klass = super().__new__(...)
        targets = [n.targets[0].id for n in ast.walk(function)
                   if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)
                   and isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Attribute)
                   and n.value.func.attr == "__new__"]
        if len(targets) != 1:
            return None
        param = targets[0]
    elif metaclass:
        if len(params) < 2:
            return None
        param = params[1]
    elif params:
        param = params[0]
    else:
        return None
    names, allowed = set(), set()
    for node in ast.walk(function):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get"
                and isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "__dict__"
                and isinstance(node.func.value.value, ast.Name) and node.func.value.value.id == param
                and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
            names.add(node.args[0].value)
        if isinstance(node, (ast.Return, ast.Assign)) and isinstance(node.value, ast.Name) and node.value.id == param:
            if isinstance(node, ast.Return) or all(isinstance(t, ast.Subscript) for t in node.targets):
                allowed.add(id(node.value))
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == param for t in node.targets):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == param:
                    allowed.add(id(t))
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == param:
            allowed.add(id(node.value))
            if isinstance(node.ctx, ast.Load):
                names.add(node.attr)
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
              and node.func.id in ("getattr", "hasattr", "setattr") and len(node.args) >= 2
              and isinstance(node.args[0], ast.Name) and node.args[0].id == param):
            if not (isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str)):
                return None
            allowed.add(id(node.args[0]))
            if node.func.id != "setattr":
                names.add(node.args[1].value)
    for node in ast.walk(function):
        if isinstance(node, ast.Name) and node.id == param and id(node) not in allowed:
            return None
    return frozenset(names)


class Recorder:
    def __init__(self, root, model, coverage):
        self.root, self.model, self.coverage = root, model, coverage
        self.current, self.phase = None, "collection"
        self.attrs = defaultdict(set)
        # (module, class qualname) -> tests that read an attribute the class got at
        # creation time instead of from its body.
        self.derived_reads = defaultdict(set)
        self.reads = defaultdict(set)
        self.cache = {}
        self.tests = []
        self.items = {}
        self.busy = False
        self.external = set()
        self.native = set()
        self.comparisons = {}
        # Class-setup refinement: files opened in setUpClass with the setUpClass
        # frame's current line, that function's code location, and the attribute
        # names each test reads through its instance.
        self.setup_opens = defaultdict(lambda: defaultdict(set))
        self.setup_code = {}
        self.instance_reads = defaultdict(set)

    def audit(self, event, args):
        if event == "open":
            file, mode, flags = args
            if mode is None:
                if flags & (os.O_WRONLY | os.O_RDWR):
                    return
                mode = "r"
            self.opened(file, mode)
        if event in ("subprocess.Popen", "os.fork", "os.posix_spawn", "_posixsubprocess.fork_exec") and self.current:
            self.external.add(self.current)

    def record_object_attr(self, obj, name):
        if self.current is None or not isinstance(name, str):
            return
        if type(obj) is types.ModuleType:
            path = self.model.modules.get(obj.__name__)
            if path and (path, name) in self.model.symbols:
                self.attrs[(path, name)].add(self.current)
            return
        self.record_attr(obj if isinstance(obj, type) else type(obj), name)

    def record_attr(self, cls, name):
        if self.current is None or name not in self.model.attr_names:
            return
        key = (cls, name)
        if key not in self.cache:
            result = None
            mro = type.__getattribute__(cls, "__mro__")
            for base in mro:
                if name in type.__getattribute__(base, "__dict__"):
                    result = self.model.class_fields.get((
                        type.__getattribute__(base, "__module__"),
                        type.__getattribute__(base, "__qualname__"), name))
                    if result is None:
                        # Set at class creation (for example by __init_subclass__)
                        # rather than written in the class body. The read counts for
                        # the class-level assignments along the MRO that the creation
                        # hooks read, or all of them when that is unknown (see write_map).
                        result = ("derived", tuple(
                            (type.__getattribute__(k, "__module__"), type.__getattribute__(k, "__qualname__"))
                            for k in mro), self.creation_inputs(cls))
                    break
            self.cache[key] = result
        symbol = self.cache[key]
        if symbol and symbol[0] == "derived":
            # Only attributes a known creation hook sets are followed; other
            # runtime-set attributes (caches, later assignments) are not.
            if symbol[2] is not None:
                for owner in symbol[1]:
                    self.derived_reads[(*owner, symbol[2])].add(self.current)
        elif symbol:
            self.attrs[symbol].add(self.current)

    def creation_inputs(self, cls):
        """Names the creation hooks of ``cls`` read from it, or None if unknown."""
        hooks = getattr(self.model, "creation_hooks", {})
        names, found = set(), False
        mro = type.__getattribute__(cls, "__mro__")
        metaclasses = [m for m in type.__getattribute__(type(cls), "__mro__") if m not in (type, object)]
        for owner in [k for k in mro if "__init_subclass__" in type.__getattribute__(k, "__dict__")] + metaclasses:
            key = (type.__getattribute__(owner, "__module__"), type.__getattribute__(owner, "__qualname__"))
            if key not in hooks:
                continue
            found = True
            if hooks[key] is None:
                # Too dynamic to tell which names: every class-level assignment.
                return ALL_NAMES
            names |= hooks[key]
        return frozenset(names) if found else None

    def record_comparison(self, path, site, op, state):
        entry = self.comparisons.setdefault((path, site), {"op": op, "states": defaultdict(set), "unknown": set(), "collection": False})
        if self.current is None:
            entry["collection"] = True
        elif state is None:
            entry["unknown"].add(self.current)
        else:
            entry["states"][state].add(self.current)

    def opened(self, file, mode="r"):
        if self.busy or isinstance(file, int) or not isinstance(mode, str) or any(c in mode for c in "wax+"):
            return
        self.busy = True
        try:
            path = Path(os.fsdecode(file)).resolve()
            if not path.is_relative_to(self.root) or not path.is_file():
                return
            name = path.relative_to(self.root).as_posix()
            # Source loaded by the import machinery is not an explicit source
            # inspection dependency. Keeping it would erase value refinement.
            if path.suffix in (".py", ".pyc"):
                frame = sys._getframe(2)
                while frame:
                    filename, function = frame.f_code.co_filename, frame.f_code.co_name
                    if ("importlib" in filename and function in ("get_data", "get_code", "_compile_bytecode")) or (filename.endswith("/instrument.py") and function == "get_code"):
                        return
                    if filename.startswith(str(self.root)) and not filename.endswith("/instrument.py"):
                        break
                    if any(part in filename for part in ("inspect.py", "linecache.py", "tokenize.py")):
                        break
                    frame = frame.f_back
            if self.current and self.phase == "call":
                scope = ("test", self.current)
            elif self.current:
                scope = ("all", "")
                frame = sys._getframe(2)
                while frame:
                    function = frame.f_code.co_name
                    if function in ("setUpClass", "setup_class", "tearDownClass", "teardown_class"):
                        scope = ("class", self.current.rsplit("::", 1)[0])
                        if function in ("setUpClass", "setup_class"):
                            self.setup_opens[scope[1]][name].add(frame.f_lineno)
                            self.setup_code[scope[1]] = (frame.f_code.co_filename, frame.f_code.co_firstlineno, function)
                        break
                    if function in ("setUp", "tearDown", "asyncSetUp", "asyncTearDown"):
                        scope = ("test", self.current)
                        break
                    if function in ("setUpModule", "tearDownModule", "setup_module", "teardown_module"):
                        scope = ("file", self.current.split("::")[0])
                        break
                    frame = frame.f_back
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

class InlineProcessPool:
    """Stand-in for ProcessPoolExecutor during map generation.

    sqlglot's optimizer and executor tests hand work to spawned process pools.
    Coverage and attribute observers only see the parent process, so that work
    was attributed to no test, and targeted selection missed those tests. Running
    each submission inline in the calling test keeps every dependency observable.
    Futures complete before submit returns, so wait/as_completed behave normally.
    Arguments and results still round-trip through pickle, as with a real pool,
    so serialization code (Expr.__reduce__ -> sqlglot.serde) is attributed too.
    """

    def __init__(self, max_workers=None, mp_context=None, initializer=None, initargs=(), **kwargs):
        self._initializer, self._initargs, self._ready = initializer, tuple(initargs), False

    def submit(self, fn, /, *args, **kwargs):
        from concurrent.futures import Future
        import pickle
        future = Future()
        try:
            if not self._ready:
                self._ready = True
                if self._initializer is not None:
                    initializer, initargs = pickle.loads(pickle.dumps((self._initializer, self._initargs)))
                    initializer(*initargs)
            fn, args, kwargs = pickle.loads(pickle.dumps((fn, args, kwargs)))
            future.set_result(pickle.loads(pickle.dumps(fn(*args, **kwargs))))
        except BaseException as error:
            future.set_exception(error)
        return future

    def map(self, fn, *iterables, timeout=None, chunksize=1):
        return [self.submit(fn, *args).result() for args in zip(*iterables)]

    def shutdown(self, wait=True, *, cancel_futures=False):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.shutdown()
        return False


def observe_instance_reads(item, rec):
    """Record attribute names a unittest test reads through self; return an undo function."""
    cls = getattr(item, "cls", None)
    if cls is None or not issubclass(cls, unittest.TestCase):
        return lambda: None
    own = cls.__dict__.get("__getattribute__")
    inherited = cls.__getattribute__
    reads = rec.instance_reads[item.nodeid]

    def observed(self, name):
        reads.add(name)
        return inherited(self, name)

    cls.__getattribute__ = observed

    def restore():
        if own is None:
            del cls.__getattribute__
        else:
            cls.__getattribute__ = own
    return restore


def refine_class_readers(rec, class_id):
    """{file: set(test ids)} for class-setup reads this class can attribute per test, else None."""
    import ast
    from setup_flow import class_level_access, find_function, setup_flow
    if class_id not in rec.setup_code:
        return None
    filename, firstlineno, function = rec.setup_code[class_id]
    try:
        tree = ast.parse(Path(filename).read_text(encoding="utf-8"))
        func = find_function(tree, firstlineno, function)
        module_path, class_name = class_id.split("::")[0], class_id.split("::")[-1]
        module_tree = ast.parse((rec.root / module_path).read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return None
    if func is None or class_level_access(module_tree, class_name) or class_level_access(tree, class_name):
        return None
    flow, problems = setup_flow(func, rec.setup_opens[class_id])
    if problems:
        return None
    members = [t for t in rec.tests if t.startswith(class_id + "::")]
    return {name: {t for t in members if rec.instance_reads.get(t, set()) & set(attrs)}
            for name, attrs in flow.items()}


def build(root, destination):
    import coverage
    import pytest
    from instrument import Instrumentation
    root = root.resolve()
    os.chdir(root)
    sys.path.insert(0, str(root))
    os.environ["SKIP_INTEGRATION"] = "0"
    model = SourceModel(root)
    cov = coverage.Coverage(data_file=None, source=[str(root / "sqlglot"), str(root / "tests")], config_file=False)
    rec = Recorder(root, model, cov)
    class Plugin:
        def pytest_collection_modifyitems(self, items):
            rec.tests = sorted(item.nodeid for item in items)
            rec.items = {item.nodeid: item for item in items}
            for item in items:
                if not item.cls or not issubclass(item.cls, unittest.TestCase):
                    continue
                if getattr(item.cls, "pytestmark", ()) or getattr(item.obj, "pytestmark", ()):
                    continue
                definitions = getattr(getattr(item, "_fixtureinfo", None), "name2fixturedefs", {})
                if any(not definition.func.__module__.startswith("_pytest")
                       for group in definitions.values() for definition in (group or ())):
                    continue
                rec.native.add(item.nodeid)

        @pytest.hookimpl(hookwrapper=True)
        def pytest_runtest_protocol(self, item, nextitem):
            rec.current, rec.phase = item.nodeid, "setup"
            cov.switch_context(item.nodeid)
            restore = observe_instance_reads(item, rec)
            try:
                yield
            finally:
                restore()
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
    # Patch before collection: test modules bind ProcessPoolExecutor at import.
    import concurrent.futures
    import concurrent.futures.process
    concurrent.futures.ProcessPoolExecutor = InlineProcessPool
    concurrent.futures.process.ProcessPoolExecutor = InlineProcessPool
    instrumentation = Instrumentation(root, rec)
    instrumentation.install()
    cov.start()
    try:
        status = pytest.main(["tests", "-q", "-p", "no:cacheprovider", f"--timeout={int(os.environ.get('MAP_TIMEOUT', '180'))}"], plugins=[Plugin()])
    finally:
        cov.stop()
        instrumentation.restore()
    if status != 0:
        raise RuntimeError(f"pinned suite did not pass during map generation (pytest exit {status}); refusing incomplete map")
    if len(rec.tests) < 1000:
        raise RuntimeError("unexpectedly small test collection")
    write_map(root, model, rec, cov.get_data(), destination)


def lambda_spans(tree):
    """(first, last) line of every lambda that is not inside a function or another lambda."""
    spans = []

    def walk(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if isinstance(child, ast.Lambda):
                spans.append((child.lineno, child.end_lineno))
                continue
            walk(child)

    walk(tree)
    return spans


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
    refined = {}
    for name, scopes in rec.reads.items():
        for kind, value in scopes:
            if kind == "all":
                files[name]["readers"] |= all_tests
            elif kind == "test":
                files[name]["readers"] |= bit.get(value, 0)
            elif kind == "file":
                files[name]["readers"] |= mask(t for t in rec.tests if t.split("::")[0] == value)
            elif kind == "class":
                if value not in refined:
                    refined[value] = refine_class_readers(rec, value) if hasattr(rec, "setup_code") else None
                by_file = refined[value]
                if by_file is not None and name in by_file:
                    # Only tests reading an attribute this file can reach. The
                    # selector must verify setUpClass still succeeds before relying on it.
                    files[name]["readers"] |= mask(by_file[name])
                    setup = files[name].setdefault("setup_classes", {})
                    setup[value] = format(mask(t for t in rec.tests if t.startswith(value + "::")), "x")
                else:
                    files[name]["readers"] |= mask(t for t in rec.tests if t.startswith(value + "::"))
    # Raw executed lines inside lambdas that are not nested in a function, before
    # class-level value spans are widened below. A lambda body runs only when the
    # lambda is called, so these are the tests that called it.
    for name, tree in model.trees.items():
        lines = files[name]["lines"] if name in files else {}
        raw = {}
        for start, end in lambda_spans(tree):
            for line in range(start, end + 1):
                if lines.get(line):
                    raw[line] = lines[line]
        if raw:
            files[name]["lambda_lines"] = {str(line): format(value, "x") for line, value in sorted(raw.items())}
    users = {}
    for key, symbol in model.symbols.items():
        entry = files[key[0]]
        value = mask(rec.attrs.get(key, ()))
        for line, line_mask in entry["lines"].items():
            if symbol["start"] <= line <= symbol["end"]:
                value |= line_mask
        users[key] = value
    # Attributes a class gets at creation are computed from its class-level
    # assignments, so a test reading one depends on all of them.
    class_values = defaultdict(list)
    for key, symbol in model.symbols.items():
        if symbol["kind"] == "value" and "." in key[1]:
            class_values[(key[0], key[1].rpartition(".")[0])].append(key)
    for (module, qualname, names), tests in getattr(rec, "derived_reads", {}).items():
        path = model.modules.get(module)
        value = mask(tests)
        for key in class_values.get((path, qualname), ()):
            if names == ALL_NAMES or key[1].rpartition(".")[2] in names:
                users[key] |= value
    # Runtime references use the exact executed line. Only initializer copies
    # and class inheritance need transitive source-symbol propagation.
    edges = []
    for key, symbol in model.symbols.items():
        node = symbol["node"]
        if symbol["kind"] == "class":
            nodes = [*node.bases, *node.decorator_list, *(k.value for k in node.keywords)]
        else:
            nodes = [node]
        for node in nodes:
            for target, line in set(model.references(key[0], node, with_lines=True)):
                if target == key:
                    continue
                if symbol["kind"] == "function":
                    users[target] |= files[key[0]]["lines"].get(line, 0)
                else:
                    edges.append((key, target))
        if "." in key[1]:
            parent = (key[0], key[1].rpartition(".")[0])
            if parent in users:
                edges.append((key, parent))
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
    # Operators and punctuation may sit on continuation lines without a trace
    # event of their own. Attach the smallest containing expression's users.
    for name, tree in model.trees.items():
        lines = files[name]["lines"]
        anchors = {}
        expressions = [n for n in ast.walk(tree) if isinstance(n, ast.expr) and getattr(n, "end_lineno", n.lineno) > n.lineno]
        expressions.sort(key=lambda n: n.end_lineno - n.lineno)
        for node in expressions:
            holes = [line for line in range(node.lineno, node.end_lineno + 1) if line not in lines and line not in anchors]
            if not holes:
                continue
            value = 0
            for line in range(node.lineno, node.end_lineno + 1):
                value |= lines.get(line, 0)
            if value:
                for line in holes:
                    anchors[line] = value
        files[name]["anchors"] = {str(line): format(value, "x") for line, value in anchors.items()}
    for (name, site), observation in rec.comparisons.items():
        if observation["collection"]:
            continue
        line = int(site.split(":")[0])
        states = {state: mask(contexts) for state, contexts in observation["states"].items()}
        observed = mask(observation["unknown"])
        for value in states.values():
            observed |= value
        unknown = mask(observation["unknown"]) | (files[name]["lines"].get(line, 0) & ~observed)
        files[name].setdefault("comparisons", {})[site] = {
            "op": observation["op"], "states": {state: format(value, "x") for state, value in states.items()},
            "unknown": format(unknown, "x")}
    for name, entry in files.items():
        entry["lines"] = {str(line): format(value, "x") for line, value in sorted(entry["lines"].items()) if value}
        entry["regions"] = [[a, b, k, format(v, "x")] for a, b, k, v in entry["regions"]]
        entry["readers"], entry["executed"] = format(entry["readers"], "x"), format(entry["executed"], "x")
    result = {"version": 1, "revision": REVISION, "complete": True, "tests": rec.tests, "external": format(external_mask, "x"), "native": sorted(rec.native), "files": dict(files)}
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
