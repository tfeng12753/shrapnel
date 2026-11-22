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
import inspect
import importlib
import json
import os
from pathlib import Path
import sys
import time
import threading
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


def alias_names(model, names):
    """Parameter names of instrumented functions, and every name a tracked mapping can reach.

    A mapping moves to a new name only through a single-target assignment or a
    keyword argument matching a parameter of an instrumented function. Every
    load of a resulting name is instrumented and checked by identity, so reads
    through it are observed; any other use counts as a read of every key.
    """
    parameters = defaultdict(set)
    trees = getattr(model, "trees", {})
    def accepts(function):
        arguments = function.args
        return {argument.arg for argument in (*arguments.args, *arguments.kwonlyargs)}
    for name, tree in trees.items():
        if not name.startswith("sqlglot/"):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                parameters[node.name] |= accepts(node)
            elif isinstance(node, ast.ClassDef):
                for member in node.body:
                    if isinstance(member, ast.FunctionDef) and member.name == "__init__":
                        parameters[node.name] |= accepts(member)
    parameters = {callee: frozenset(names) for callee, names in parameters.items()}
    def identifier(node):
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return None
    flows = []
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = identifier(node.targets[0])
                if target and isinstance(node.targets[0], (ast.Name, ast.Attribute)):
                    flows.append((identifier(node.value), target))
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                flows.append((identifier(node.value), identifier(node.target)))
            elif isinstance(node, ast.Call):
                for keyword in node.keywords:
                    if keyword.arg in parameters.get(identifier(node.func), ()):
                        flows.append((identifier(keyword.value), keyword.arg))
    names = set(names)
    changed = True
    while changed:
        changed = False
        for source, target in flows:
            if source in names and target and target not in names:
                names.add(target)
                changed = True
    return parameters, names


