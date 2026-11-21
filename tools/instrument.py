"""Value-preserving observers used only on the unchanged baseline at build time."""
from __future__ import annotations
import ast
import builtins
from enum import Enum
import importlib.machinery
import operator
from pathlib import Path

OPS = {"Eq": operator.eq, "NotEq": operator.ne, "Lt": operator.lt, "LtE": operator.le,
       "Gt": operator.gt, "GtE": operator.ge, "Is": operator.is_, "IsNot": operator.is_not}
SCALARS = {bool, int, float, str, bytes, complex, type(None)}
NONE_SAFE = SCALARS | {tuple, list, dict, set, frozenset, range, object}


def relation(left, right):
    """An abstract relation; never invoke user-defined comparisons to observe it."""
    a, b = type(left), type(right)
    pure = a in SCALARS and b in SCALARS
    # String/int Enum subclasses may use builtin equality rather than identity.
    # Their operators are still pure; retain the actual observed relation.
    if a is b and issubclass(a, Enum):
        pure = (type.__getattribute__(a, "__eq__") in (object.__eq__, str.__eq__, int.__eq__)
                and type.__getattribute__(a, "__ne__") in (object.__ne__, str.__ne__, int.__ne__))
    if a is tuple and b is tuple:
        pure = len(left) <= 16 and len(right) <= 16 and all(type(x) in SCALARS for x in (*left, *right))
    if not pure:
        def identity_only(kind):
            return type.__getattribute__(kind, "__eq__") is object.__eq__ and type.__getattribute__(kind, "__ne__") is object.__ne__
        pure = identity_only(a) and identity_only(b)
        if left is None:
            pure = b in NONE_SAFE or identity_only(b)
        elif right is None:
            pure = a in NONE_SAFE or identity_only(a)
        if not pure:
            return None
    state = (1 if left == right else 0) | (8 if left is right else 0)
    try:
        # Ordering is permitted only for builtin scalar/tuple values, not objects
        # with application-defined operators.
        if (a in SCALARS and b in SCALARS) or (a is tuple and b is tuple):
            state |= 2 if left < right else 0
            state |= 4 if left > right else 0
            return "p:" + str(state)
    except TypeError:
        pass
    return "e:" + str(state)


def outcome(state, op):
    kind, value = state.split(":")
    value = int(value)
    if op == "Eq": return bool(value & 1)
    if op == "NotEq": return not value & 1
    if op == "Is": return bool(value & 8)
    if op == "IsNot": return not value & 8
    if kind != "p": return None
    if op == "Lt": return bool(value & 2)
    if op == "LtE": return bool(value & 3)
    if op == "Gt": return bool(value & 4)
    if op == "GtE": return bool(value & 5)
    return None


class Transformer(ast.NodeTransformer):
    def __init__(self, path, fields):
        self.path, self.fields = path, fields

    def visit_Attribute(self, node):
        node = self.generic_visit(node)
        if isinstance(node.ctx, ast.Load) and node.attr in self.fields:
            call = ast.Call(func=ast.Name(id="_blast_radius_attribute", ctx=ast.Load()),
                            args=[node.value, ast.Constant(node.attr)], keywords=[])
            return ast.copy_location(call, node)
        return node

    def visit_Call(self, node):
        node = self.generic_visit(node)
        if isinstance(node.func, ast.Name) and node.func.id == "getattr":
            call = ast.Call(func=ast.Name(id="_blast_radius_dynamic_attribute", ctx=ast.Load()),
                            args=[node.func, *node.args], keywords=node.keywords)
            return ast.copy_location(call, node)
        return node

    def visit_Compare(self, node):
        node = self.generic_visit(node)
        if len(node.ops) != 1:
            return node
        op = type(node.ops[0]).__name__
        def literal(expr):
            try:
                ast.literal_eval(expr)
                return True
            except (ValueError, TypeError):
                return False
        observe = op in ("Is", "IsNot") or (op in ("Lt", "LtE", "Gt", "GtE") and (literal(node.left) or literal(node.comparators[0])))
        if not observe:
            return node
        call = ast.Call(func=ast.Name(id="_blast_radius_comparison", ctx=ast.Load()),
                        args=[node.left, node.comparators[0], ast.Constant(op),
                              ast.Constant(self.path), ast.Constant(f"{node.lineno}:{node.col_offset}")], keywords=[])
        return ast.copy_location(call, node)

    def visit_AnnAssign(self, node):
        # Keep deferred annotations byte-for-byte equivalent in meaning.
        if node.value is not None:
            node.value = self.visit(node.value)
        return node

    def visit_FunctionDef(self, node):
        node.body = [self.visit(n) for n in node.body]
        node.decorator_list = [self.visit(n) for n in node.decorator_list]
        node.args.defaults = [self.visit(n) for n in node.args.defaults]
        node.args.kw_defaults = [self.visit(n) if n is not None else None for n in node.args.kw_defaults]
        return node
    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_TypeAlias(self, node):
        return node


class Instrumentation:
    def __init__(self, root, recorder):
        self.root, self.recorder = root.resolve(), recorder
        self.saved = {}

    def install(self):
        recorder = self.recorder
        def attribute(obj, name):
            value = getattr(obj, name)
            if recorder.current is not None:
                recorder.record_object_attr(obj, name)
            return value
        original_getattr = builtins.getattr
        def dynamic_attribute(target, *args, **kwargs):
            value = target(*args, **kwargs)
            if target is original_getattr and recorder.current is not None and len(args) >= 2:
                recorder.record_object_attr(args[0], args[1])
            return value
        def comparison(left, right, op, path, site):
            value = OPS[op](left, right)
            recorder.record_comparison(path, site, op, relation(left, right))
            return value
        for name, value in (("_blast_radius_attribute", attribute), ("_blast_radius_dynamic_attribute", dynamic_attribute), ("_blast_radius_comparison", comparison)):
            self.saved[name] = getattr(builtins, name, None)
            setattr(builtins, name, value)
        self.original = importlib.machinery.SourceFileLoader.get_code
        original, root, fields = self.original, self.root, recorder.model.attr_names
        def get_code(loader, fullname):
            path = Path(loader.get_filename(fullname)).resolve()
            if not path.is_relative_to(root / "sqlglot") or path.suffix != ".py":
                return original(loader, fullname)
            name = path.relative_to(root).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            tree = Transformer(name, fields).visit(tree)
            ast.fix_missing_locations(tree)
            return compile(tree, str(path), "exec", dont_inherit=True)
        importlib.machinery.SourceFileLoader.get_code = get_code

    def restore(self):
        importlib.machinery.SourceFileLoader.get_code = self.original
        for name, value in self.saved.items():
            if value is None:
                delattr(builtins, name)
            else:
                setattr(builtins, name, value)
