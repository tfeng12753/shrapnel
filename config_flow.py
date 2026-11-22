"""Construct baseline coverage of reads of scalar options forwarded into a core constructor."""

import ast
import json
from pathlib import Path


def simple_target(stmt):
    if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
        return getattr(stmt.targets[0], "id", None)
    if isinstance(stmt, ast.AnnAssign):
        return getattr(stmt.target, "id", None)
    return None


def build(root, destination):
    wrapper = "sqlglot/tokens.py"
    core = "sqlglot/tokenizer_core.py"
    documents = {
        p.relative_to(root).as_posix(): ast.parse(p.read_bytes())
        for folder in ("sqlglot", "tests")
        for p in (root / folder).rglob("*.py")
    }
    tree = documents[wrapper]
    options = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "TokenizerCore"
        ):
            for kw in node.keywords:
                expr = kw.value
                if (
                    isinstance(expr, ast.Attribute)
                    and isinstance(expr.value, ast.Name)
                    and expr.value.id == "self"
                ):
                    options[expr.attr] = kw.arg, expr
    for name, (field, forward) in list(options.items()):
        if not field or not any(
            simple_target(n) == name
            and isinstance(n.value, ast.Constant)
            and type(n.value.value) is bool
            for n in ast.walk(tree)
        ):
            del options[name]
    upper_reads = {}
    for file, t in documents.items():
        for node in ast.walk(t):
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                upper_reads.setdefault(node.attr, []).append((file, node))
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value in options
            ):
                upper_reads.setdefault(node.value, []).append((file, node))
    core_tree = documents[core]
    cls = next(
        n
        for n in core_tree.body
        if isinstance(n, ast.ClassDef) and n.name == "TokenizerCore"
    )
    init = next(
        n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "__init__"
    )
    out = {}
    for option, (field, forward) in options.items():
        if upper_reads.get(option) != [(wrapper, forward)]:
            continue
        assignments = [
            n
            for n in ast.walk(init)
            if isinstance(n, ast.Assign)
            and len(n.targets) == 1
            and isinstance(n.targets[0], ast.Attribute)
            and n.targets[0].attr == field
            and isinstance(n.value, ast.Name)
            and n.value.id == field
        ]
        if (
            len(assignments) != 1
            or sum(isinstance(n, ast.Name) and n.id == field for n in ast.walk(init))
            != 1
        ):
            continue
        uses = upper_reads.get(field, [])
        if not uses or any(
            file != core or not isinstance(n.value, ast.Name) or n.value.id != "self"
            for file, n in uses
        ):
            continue
        # Use every line in the innermost enclosing function. This covers multiline expressions and
        # short-circuit evaluation even when the source-line event precedes an attribute instruction.
        spans = set()
        for _, n in uses:
            blocks = [
                f
                for f in ast.walk(cls)
                if isinstance(f, ast.FunctionDef)
                and f.lineno <= n.lineno <= f.end_lineno
            ]
            if not blocks:
                break
            f = min(blocks, key=lambda f: f.end_lineno - f.lineno)
            spans.add((f.lineno, f.end_lineno))
        else:
            out[option] = sorted(spans)
    destination.write_text(json.dumps({"source": core, "options": out}, sort_keys=True))


def literal_edit(before, after, changed):
    """One boolean initializer changed on one source line with identical surrounding syntax."""
    a, b = before.splitlines(), after.splitlines()
    if len(a) != len(b) or len(changed) != 1:
        return
    (line,) = changed
    if not 0 < line <= len(a) or a[: line - 1] != b[: line - 1] or a[line:] != b[line:]:
        return
    try:
        (old,) = ast.parse(a[line - 1].strip()).body
        (new,) = ast.parse(b[line - 1].strip()).body
    except (SyntaxError, ValueError):
        return
    if (
        not simple_target(old)
        or not simple_target(new)
        or not isinstance(old.value, ast.Constant)
        or not isinstance(new.value, ast.Constant)
        or type(new.value.value) is not bool
        or type(old.value.value) is not bool
        or old.value.value == new.value.value
    ):
        return
    new.value = old.value
    if ast.dump(old) == ast.dump(new):
        return simple_target(old)


def narrow(s, mask, path, old, new, removed):
    if path != "sqlglot/tokens.py" and not path.startswith("sqlglot/dialects/"):
        return mask
    option = literal_edit(old, new, removed)
    if not option:
        return mask
    entry = s.files.get(path)
    if not entry or not s.import_effects:
        return mask
    if not hasattr(s, "option_coverage"):
        s.option_coverage = json.loads(
            Path(__file__).with_name("option-coverage.json").read_text()
        )
    data = s.option_coverage
    spans = data["options"].get(option)
    if not spans:
        return mask
    from selector import baseline_tree

    tree = baseline_tree(old)
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
    if path != "sqlglot/tokens.py":
        classes = [
            nested
            for outer in classes
            for nested in outer.body
            if isinstance(nested, ast.ClassDef)
        ]
    if not any(
        cls.name == "Tokenizer"
        and any(
            simple_target(stmt) == option and stmt.lineno in removed
            for stmt in cls.body
        )
        for cls in classes
    ):
        return mask
    consumer = s.files.get(data["source"])
    if not consumer:
        return mask
    # Keep subprocesses and direct source readers. Failed imports/setups are re-added by select().
    if not hasattr(s, "option_children"):
        from key_evidence import evidence

        records = evidence()
        if not records:
            return mask
        s.option_children = sum(s.test_bits.get(t, 0) for t in records["children"])
        s.option_children |= s.all & ~sum(
            s.test_bits.get(t, 0) for t in records["catalog"]
        )
    keep = (
        entry["readers"]
        | s.external_for(path)
        | s.option_children
        | consumer["readers"]
    )
    for a, b in spans:
        keep |= s.span_users(consumer, a, b)
    return mask & keep


if __name__ == "__main__":
    import sys

    build(Path(sys.argv[1]), Path(sys.argv[2]))