class Recorder:
    def __init__(self, root, model, coverage):
        self.root, self.model, self.coverage = root, model, coverage
        self.current, self.phase = None, "collection"
        self.attrs = defaultdict(set)
        self.shared_attrs = defaultdict(set)
        self.dictionaries = {}
        for symbol, info in getattr(model, "symbols", {}).items():
            node = info["node"]
            if info["kind"] != "value" or not isinstance(node.value, ast.Dict):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            field = symbol[1].rsplit(".", 1)[-1]
            if any(isinstance(target, ast.Name) and target.id == field for target in targets):
                self.dictionaries[symbol] = info
        self.dictionary_names = {symbol.rsplit(".", 1)[-1] for _, symbol in self.dictionaries}
        for path, aliases in getattr(model, "imports", {}).items():
            for local_name in aliases:
                if model.resolve(path, local_name) in self.dictionaries:
                    self.dictionary_names.add(local_name)
        self.literal_keys = {
            symbol: {k.value for k in info["node"].value.keys
                     if isinstance(k, ast.Constant) and type(k.value) is str}
            for symbol, info in self.dictionaries.items()
        }
        self.bound_dictionaries = {}
        self.dict_declarations = defaultdict(dict)
        for (path, symbol), info in self.dictionaries.items():
            literal = info["node"].value
            location = literal.lineno, literal.col_offset
            previous = self.dict_declarations[path].get(location, ())
            self.dict_declarations[path][location] = (*previous, (path, symbol))
        self.dictionary_reads = defaultdict(lambda: defaultdict(set))
        # Membership reads depend only on the key set; transfers move a
        # tracked mapping to another alias name without reading it.
        self.dictionary_presence = defaultdict(lambda: defaultdict(set))
        self.dictionary_transfers = defaultdict(set)
        self.importing = 0
        self.parameter_names, self.dictionary_names = alias_names(model, self.dictionary_names)
        self.reads = defaultdict(set)
        self.cache = {}
        self.tests = []
        self.items = {}
        self.audit_state = threading.local()
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
                if flags & os.O_ACCMODE == os.O_WRONLY:
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
                self.attribute_user((path, name))
            return
        self.record_attr(obj if isinstance(obj, type) else type(obj), name)

    def read_context(self):
        """The test (or setup scope) a read belongs to; None means every test."""
        if (self.current is None or self.importing > 0
                or threading.current_thread() is not threading.main_thread()):
            return None
        frame = sys._getframe(2)
        depth = 0
        while frame is not None and depth < 6:
            code = frame.f_code
            # Class creation hooks derive values that outlive the current test.
            if code.co_name == "__init_subclass__" or (code.co_name == "__new__" and "bases" in code.co_varnames):
                return None
            frame, depth = frame.f_back, depth + 1
        if self.phase == "call":
            return self.current
        if self.phase not in ("setup", "teardown"):
            return None
        frame = sys._getframe(2)
        while frame is not None:
            function = frame.f_code.co_name
            if function in ("setUpClass", "tearDownClass", "setup_class", "teardown_class"):
                return ("class", self.current.rsplit("::", 1)[0])
            if function in ("setUpModule", "tearDownModule", "setup_module", "teardown_module"):
                return ("file", self.current.split("::")[0])
            if function in ("setUp", "tearDown", "setup_method", "teardown_method"):
                return self.current
            frame = frame.f_back
        # Fixtures with a wider scope run once for many tests.
        return None

    def record_dictionary(self, mapping, key, whole=None):
        binding = self.bound_dictionaries.get(id(mapping))
        if binding is None:
            return
        self.credit_writers(mapping, key, key is None if whole is None else whole)
        key = key if type(key) is str else None
        context = self.read_context()
        for symbol in binding[1]:
            # Avoid storing arbitrary SQL, runtime inputs and unseen keys.
            recorded = key if key in self.literal_keys[symbol] else None
            self.dictionary_reads[symbol][recorded].add(context)

    def record_presence(self, mapping, key):
        binding = self.bound_dictionaries.get(id(mapping))
        if binding is None:
            return
        self.credit_writers(mapping, key, False)
        key = key if type(key) is str else None
        context = self.read_context()
        for symbol in binding[1]:
            recorded = key if key in self.literal_keys[symbol] else None
            self.dictionary_presence[symbol][recorded].add(context)

    def record_store(self, mapping):
        """Before a test writes a tracked mapping, snapshot it and note the writing functions."""
        binding = self.bound_dictionaries.get(id(mapping))
        if binding is None or self.current is None or self.importing > 0:
            return
        pending = self.__dict__.setdefault("pending_writes", {})
        if id(mapping) not in pending:
            pending[id(mapping)] = (mapping, {key: id(value) for key, value in mapping.items()}, set())
        spans = pending[id(mapping)][2]
        # Only functions in the mapping's own module: callers elsewhere run
        # again in each later test and are covered by those tests themselves.
        owners = {(self.root / symbol[0]).resolve() for symbol in binding[1]}
        frame = sys._getframe(2)
        while frame is not None:
            code = frame.f_code
            path = Path(code.co_filename).resolve()
            if path in owners and code.co_flags & inspect.CO_NEWLOCALS and code.co_name != "<module>":
                lines = [line for _, _, line in code.co_lines() if line is not None]
                spans.add((path.relative_to(self.root.resolve()).as_posix(),
                           min(lines, default=code.co_firstlineno), max(lines, default=code.co_firstlineno)))
            elif path.is_relative_to(self.root / "tests"):
                break
            frame = frame.f_back

    def end_test(self):
        """Keys whose values a test changed now depend on the functions that wrote them."""
        writers = self.__dict__.setdefault("key_writers", {})
        for mapping, snapshot, spans in self.__dict__.pop("pending_writes", {}).values():
            if not spans:
                continue
            changed = [key for key, value in mapping.items() if snapshot.get(key, None) != id(value)]
            if changed:
                keys = writers.setdefault(id(mapping), (mapping, {}))[1]
                for key in changed:
                    keys[key] = frozenset(spans)

    def credit_writers(self, mapping, key, whole):
        """A later test reading a written key depends on the code that wrote it."""
        writers = getattr(self, "key_writers", {}).get(id(mapping))
        if writers is None or writers[0] is not mapping or self.current is None:
            return
        credit = self.__dict__.setdefault("writer_credit", defaultdict(set))
        try:
            groups = writers[1].values() if whole else [writers[1].get(key, ())]
        except TypeError:
            groups = writers[1].values()
        for spans in groups:
            for span in spans:
                credit[span].add(self.current)

    def record_transfer(self, mapping):
        binding = self.bound_dictionaries.get(id(mapping))
        if binding is None or self.current is None:
            return
        for symbol in binding[1]:
            self.dictionary_transfers[symbol].add(self.current)

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
            self.attribute_user(symbol)

    def attribute_user(self, symbol):
        self.attrs[symbol].add(self.current)
        if self.phase not in ("setup", "teardown"):
            return
        frame = sys._getframe(1)
        while frame:
            function = frame.f_code.co_name
            if function in ("setUpClass", "tearDownClass", "setup_class", "teardown_class"):
                self.shared_attrs[symbol].add(("class", self.current.rsplit("::", 1)[0]))
                return
            if function in ("setUpModule", "tearDownModule", "setup_module", "teardown_module"):
                self.shared_attrs[symbol].add(("file", self.current.split("::")[0]))
                return
            frame = frame.f_back

    def record_comparison(self, path, site, op, state):
        key = (path, site)
        entry = self.comparisons.get(key)
        if entry is None:
            entry = {"op": op, "states": defaultdict(set), "unknown": set(), "collection": False}
            self.comparisons[key] = entry
        if self.current is None or (op in ("Eq", "NotEq") and
                                    (self.phase != "call" or threading.current_thread() is not threading.main_thread())):
            entry["collection"] = True
        elif state is None:
            entry["unknown"].add(self.current)
        else:
            entry["states"][state].add(self.current)

    def exact_entry(self, path, site, op):
        exact = self.__dict__.setdefault("exact", {})
        entry = exact.get((path, site))
        if entry is None:
            entry = {"op": op, "attempts": defaultdict(int), "completed": defaultdict(int),
                     "states": defaultdict(set), "incomplete": set(), "shared": False}
            exact[(path, site)] = entry
        return entry

    def exact_context(self):
        """The test an attempt belongs to, False if it disqualifies only that test, None if shared."""
        if self.current is None or self.importing > 0 or threading.current_thread() is not threading.main_thread():
            return None
        if self.phase != "call" or InlineProcessPool.active:
            return False
        return self.current

    def record_reach(self, path, site, op):
        entry = self.exact_entry(path, site, op)
        context = self.exact_context()
        if context is None:
            entry["shared"] = True
        elif context is False:
            entry["incomplete"].add(self.current)
        else:
            entry["attempts"][context] += 1

    def record_exact(self, path, site, op, state):
        entry = self.exact_entry(path, site, op)
        context = self.exact_context()
        if context is None:
            entry["shared"] = True
        elif context is False:
            entry["incomplete"].add(self.current)
        elif state is None:
            entry["incomplete"].add(context)
        else:
            entry["completed"][context] += 1
            entry["states"][state].add(context)

    def opened(self, file, mode="r"):
        if getattr(self.audit_state, "busy", False) or isinstance(file, int) or not isinstance(mode, str) or ("+" not in mode and any(c in mode for c in "wax")):
            return
        self.audit_state.busy = True
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
            if threading.current_thread() is not threading.main_thread():
                # A worker can outlive its submitting test.
                scope = ("all", "")
            elif self.current and self.phase == "call":
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
                    if path.suffix in (".py", ".pyc"):
                        return
                    # Collection-time resource data may initialize shared state.
                    scope = ("all", "")
            self.reads[name].add(scope)
        except (OSError, TypeError, ValueError):
            pass
        finally:
            self.audit_state.busy = False

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

    active = 0  # depth of submitted work running inline

    def __init__(self, max_workers=None, mp_context=None, initializer=None, initargs=(), **kwargs):
        self._initializer, self._initargs, self._ready = initializer, tuple(initargs), False

    def submit(self, fn, /, *args, **kwargs):
        from concurrent.futures import Future
        import pickle
        future = Future()
        InlineProcessPool.active += 1
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
        finally:
            InlineProcessPool.active -= 1
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
    started = time.monotonic()
    model = SourceModel(root)
    modeled = time.monotonic()
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
                rec.end_test()
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
    collected = time.monotonic()
    write_map(root, model, rec, cov.get_data(), destination)
    print(f"map seconds: model={modeled-started:.2f} suite={collected-modeled:.2f} "
          f"serialization and aggregation={time.monotonic()-collected:.2f}", file=sys.stderr)


