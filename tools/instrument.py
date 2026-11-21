"""Value-preserving observers used only on the unchanged baseline at build time."""
from __future__ import annotations
import ast
import builtins
import copy
from enum import Enum
import importlib.machinery
import operator
import types
from pathlib import Path

OPS = {"Eq": operator.eq, "NotEq": operator.ne, "Lt": operator.lt, "LtE": operator.le,
       "Gt": operator.gt, "GtE": operator.ge, "Is": operator.is_, "IsNot": operator.is_not}
SCALARS = {bool, int, float, str, bytes, complex, type(None)}
NONE_SAFE = SCALARS | {tuple, list, dict, set, frozenset, range, object}


def relation(left, right):
    """An abstract relation; never invoke user-defined comparisons to observe it."""
    a, b = type(left), type(right)
    # None has no ordering. With exact builtin operands, equality here is
    # identity, so no ordering operation or exception is needed.
    if left is None and type(b) is type and b in NONE_SAFE:
        return "e:9" if right is None else "e:0"
    if right is None and type(a) is type and a in NONE_SAFE:
        return "e:0"
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


EQUALITY_TYPES = (int, str, bool)


def equality_state(left, right):
    """The relation of two exact builtin int/str/bool/None operands, else None.

    None compared with one of these types is decided by identity, without user
    code; such pairs have no ordering ("e:" states). Nothing here dispatches to
    an operand-defined method.
    """
    a, b = type(left), type(right)
    if left is None or right is None:
        if (a is type(None) or a in EQUALITY_TYPES) and (b is type(None) or b in EQUALITY_TYPES):
            return "e:9" if left is right else "e:0"
        return None
    if a not in EQUALITY_TYPES or b not in EQUALITY_TYPES:
        return None
    if (a is str) != (b is str):
        return "e:0"
    if left == right:
        return "p:1"
    return "p:2" if left < right else "p:4"


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


def visit_definition_arguments(visitor, node):
    # Decorators and defaults execute outside the function. Leave annotations
    # untouched because instrumentation must preserve deferred annotations.
    node.decorator_list = [visitor.visit(n) for n in node.decorator_list]
    node.args.defaults = [visitor.visit(n) for n in node.args.defaults]
    node.args.kw_defaults = [visitor.visit(n) if n is not None else None for n in node.args.kw_defaults]


MUTATORS = frozenset({"clear", "update", "pop", "popitem", "setdefault", "__setitem__", "__delitem__"})


