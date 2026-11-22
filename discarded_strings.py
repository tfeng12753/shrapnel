"""Recognize edits confined to a discarded string after a class attribute."""
import ast
from functools import lru_cache
import marshal
from types import CodeType


@lru_cache(maxsize=8)
def attribute_strings(tree):
    spans = []
    pending = [tree]
    while pending:
        parent = pending.pop()
        pending.extend(node for node in parent.body if isinstance(node, ast.ClassDef))
        if isinstance(parent, ast.ClassDef):
            for assignment, statement in zip(parent.body, parent.body[1:]):
                if (isinstance(assignment, (ast.Assign, ast.AnnAssign))
                        and isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant)
                        and type(statement.value.value) is str):
                    spans.append(statement)
    return spans


def surrounding_text(text, node):
    lines = text.encode().splitlines(keepends=True)
    first, last = node.lineno - 1, node.end_lineno - 1
    return (b"".join(lines[:first]) + lines[first][:node.col_offset],
            lines[last][node.end_col_offset:] + b"".join(lines[last + 1:]))


def without_locations(code):
    constants = tuple(without_locations(c) if isinstance(c, CodeType) else c for c in code.co_consts)
    return code.replace(co_consts=constants, co_firstlineno=1, co_linetable=b"")


def literal_change(before, after, old, new, positions, *, allow_relocation=False):
    if not positions:
        return False
    first, last = min(positions), max(positions)
    for node in attribute_strings(before):
        if node.lineno <= first and last <= node.end_lineno:
            for replacement in attribute_strings(after):
                if ((replacement.lineno, replacement.col_offset) == (node.lineno, node.col_offset)
                        and surrounding_text(old, node) == surrounding_text(new, replacement)):
                    # Bytecode, constants and location tables must all agree.
                    a, b = (compile(s, "<module>", "exec", dont_inherit=True) for s in (old, new))
                    if marshal.dumps(a) == marshal.dumps(b):
                        return "exact"
                    # A text-only insertion/deletion inside a multiline literal
                    # can shift the location tables of later code. Check every
                    # nested code object independently before using reader data.
                    if allow_relocation and replacement.end_lineno != node.end_lineno:
                        if marshal.dumps(without_locations(a)) == marshal.dumps(without_locations(b)):
                            return "relocated"
    return False