def write_map(root, model, rec, data, destination):
    """Combine observations with source dependencies; independently testable."""
    bit = {name: 1 << i for i, name in enumerate(rec.tests)}
    shared_masks = {}
    for test, test_bit in bit.items():
        for scope in (("file", test.split("::")[0]), ("class", test.rsplit("::", 1)[0])):
            shared_masks[scope] = shared_masks.get(scope, 0) | test_bit
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
    # Raw per-line test coverage, before symbol users are merged into lines.
    covered = {name: dict(entry["lines"]) for name, entry in files.items()}
    # A test that changes a tracked mapping's values leaves state for later
    # tests (for example a registry repopulated after another test cleared it).
    # Lines of the writing functions belong to later readers of those keys.
    for (name, start, end), contexts in getattr(rec, "writer_credit", {}).items():
        value = mask(contexts)
        lines = files[name]["lines"]
        for line in range(start, end + 1):
            if line in lines:
                lines[line] |= value
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
    users = {}
    for key, symbol in model.symbols.items():
        entry = files[key[0]]
        value = mask(rec.attrs.get(key, ()))
        for scope in rec.shared_attrs.get(key, ()):
            value |= shared_masks[scope]
        lines = entry["lines"]
        if lines:
            for line in range(symbol["start"], symbol["end"] + 1):
                value |= lines.get(line, 0)
        users[key] = value
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
        if observation["op"] in ("Eq", "NotEq") and "p:1" in states:
            # Equal builtin operands have fixed ordering but their identity can differ
            # across runs. The selector retains a test if either state changes.
            states["p:9"] = states["p:1"]
        observed = mask(observation["unknown"])
        for value in states.values():
            observed |= value
        unknown = mask(observation["unknown"]) | (files[name]["lines"].get(line, 0) & ~observed)
        files[name].setdefault("comparisons", {})[site] = {
            "op": observation["op"], "states": {state: format(value, "x") for state, value in states.items()},
            "unknown": format(unknown, "x")}
        if observation["op"] in ("Eq", "NotEq"):
            files[name]["comparisons"][site]["unknown_before_dispatch"] = True
    for (name, site), observation in getattr(rec, "exact", {}).items():
        if observation["shared"]:
            # An attempt outside any test (import time, other threads): no record.
            continue
        line = int(site.split(":")[0])
        complete = {test for test, count in observation["attempts"].items()
                    if observation["completed"].get(test, 0) == count} - observation["incomplete"]
        attempted = mask(observation["attempts"]) | mask(observation["incomplete"])
        complete_mask = mask(complete)
        states = {state: mask(contexts) for state, contexts in observation["states"].items()}
        files[name].setdefault("exact_comparisons", {})[site] = {
            "op": observation["op"], "complete": format(complete_mask, "x"),
            "states": {state: format(value, "x") for state, value in states.items() if value},
            # Attempted or covering the line without a complete exact record.
            "unknown": format((attempted | covered.get(name, {}).get(line, 0)) & ~complete_mask, "x")}
    def readers(contexts):
        if None in contexts:
            return all_tests
        value = 0
        for context in contexts:
            value |= shared_masks.get(context, 0) if type(context) is tuple else bit.get(context, 0)
        return value
    for (name, symbol), info in rec.dictionaries.items():
        observations = rec.dictionary_reads.get((name, symbol), {})
        presence = getattr(rec, "dictionary_presence", {}).get((name, symbol), {})
        keys = {k: format(readers(contexts), "x") for k, contexts in observations.items()
                if k is not None}
        observed = 0
        for group in (observations, presence):
            for contexts in group.values():
                observed |= readers(contexts)
        transferred = mask(getattr(rec, "dictionary_transfers", {}).get((name, symbol), ()))
        # Coverage of the literal's own lines, without users merged in.
        lines = {str(line): format(covered.get(name, {}).get(line, 0), "x")
                 for line in range(info["start"], info["end"] + 1) if covered.get(name, {}).get(line, 0)}
        files[name].setdefault("dictionary_reads", {})[symbol] = {
            "start": info["start"], "end": info["end"],
            "whole": format(readers(observations.get(None, ())), "x"), "keys": keys,
            "presence": {("" if k is None else k): format(readers(contexts), "x") for k, contexts in presence.items()},
            "lines": lines, "transferred": format(transferred, "x"),
            # Keys ("" for the whole object) read where no single test can be
            # charged, e.g. while importing; derived values may then change.
            "shared": sorted("" if k is None else k for k, contexts in observations.items() if None in contexts),
            "unobserved": format(users[(name, symbol)] & ~(observed | transferred), "x")}
    for name, entry in files.items():
        entry["lines"] = {str(line): format(value, "x") for line, value in sorted(entry["lines"].items()) if value}
        entry["regions"] = [[a, b, k, format(v, "x")] for a, b, k, v in entry["regions"]]
        entry["readers"], entry["executed"] = format(entry["readers"], "x"), format(entry["executed"], "x")
        # Adjacent equal hex masks compress well. JSON objects retain the same
        # keys and values, including zero masks where presence is meaningful.
        for field in ("lines", "anchors"):
            entry[field] = dict(sorted(entry.get(field, {}).items(), key=lambda pair: pair[1]))
    result = {"version": 1, "revision": REVISION, "complete": True, "tests": rec.tests, "external": format(external_mask, "x"), "native": sorted(rec.native), "files": dict(sorted(files.items()))}
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