class DictionaryReads(ast.NodeTransformer):
    """Observe direct dictionary uses without wrapping or replacing the mapping.

    Key expressions and receiver expressions are each evaluated exactly once.
    Names shared by different classes are conservatively treated as aliases.
    """
    def __init__(self, names, declarations, parameters=()):
        self.names = names
        self.declarations = declarations
        # {callable name: keyword names its instrumented definition accepts}.
        # Passing a tracked mapping as such a keyword hands it to instrumented
        # code, whose reads through the parameter (an alias name) are observed.
        self.parameters = parameters or {}
        self.direct = set()
        self.passed = set()
        self.stored = set()

    def transfer(self, value):
        """Mark a plain reference whose value moves to another alias name."""
        if isinstance(value, (ast.Name, ast.Attribute)) and self.field(value):
            self.passed.add(value)

    def visit_Assign(self, node):
        if len(node.targets) == 1 and isinstance(node.targets[0], (ast.Name, ast.Attribute)) and self.field(node.targets[0]):
            self.transfer(node.value)
        for target in node.targets:
            self.store(target)
        return self.generic_visit(node)

    def store(self, target):
        """Writes into a tracked mapping persist for later tests."""
        if isinstance(target, ast.Subscript) and self.field(target.value):
            self.stored.add(target.value)

    def visit_AugAssign(self, node):
        self.store(node.target)
        return self.generic_visit(node)

    def visit_Delete(self, node):
        for target in node.targets:
            self.store(target)
        return self.generic_visit(node)

    def field(self, node):
        name = node.id if isinstance(node, ast.Name) else getattr(node, "attr", None)
        return name if name in self.names else None

    def call(self, helper, args, location):
        return ast.copy_location(ast.Call(ast.Name(helper, ast.Load()), args, []), location)

    def visit_Dict(self, node):
        self.generic_visit(node)
        symbol = self.declarations.get((node.lineno, node.col_offset))
        if symbol is None:
            return node
        return self.call("_shrapnel_bind_dict", [node, ast.Constant(symbol)], node)

    def visit_Subscript(self, node):
        if (isinstance(node.ctx, ast.Load) and self.field(node.value)
                and not isinstance(node.slice, (ast.Slice, ast.Tuple))):
            self.direct.add(node.value)
            self.generic_visit(node)
            return self.call("_shrapnel_dict_item", [node.value, node.slice], node)
        return self.generic_visit(node)

    def visit_Call(self, node):
        if isinstance(node.func, ast.Attribute) and node.func.attr in MUTATORS:
            if self.field(node.func.value):
                self.stored.add(node.func.value)
        callee = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None)
        for keyword in node.keywords:
            if keyword.arg in self.parameters.get(callee, ()) and keyword.arg in self.names:
                self.transfer(keyword.value)
        func = node.func
        if (not isinstance(func, ast.Attribute) or func.attr not in ("get", "__getitem__", "__contains__")
                or node.keywords or any(isinstance(arg, ast.Starred) for arg in node.args)):
            return self.generic_visit(node)
        allowed = (1, 2) if func.attr == "get" else (1,)
        if self.field(func.value) and len(node.args) in allowed:
            self.direct.add(func.value)
            self.generic_visit(node)
            return self.call("_shrapnel_dict_get", [node.func, *node.args], node)
        if (isinstance(func.value, ast.Name) and func.value.id == "dict"
                and len(node.args) - 1 in allowed and self.field(node.args[0])):
            self.direct.add(node.args[0])
            self.generic_visit(node)
            return self.call("_shrapnel_dict_descriptor", [node.func, *node.args], node)
        return self.generic_visit(node)

    def visit_Compare(self, node):
        if (len(node.ops) == 1 and isinstance(node.ops[0], (ast.In, ast.NotIn))
                and self.field(node.comparators[0])):
            self.direct.add(node.comparators[0])
            self.generic_visit(node)
            call = self.call("_shrapnel_dict_contains", [node.left, node.comparators[0]], node)
            return ast.copy_location(ast.UnaryOp(ast.Not(), call), node) if isinstance(node.ops[0], ast.NotIn) else call
        return self.generic_visit(node)

    def reference(self, node):
        name = self.field(node)
        self.generic_visit(node)
        if name and node not in self.direct and isinstance(node.ctx, ast.Load):
            if node in self.passed:
                return self.call("_shrapnel_dict_pass", [node], node)
            if node in self.stored:
                return self.call("_shrapnel_dict_store", [node], node)
            return self.call("_shrapnel_dict_whole", [node], node)
        return node

    visit_Name = reference
    visit_Attribute = reference

    def visit_match_case(self, node):
        # Pattern grammar only permits specific AST forms.
        if node.guard:
            node.guard = self.visit(node.guard)
        node.body = [self.visit(stmt) for stmt in node.body]
        return node

    # Use the existing transformer's rules for deferred annotations.
    def visit_AnnAssign(self, node):
        if isinstance(node.target, (ast.Name, ast.Attribute)) and self.field(node.target) and node.value is not None:
            self.transfer(node.value)
        return Transformer.visit_AnnAssign(self, node)

    def visit_FunctionDef(self, node):
        node.body = [self.visit(n) for n in node.body]
        visit_definition_arguments(self, node)
        return node

    visit_AsyncFunctionDef = visit_FunctionDef
    def visit_TypeAlias(self, node):
        return node


