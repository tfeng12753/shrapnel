"""Narrow a value-only initializer change using observed key accesses in the pinned suite."""

import ast
import gzip
import json
from functools import lru_cache
from pathlib import Path


def table_id(items):
    return json.dumps(sorted(items), separators=(",", ":"))


@lru_cache(maxsize=1)
def evidence():
    try:
        with gzip.open(Path(__file__).parent / "maps/tokenizer-key-reads.json.gz", "rt") as stream:
            record = json.load(stream)
        from selector import REVISION
        if record.get("revision") != REVISION:
            return {}
        return record
    except (OSError, EOFError, ValueError):
        return {}


def value_keys(before, after):
    # Keep dictionary membership, insertion order, truth, and every value's type invariant.
    rows = []
    for expression in (before, after):
        if not isinstance(expression, ast.Dict) or not expression.keys:
            return None
        keys = [
            k.value
            for k in expression.keys
            if isinstance(k, ast.Constant) and type(k.value) is str
        ]
        if len(keys) != len(expression.keys) or len(keys) != len(set(keys)):
            return None
        values = []
        for tup in expression.values:
            if not isinstance(tup, ast.Tuple) or len(tup.elts) != 4:
                return None
            if any(
                not isinstance(x, ast.Constant) or type(x.value) is not int
                for x in tup.elts
            ):
                return None
            values.append(tuple(x.value for x in tup.elts))
        rows.append((keys, values))
    (keys, a), (newkeys, b) = rows
    if keys != newkeys:
        return None
    return table_id(zip(keys, a)), [k for k, x, y in zip(keys, a, b) if x != y]


def refine(s, mask, name, before, after):
    if not mask or not name.startswith("sqlglot/dialects/"):
        return mask
    records = evidence()
    if not records or not records.get("complete"):
        return mask

    def declarations(t):
        found = []
        for entry in t.body:
            if not isinstance(entry, ast.ClassDef):
                continue
            for inner in entry.body:
                if not isinstance(inner, ast.ClassDef) or inner.name != "Tokenizer":
                    continue
                for stmt in inner.body:
                    if (
                        isinstance(stmt, ast.Assign)
                        and len(stmt.targets) == 1
                        and isinstance(stmt.targets[0], ast.Name)
                        and stmt.targets[0].id == "NUMERIC_ESCAPES"
                    ):
                        found.append(stmt)
        return found

    a, b = declarations(before), declarations(after)
    if len(a) != 1 or len(b) != 1:
        return mask
    oldv, newv = a[0].value, b[0].value
    if (oldv.lineno, oldv.end_lineno, oldv.col_offset) != (
        newv.lineno,
        newv.end_lineno,
        newv.col_offset,
    ):
        return mask
    ids = value_keys(oldv, newv)
    if not ids or not ids[1]:
        return mask
    # ASTs are cached elsewhere; temporarily substitute only on the fresh patched tree.
    b[0].value = oldv
    try:
        same = ast.dump(before) == ast.dump(after)
    finally:
        b[0].value = newv
    if not same:
        return mask
    table, keys = ids
    if table not in records["reads"]:
        return mask
    reads = records["reads"][table]
    keep = set(records["unknown"]) | set(records["children"])
    for k in keys:
        users = reads.get(k, [])
        if "" in users:
            return mask
        keep.update(users)
    known = records["catalog"]
    if not hasattr(s, "key_missing"):
        s.key_missing = s.all & ~sum(s.test_bits.get(t, 0) for t in known)
    required = s.key_missing | s.files[name]["readers"] | s.external_for(name)
    for t in keep:
        required |= s.test_bits.get(t, 0)
    return mask & required
