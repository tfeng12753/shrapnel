"""Select readers of edited dictionary keys instead of every user of the literal.

Applies only when a Python edit changes nothing but literal values of existing
string keys inside tracked dictionary literals: the key sequence, every other
statement and every other value stay identical. Membership, length, truthiness
and iteration order then cannot change, so the tests that can observe the edit
are those that read an edited key's value, those that read every value, users
the map could not explain, and the coverage of the edited lines.
"""
import ast
import gzip
import hashlib
import json


def catalog_digest(names):
    return hashlib.sha256(json.dumps(names, separators=(",", ":")).encode()).hexdigest()


def load(path, tests, revision, map_path):
    try:
        with gzip.open(path, "rt") as stream:
            data = json.load(stream)
        if (data["version"] == 1 and data["revision"] == revision
                and data["catalog"] == catalog_digest(tests)
                and data["map_sha256"] == hashlib.sha256(map_path.read_bytes()).hexdigest()
                and isinstance(data["files"], dict)):
            return data["files"]
    except (OSError, EOFError, ValueError, TypeError, KeyError):
        pass
    return {}


def matched_readers(observation, decode, before, after, old, removed, inserted):
    try:
        records = observation["records"]
        if not removed:
            return None
        first, last = min(removed), max(removed)
        if not any(r["start"] <= first and last <= r["end"] for r in records.values()):
            return None
        if hashlib.sha256(old.encode()).hexdigest() == observation["sha256"]:
            return refine(records, decode, before, after, removed, inserted)
    except (AttributeError, KeyError, TypeError, IndexError, ValueError):
        pass
    return None


def same_value_shape(a, b):
    if isinstance(a, ast.Tuple) and isinstance(b, ast.Tuple):
        return len(a.elts) == len(b.elts) and all(same_value_shape(x, y) for x, y in zip(a.elts, b.elts))
    return (isinstance(a, ast.Constant) and isinstance(b, ast.Constant) and type(a.value) is type(b.value)
            and type(a.value) in (str, bytes, int, float, bool, type(None)))


def argument_schema_spans(before, after):
    """Baseline class spans for changed validation metadata, or None if unmatched."""
    def schemas(tree):
        result = []
        for cls in ast.walk(tree):
            if not isinstance(cls, ast.ClassDef):
                continue
            for assignment in cls.body:
                if isinstance(assignment, ast.Assign):
                    targets = assignment.targets
                elif isinstance(assignment, ast.AnnAssign):
                    targets = [assignment.target]
                else:
                    continue
                if any(isinstance(target, ast.Name) and target.id == "arg_types" for target in targets):
                    result.append((cls.name, ast.dump(assignment), (cls.lineno, cls.end_lineno)))
        return result

    old, new = schemas(before), schemas(after)
    if [name for name, _, _ in old] != [name for name, _, _ in new]:
        return None
    return {span for (_, before_value, span), (_, after_value, _) in zip(old, new)
            if before_value != after_value}


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
        if not all(isinstance(k, ast.Constant) and type(k.value) is str for k in old_literal.keys):
            return None
        if len({k.value for k in old_literal.keys}) != len(old_literal.keys):
            return None
        changed = []
        for key, old_value, new_value in zip(old_literal.keys, old_literal.values, new_literal.values):
            if ast.dump(old_value) == ast.dump(new_value):
                continue
            if not (isinstance(key, ast.Constant) and type(key.value) is str):
                return None
            if not same_value_shape(old_value, new_value):
                return None
            changed.append(key.value)
        if not changed:
            return None
        for info in spans[span]:
            if not {"whole", "unobserved", "keys", "lines", "presence", "transferred"} <= info.keys():
                return None
            shared = info.get("shared")
            if shared is None or "" in shared or any(key in shared for key in changed):
                # Read where no single test can be charged (for example while
                # importing): values derived from it persist, so fall back.
                return None
            result |= decode(info["whole"]) | decode(info["unobserved"])
            keys = info["keys"]
            for key in changed:
                result |= decode(keys.get(key, "0"))
            lines = info["lines"]
            for line in removed:
                result |= decode(lines.get(str(line), "0"))
    return result
