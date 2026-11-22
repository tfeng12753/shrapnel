"""Recognize changed prose following class assignments in otherwise equivalent modules."""

import ast


def _attribute_docs(tree):
    values = {}

    def walk(body, parents):
        for index, stmt in enumerate(body):
            if isinstance(stmt, ast.ClassDef):
                walk(stmt.body, (*parents, (index, stmt.name)))
            if not parents or index == 0 or not isinstance(stmt, ast.Expr):
                continue
            if (
                not isinstance(stmt.value, ast.Constant)
                or type(stmt.value.value) is not str
            ):
                continue
            if isinstance(body[index - 1], (ast.AnnAssign, ast.Assign)):
                values[parents, index] = stmt.value

    walk(tree.body, ())
    return values


def matches(original, patched, compare):
    before, after = _attribute_docs(original), _attribute_docs(patched)
    if not before or before.keys() != after.keys():
        return False
    restore = []
    try:
        for key, node in after.items():
            old = before[key].value
            if node.value != old:
                restore.append((node, node.value))
                node.value = old
        return bool(restore) and compare(original, patched) == []
    finally:
        for node, value in restore:
            node.value = value
