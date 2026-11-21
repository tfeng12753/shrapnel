"""Which class attributes can carry data from fixture files opened during setUpClass.

Build-time only. Class setup reads are credited to every test of the class,
because the map cannot see which test uses which loaded data. This module does
a conservative statement-level data-flow pass over one setUpClass body:

* Each top-level statement of the body is one unit. The recorder notes which
  statement was executing (the setUpClass frame's line) when a file opened.
* Data flows forward through local names and ``cls.<attr>`` references. Any
  attribute referenced in a statement (read, written, passed or mutated) is
  treated as possibly receiving everything that flows into that statement.
* Anything this cannot follow makes the whole class unrefinable: ``cls`` used
  other than as ``cls.<attr>``, ``super()``, ``global``/``nonlocal``, or a
  store into an attribute or item of anything that is not a local variable
  (for example a module global). Unrefinable classes keep the existing
  class-wide attribution.

Known limit: a method call on a module-level object that stores loaded data
there (``SCHEMA.update(rows)``) is not detected. The selector must still verify
that setUpClass itself succeeds on the patched files before narrowing, since a
crash there fails every test of the class.
"""
import ast

CLASS_ONLY = ("setUpClass", "tearDownClass", "setup_class", "teardown_class")


def find_function(tree, firstlineno, name):
    """The FunctionDef for a code object (co_firstlineno may point at a decorator)."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            if firstlineno in [node.lineno] + [d.lineno for d in node.decorator_list]:
                return node
    return None


def _params(func):
    a = func.args
    names = [x.arg for x in a.posonlyargs + a.args + a.kwonlyargs]
    names += [x.arg for x in (a.vararg, a.kwarg) if x]
    return set(names)


def _assigned(func):
    """Names bound directly in func's own scope (not inside nested functions)."""
    names = set()
    stack = list(func.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
            continue
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names |= {(a.asname or a.name).split(".")[0] for a in node.names}
        stack.extend(ast.iter_child_nodes(node))
    return names


class _Unit(ast.NodeVisitor):
    """Names, cls attributes, and unsupported constructs in one top-level statement."""

    def __init__(self, cls_name, local):
        self.cls_name, self.local = cls_name, local
        self.loads, self.stores, self.attrs = set(), set(), set()
        self.unsupported = []

    def _base(self, node):
        while isinstance(node, (ast.Attribute, ast.Subscript)):
            node = node.value
        return node.id if isinstance(node, ast.Name) else None

    def _check_store(self, node, kind):
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            base = self._base(node.value)
            if base != self.cls_name and base not in self.local:
                self.unsupported.append(f"store to non-local {kind} at line {node.lineno}")

    def visit_Attribute(self, node):
        if isinstance(node.value, ast.Name) and node.value.id == self.cls_name:
            self.attrs.add(node.attr)
            return
        self._check_store(node, "attribute")
        self.generic_visit(node)

    def visit_Subscript(self, node):
        self._check_store(node, "item")
        self.generic_visit(node)

    def visit_Name(self, node):
        if node.id == self.cls_name:
            self.unsupported.append(f"bare {self.cls_name} at line {node.lineno}")
        elif isinstance(node.ctx, ast.Store):
            self.stores.add(node.id)
        else:
            self.loads.add(node.id)

    def visit_Call(self, node):
        if isinstance(node.func, ast.Name) and node.func.id == "super":
            self.unsupported.append(f"super() at line {node.lineno}")
        self.generic_visit(node)

    def visit_Global(self, node):
        self.unsupported.append(f"global/nonlocal at line {node.lineno}")

    visit_Nonlocal = visit_Global

    def visit_FunctionDef(self, node):
        # A nested helper is a local; files it opens are attributed to the
        # statement that calls it. Its cls references and non-local stores count.
        self.stores.add(node.name)
        inner = _Unit(self.cls_name, self.local | _params(node) | _assigned(node))
        for statement in node.body:
            inner.visit(statement)
        for default in node.args.defaults + [d for d in node.args.kw_defaults if d]:
            self.visit(default)
        self.attrs |= inner.attrs
        self.loads |= inner.loads - _params(node) - _assigned(node)
        self.unsupported += inner.unsupported

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node):
        inner = _Unit(self.cls_name, self.local | {a.arg for a in node.args.args})
        inner.visit(node.body)
        self.attrs |= inner.attrs
        self.loads |= inner.loads - {a.arg for a in node.args.args}
        self.unsupported += inner.unsupported

    def visit_ClassDef(self, node):
        self.unsupported.append(f"class definition at line {node.lineno}")


def setup_flow(func, opened):
    """Map each opened file to the cls attributes it can reach.

    ``opened`` maps file name -> set of source lines (the setUpClass frame's
    current line when the file was opened). Returns (files_to_attrs, problems).
    A file whose open line falls outside every top-level statement is left
    out of files_to_attrs and keeps class-wide attribution. A non-empty problem
    list means nothing in this class can be refined.
    """
    params = [a.arg for a in func.args.posonlyargs + func.args.args]
    if not params:
        return {}, ["no class argument"]
    cls_name = params[0]
    local = _params(func) - {cls_name} | _assigned(func)
    units, problems = [], []
    for statement in func.body:
        unit = _Unit(cls_name, local)
        unit.visit(statement)
        units.append((statement.lineno, statement.end_lineno, unit))
        problems += unit.unsupported
    if problems:
        return {}, problems

    opened_at, unmapped = {}, set()
    for name, lines in opened.items():
        for line in lines:
            index = next((i for i, (s, e, _) in enumerate(units) if s <= line <= e), None)
            if index is None:
                unmapped.add(name)
            else:
                opened_at.setdefault(index, set()).add(name)

    local_files, attr_files = {}, {}
    for index, (_, _, unit) in enumerate(units):
        incoming = set(opened_at.get(index, ()))
        for name in unit.loads:
            incoming |= local_files.get(name, set())
        for attr in unit.attrs:
            incoming |= attr_files.get(attr, set())
        for name in unit.stores:
            local_files.setdefault(name, set()).update(incoming)
        for attr in unit.attrs:
            attr_files.setdefault(attr, set()).update(incoming)

    return {name: sorted(a for a, files in attr_files.items() if name in files)
            for name in opened if name not in unmapped}, []


def class_level_access(tree, class_name):
    """Problems that make per-test attribute tracking unreliable for a test class.

    Instance access (self.x) is observed at runtime. Class-level access from
    test code (<ClassName>.x anywhere in the module, type(...), __class__, or
    other classmethods using cls) is not, so any such use disables refinement.
    """
    problems = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == class_name:
            problems.append(f"{class_name}.{node.attr} at line {node.lineno}")
        elif isinstance(node, ast.Attribute) and node.attr == "__class__":
            problems.append(f"__class__ at line {node.lineno}")
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("type", "vars")
              and len(node.args) == 1 and isinstance(node.args[0], ast.Name) and node.args[0].id in ("self", "cls")):
            problems.append(f"{node.func.id}({node.args[0].id}) at line {node.lineno}")
        elif isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if (isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name not in CLASS_ONLY
                        and any(isinstance(d, ast.Name) and d.id in ("classmethod", "staticmethod") for d in item.decorator_list)):
                    problems.append(f"{item.name} is a class/static method")
    return problems
