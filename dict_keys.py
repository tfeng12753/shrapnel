"""Select readers of edited dictionary keys instead of every user of the literal.

Applies only when a Python edit changes nothing but literal values of existing
string keys inside tracked dictionary literals: the key sequence, every other
statement and every other value stay identical. Membership, length, truthiness
and iteration order then cannot change, so the tests that can observe the edit
are those that read an edited key's value, those that read every value, users
the map could not explain, and the coverage of the edited lines.
"""
import ast


def _literal(node):
    try:
        ast.literal_eval(node)
        return True
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return False


def _dictionaries(tree):
    """[((start, end), Dict node)] for module- and class-level dict assignments, in source order."""
    found = []
    def scan(body):
        for node in body:
            if isinstance(node, ast.ClassDef):
                scan(node.body)
            elif isinstance(node, (ast.If, ast.Try, ast.With)):
                for field in ("body", "orelse", "finalbody"):
                    scan(getattr(node, field, []))
                for handler in getattr(node, "handlers", []):
                    scan(handler.body)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(node.value, ast.Dict):
                found.append(((node.lineno, node.end_lineno), node.value))
    scan(tree.body)
    return found


def _masked(tree, literals):
    """A dump of `tree` with the values of the given Dict nodes blanked out."""
    saved = []
    for literal in literals:
        saved.append((literal, literal.values))
        literal.values = [ast.Constant(None) for _ in literal.values]
    try:
        return ast.dump(tree, include_attributes=False)
    finally:
        for literal, values in saved:
            literal.values = values


def refine(entry, decode, before, after, removed, inserted):
    """Mask of tests that can observe a value-only edit, or None if the rule does not apply.

    `entry` is the file's raw map `dictionary_reads`, `decode` turns a map hex
    mask into catalog bits, `before`/`after` are parsed module trees, and
    `removed`/`inserted` are the changed baseline line numbers.
    """
    if not entry or not removed:
        return None
    spans = {}
    for symbol, info in entry.items():
        spans.setdefault((info["start"], info["end"]), []).append(info)
    old, new = _dictionaries(before), _dictionaries(after)
    if len(old) != len(new):
        return None
    # Every changed baseline line must lie inside exactly one mapped literal.
    touched = set()
    for line in set(removed) | set(inserted):
        hits = [index for index, (span, _) in enumerate(old) if span[0] <= line <= span[1]]
        if len(hits) != 1 or old[hits[0]][0] not in spans:
            return None
        touched.add(hits[0])
    if not touched:
        return None
    # The masked comparison below proves both lists pair up statement by statement.
    before_literals = [old[index][1] for index in sorted(touched)]
    after_literals = [new[index][1] for index in sorted(touched)]
    for literal, candidate in zip(before_literals, after_literals):
        if len(candidate.keys) != len(literal.keys):
            return None
    if _masked(before, before_literals) != _masked(after, after_literals):
        return None
    result = 0
    for index, old_literal, new_literal in zip(sorted(touched), before_literals, after_literals):
        span = old[index][0]
        changed = []
        for key, old_value, new_value in zip(old_literal.keys, old_literal.values, new_literal.values):
            if ast.dump(old_value) == ast.dump(new_value):
                continue
            if not (isinstance(key, ast.Constant) and type(key.value) is str):
                return None
            if not (_literal(old_value) and _literal(new_value)):
                return None
            changed.append(key.value)
        if not changed:
            return None
        for info in spans[span]:
            result |= decode(info.get("whole", "0")) | decode(info.get("unobserved", "0"))
            keys = info.get("keys", {})
            for key in changed:
                result |= decode(keys.get(key, "0"))
            lines = info.get("lines", {})
            for line in removed:
                result |= decode(lines.get(str(line), "0"))
            if "presence" not in info or "transferred" not in info:
                # A map written before consuming-site tracking.
                return None
    return result