class Transformer(ast.NodeTransformer):
    def __init__(self, path, fields):
        self.path, self.fields = path, fields
        self.function_body = False

    def visit_Attribute(self, node):
        node = self.generic_visit(node)
        if isinstance(node.ctx, ast.Load) and node.attr in self.fields:
            call = ast.Call(func=ast.Name(id="_shrapnel_attribute", ctx=ast.Load()),
                            args=[node.value, ast.Constant(node.attr)], keywords=[])
            return ast.copy_location(call, node)
        return node

    def visit_Call(self, node):
        node = self.generic_visit(node)
        if isinstance(node.func, ast.Name) and node.func.id == "getattr":
            call = ast.Call(func=ast.Name(id="_shrapnel_dynamic_attribute", ctx=ast.Load()),
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
        legacy = True
        if op in ("Eq", "NotEq"):
            left, right = node.left, node.comparators[0]
            call, bound = (right, left) if isinstance(left, ast.Constant) else (left, right)
            observe = legacy = False
            if (self.function_body and node.lineno == node.end_lineno and isinstance(call, ast.Call)
                    and not call.keywords and isinstance(bound, ast.Constant)):
                if type(bound.value) is int:
                    legacy = isinstance(call.func, ast.Name) and call.func.id == "len" and len(call.args) == 1
                elif type(bound.value) is str:
                    legacy = (isinstance(call.func, ast.Attribute) and not call.args
                              and call.func.attr in ("upper", "lower", "casefold"))
            # Every single-line equality in an ordinary function body is
            # observed for the separate equality record; only the sites above
            # also enter the established comparison record.
            observe = self.function_body and node.lineno == node.end_lineno
        if not observe:
            return node
        path, site = ast.Constant(self.path), ast.Constant(f"{node.lineno}:{node.col_offset}")
        left = node.left
        if op in ("Eq", "NotEq"):
            # Mark the site as reached before either operand is evaluated, so
            # an operand that raises leaves an incomplete (unknown) record.
            reach = ast.Call(func=ast.Name(id="_shrapnel_reach", ctx=ast.Load()), args=[path, site, ast.Constant(op)], keywords=[])
            left = ast.Subscript(ast.Tuple([reach, left], ast.Load()), ast.Constant(1), ast.Load())
        args = [left, node.comparators[0], ast.Constant(op), path, site]
        if not legacy:
            args.append(ast.Constant(False))
        call = ast.Call(func=ast.Name(id="_shrapnel_comparison", ctx=ast.Load()), args=args, keywords=[])
        return ast.copy_location(call, node)

    def visit_AnnAssign(self, node):
        # Keep deferred annotations byte-for-byte equivalent in meaning.
        if node.value is not None:
            node.value = self.visit(node.value)
        return node

    def visit_ClassDef(self, node):
        enclosing = self.function_body
        self.function_body = False
        self.generic_visit(node)
        self.function_body = enclosing
        return node

    def visit_FunctionDef(self, node):
        enclosing = self.function_body
        self.function_body = not node.decorator_list and not node.name.startswith("__")
        node.body = [self.visit(n) for n in node.body]
        self.function_body = False
        visit_definition_arguments(self, node)
        self.function_body = enclosing
        return node
    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_TypeAlias(self, node):
        return node


def mark_import(tree):
    """Count module execution so reads made while importing reach every test.

    Values derived at import time persist for all later tests, so a read made
    then is not attributable to whichever test triggered the import.
    """
    body = tree.body
    start = 0
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        start = 1
    while start < len(body) and isinstance(body[start], ast.ImportFrom) and body[start].module == "__future__":
        start += 1
    rest = body[start:] or [ast.Pass()]
    location = rest[0]
    enter = ast.copy_location(ast.Expr(ast.Call(ast.Name("_shrapnel_import_enter", ast.Load()), [], [])), location)
    leave = ast.copy_location(ast.Expr(ast.Call(ast.Name("_shrapnel_import_exit", ast.Load()), [], [])), location)
    guarded = ast.copy_location(ast.Try(body=rest, handlers=[], orelse=[], finalbody=[leave]), location)
    tree.body = body[:start] + [enter, guarded]
    return tree


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
            if target is original_getattr and type(value) is dict:
                whole(value)
            return value
        def comparison(left, right, op, path, site, legacy=True):
            if op not in ("Eq", "NotEq"):
                value = OPS[op](left, right)
                recorder.record_comparison(path, site, op, relation(left, right))
                return value
            # A custom comparison can raise and the test may catch it, so the
            # state (or its absence) is recorded before dispatch, even if an
            # earlier call at this site already produced a known state.
            state = equality_state(left, right)
            if legacy:
                # The established record keeps exact same-type int/str relations only.
                kind = type(left)
                same = kind in (int, str) and type(right) is kind
                recorder.record_comparison(path, site, op, state if same else None)
            equality = getattr(recorder, "record_equality", None)
            if equality is not None:
                equality(path, site, op, state)
            return OPS[op](left, right)

        def reach(path, site, op):
            reached = getattr(recorder, "record_reach", None)
            if reached is not None:
                reached(path, site, op)
        def whole(mapping):
            if type(mapping) is dict:
                recorder.record_dictionary(mapping, None)
            return mapping

        def bind(mapping, symbols):
            recorder.bound_dictionaries[id(mapping)] = (mapping, symbols)
            return mapping

        def item(mapping, key):
            if type(mapping) is dict:
                recorder.record_dictionary(mapping, key)
            return mapping[key]

        def get(method, key, *default):
            if type(method) in (types.BuiltinMethodType, types.MethodWrapperType) and type(method.__self__) is dict:
                recorder.record_dictionary(method.__self__, key)
            return method(key, *default)

        def descriptor(method, mapping, key, *args):
            if type(mapping) is dict:
                known = any(method is op for op in (dict.get, dict.__getitem__, dict.__contains__))
                recorder.record_dictionary(mapping, key if known else None)
            return method(mapping, key, *args)

        def contains(key, mapping):
            # Membership depends only on the key set, never on a value.
            if type(mapping) is dict:
                presence = getattr(recorder, "record_presence", None)
                if presence is None:
                    recorder.record_dictionary(mapping, key)
                else:
                    presence(mapping, key)
            return key in mapping

        def passed(mapping):
            if type(mapping) is dict:
                transfer = getattr(recorder, "record_transfer", None)
                if transfer is not None:
                    transfer(mapping)
            return mapping

        def stored(mapping):
            # Also a whole read: mutating methods can return or inspect values.
            whole(mapping)
            if type(mapping) is dict:
                writer = getattr(recorder, "record_store", None)
                if writer is not None:
                    writer(mapping)
            return mapping

        def import_enter():
            recorder.importing = getattr(recorder, "importing", 0) + 1

        def import_exit():
            recorder.importing = getattr(recorder, "importing", 1) - 1

        for name, value in (("_shrapnel_dict_whole", whole), ("_shrapnel_bind_dict", bind),
                            ("_shrapnel_dict_item", item), ("_shrapnel_dict_get", get),
                            ("_shrapnel_dict_contains", contains), ("_shrapnel_dict_descriptor", descriptor),
                            ("_shrapnel_dict_pass", passed), ("_shrapnel_import_enter", import_enter),
                            ("_shrapnel_dict_store", stored),
                            ("_shrapnel_import_exit", import_exit),
                            ("_shrapnel_attribute", attribute), ("_shrapnel_dynamic_attribute", dynamic_attribute),
                            ("_shrapnel_comparison", comparison), ("_shrapnel_reach", reach)):
            self.saved[name] = getattr(builtins, name, None)
            setattr(builtins, name, value)
        self.original = importlib.machinery.SourceFileLoader.get_code
        original, root, fields = self.original, self.root, recorder.model.attr_names
        def get_code(loader, fullname):
            path = Path(loader.get_filename(fullname)).resolve()
            if not any(path.is_relative_to(root / folder) for folder in ("sqlglot", "tests")) or path.suffix != ".py":
                return original(loader, fullname)
            name = path.relative_to(root).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            declarations = getattr(recorder, "dict_declarations", {}).get(name, {})
            tree = DictionaryReads(getattr(recorder, "dictionary_names", ()), declarations,
                                   getattr(recorder, "parameter_names", {})).visit(tree)
            if path.is_relative_to(root / "sqlglot"):
                tree = Transformer(name, fields).visit(tree)
                mark_import(tree)
            ast.fix_missing_locations(tree)
            return compile(tree, str(path), "exec", dont_inherit=True)
        importlib.machinery.SourceFileLoader.get_code = get_code
        # Copies made by the copy module read every value of a tracked mapping.
        self.copy_hooks = (copy._copy_dispatch.get(dict), copy._deepcopy_dispatch.get(dict))
        shallow, deep = self.copy_hooks
        def copied(mapping):
            whole(mapping)
            return shallow(mapping)
        def deep_copied(mapping, memo, deepcopy=copy.deepcopy):
            whole(mapping)
            return deep(mapping, memo, deepcopy)
        if shallow is not None:
            copy._copy_dispatch[dict] = copied
        if deep is not None:
            copy._deepcopy_dispatch[dict] = deep_copied

    def restore(self):
        shallow, deep = getattr(self, "copy_hooks", (None, None))
        if shallow is not None:
            copy._copy_dispatch[dict] = shallow
        if deep is not None:
            copy._deepcopy_dispatch[dict] = deep
        importlib.machinery.SourceFileLoader.get_code = self.original
        for name, value in self.saved.items():
            if value is None:
                delattr(builtins, name)
            else:
                setattr(builtins, name, value)
