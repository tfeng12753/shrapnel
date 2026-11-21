"""Dependency-based test selection. No patch identifiers or sample answers are used."""
from __future__ import annotations

import argparse
import ast
import gzip
import json
import re
import sys
from pathlib import Path

REVISION = "eb11aec4be8cb2122afe713a4816662c3f0fb2fa"
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def split(tests: list[dict]) -> list[list[int]]:
    """Choose the cheapest LPT schedule over 1..8 workers, in integer milliseconds."""
    if not tests:
        return []
    ordered = sorted(((round(t["seconds"] * 1000), t["number"]) for t in tests), reverse=True)
    best, best_cost = [], None
    for count in range(1, min(8, len(tests)) + 1):
        loads = [0] * count
        workers = [[] for _ in loads]
        for duration, number in ordered:
            i = min(range(count), key=loads.__getitem__)
            workers[i].append(number)
            loads[i] += duration
        cost = max(loads) + 2000 * count
        if best_cost is None or cost < best_cost:
            best, best_cost = workers, cost
    return best


def parse_patch(text: str) -> list[dict]:
    """Read unified diffs without executing git or writing to the checkout."""
    changes, current, hunk = [], None, None
    for raw in text.splitlines(keepends=True):
        if raw.startswith("diff --git "):
            current, hunk = None, None
        elif raw.startswith("--- ") and hunk is None:
            old = raw[4:].strip().split("\t")[0]
            current = {"old": old[2:] if old.startswith("a/") else old, "hunks": []}
        elif raw.startswith("+++ ") and current is not None and hunk is None:
            new = raw[4:].strip().split("\t")[0]
            current["new"] = new[2:] if new.startswith("b/") else new
            changes.append(current)
        elif raw.startswith("@@ ") and current is not None:
            m = HUNK.match(raw)
            if not m:
                raise ValueError("unsupported hunk")
            hunk = {"start": int(m[1]), "old_count": int(m[2] or 1), "new_count": int(m[4] or 1), "rows": []}
            current["hunks"].append(hunk)
        elif hunk is not None and raw[:1] in (" ", "+", "-"):
            hunk["rows"].append(raw)
        elif raw.startswith("\\ No newline") and hunk is not None and hunk["rows"]:
            hunk["rows"][-1] = hunk["rows"][-1].rstrip("\r\n")
    if not changes or any("new" not in c or not c["hunks"] for c in changes):
        raise ValueError("unsupported or empty diff")
    return changes


def apply_change(original: str, change: dict) -> tuple[str, set[int], set[int]]:
    """Verify the base and return new text, removed line positions, insertion anchors."""
    lines = original.splitlines(keepends=True)
    output, cursor, removed, inserted = [], 0, set(), set()
    for h in change["hunks"]:
        start = h["start"] - (1 if h["old_count"] else 0)
        if start < cursor or start > len(lines):
            raise ValueError("overlapping or out of range hunk")
        output.extend(lines[cursor:start])
        cursor, old_count, new_count = start, 0, 0
        block_removed, block_added = False, set()
        def flush_block():
            if not block_removed:
                inserted.update(block_added)
        for row in h["rows"]:
            kind, content = row[0], row[1:]
            if kind == " ":
                flush_block()
                block_removed, block_added = False, set()
            if kind in " -":
                if cursor >= len(lines) or lines[cursor] != content:
                    raise ValueError("patch does not match pinned source")
                if kind == "-":
                    removed.add(cursor + 1)
                    block_removed = True
                cursor += 1
                old_count += 1
            if kind in " +":
                output.append(content)
                new_count += 1
                if kind == "+":
                    block_added.add(cursor)
        flush_block()
        if old_count != h["old_count"] or new_count != h["new_count"]:
            raise ValueError("hunk length mismatch")
    output.extend(lines[cursor:])
    return "".join(output), removed, inserted


def semantic_tree(source: str) -> str:
    return ast.dump(ast.parse(source, type_comments=True), include_attributes=False)


def import_hazard(old: str, new: str) -> bool:
    """Unknown collection-time behavior costs less to cover than a single miss."""
    def scaffold(source):
        tree = ast.parse(source, type_comments=True)
        imports, other = [], []
        def walk(body):
            for node in body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if isinstance(node, ast.ClassDef):
                    other.append((node.name, tuple(ast.dump(x, include_attributes=False) for x in [*node.bases, *node.decorator_list, *(k.value for k in node.keywords)])))
                    walk(node.body)
                elif isinstance(node, (ast.Import, ast.ImportFrom)):
                    imports.append(ast.dump(node, include_attributes=False))
                elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                    # Literal initializers cannot throw while being evaluated.
                    # Other initializer changes may break module/class creation.
                    value = node.value
                    if value is not None:
                        try:
                            literal = ast.literal_eval(value)
                            signature = ("literal", type(literal).__name__)
                        except (ValueError, TypeError):
                            class Deferred(ast.NodeTransformer):
                                def visit_Lambda(self, node):
                                    node.body = ast.Constant(value="<deferred>")
                                    return node
                            signature = ast.dump(Deferred().visit(value), include_attributes=False)
                        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                        other.append((tuple(ast.dump(t, include_attributes=False) for t in targets), signature))
                elif isinstance(node, ast.Pass):
                    continue
                elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                    continue
                else:
                    other.append(ast.dump(node, include_attributes=False))
        walk(tree.body)
        return imports, other
    return scaffold(old) != scaffold(new)


class Selector:
    def __init__(self, tests: list[dict], source: Path, map_path: Path):
        self.tests, self.source = tests, source
        self.all = (1 << len(tests)) - 1
        self.files, self.complete = {}, False
        self.external = 0
        self.sources = {}
        try:
            opener = gzip.open if map_path.suffix == ".gz" else open
            with opener(map_path, "rt") as f:
                data = json.load(f)
            if data["revision"] != REVISION or data.get("version") != 1:
                raise ValueError("map revision or format mismatch")
            id_to_bit = {t["id"]: 1 << t["number"] for t in tests}
            translated = [id_to_bit.get(t, 0) for t in data["tests"]]
            # Translate contiguous catalog runs at once; skips may change indices.
            runs = []
            for i, target in enumerate(translated):
                if not target:
                    continue
                number = target.bit_length() - 1
                if runs and i == runs[-1][0] + runs[-1][1] and number == runs[-1][2] + runs[-1][1]:
                    runs[-1][1] += 1
                else:
                    runs.append([i, 1, number])
            runs = [(start, (1 << width) - 1, target) for start, width, target in runs]
            cache = {"0": 0}
            def decode(value):
                if value not in cache:
                    result, mask = 0, int(value, 16)
                    if mask < 0 or mask.bit_length() > len(translated):
                        raise ValueError("invalid dependency mask")
                    for start, bits, target in runs:
                        result |= ((mask >> start) & bits) << target
                    cache[value] = result
                return cache[value]
            self.external = decode(data.get("external", "0"))
            for name, entry in data["files"].items():
                self.files[name] = {
                    "lines": {int(k): decode(v) for k, v in entry.get("lines", {}).items()},
                    "regions": [(a, b, kind, decode(v)) for a, b, kind, v in entry.get("regions", [])],
                    "readers": decode(entry.get("readers", "0")),
                    "executed": decode(entry.get("executed", "0")),
                }
            mapped_ids = set(data["tests"])
            missing = [t["id"] for t in tests if t["id"] not in mapped_ids]
            self.complete = bool(data.get("complete")) and not missing
            if not self.complete:
                print("dependency map incomplete; using full-suite fallback", file=sys.stderr)
        except (OSError, ValueError, KeyError, IndexError, TypeError) as e:
            print(f"dependency map unavailable ({type(e).__name__}); using full-suite fallback", file=sys.stderr)

    def read_source(self, name):
        if name not in self.sources:
            path = (self.source / name).resolve()
            if not path.is_relative_to(self.source.resolve()):
                raise ValueError("invalid patch path")
            self.sources[name] = path.read_text(encoding="utf-8")
        return self.sources[name]

    def select(self, patch: str) -> int:
        # An incomplete map must never silently omit a test absent from map generation.
        if not self.complete:
            return self.all
        try:
            changes = parse_patch(patch)
            result = 0
            for c in changes:
                name = c["old"]
                if name != c["new"]:
                    return self.all
                old = self.read_source(name)
                new, removed, inserted = apply_change(old, c)
                entry = self.files.get(name)
                if name.endswith(".py"):
                    # Compare every file: a harmless-looking comment may accompany a fixture edit.
                    if semantic_tree(old) == semantic_tree(new):
                        # Tests may inspect comments or exact source text.
                        if entry is None:
                            return self.all
                        result |= entry["readers"]
                        continue
                    if import_hazard(old, new):
                        return self.all
                result |= self.external
                if entry is None:
                    return self.all
                if not name.endswith(".py"):
                    result |= entry["readers"]
                    # Unknown readers are uncertainty, not proof of irrelevance.
                    if not entry["readers"]:
                        return self.all
                    continue
                result |= entry["readers"]
                for line in removed:
                    result |= entry["lines"].get(line, 0)
                for anchor in inserted:
                    # An insertion can create a new branch or override, so select users
                    # of the containing symbol, including presently unexecuted paths.
                    regions = [r for r in entry["regions"] if r[0] <= anchor <= r[1]]
                    if regions:
                        size = min(b - a for a, b, _, _ in regions)
                        for a, b, _, mask in regions:
                            if b - a == size:
                                result |= mask
                    else:
                        result |= entry["executed"]
                # Constants, defaults, decorators, and class-body edits execute at
                # import time. Regions include their runtime attribute/symbol users.
                for a, b, kind, mask in entry["regions"]:
                    if kind not in ("function", "class") and any(a <= line <= b for line in removed):
                        result |= mask
                if not removed and not inserted:
                    return self.all
            return result
        except (ValueError, SyntaxError, OSError, UnicodeError, RecursionError):
            # Syntax/import-shape uncertainty can break every test's collection.
            return self.all

    def chosen(self, mask):
        return [t for t in self.tests if mask & (1 << t["number"])]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=Path("/data"))
    parser.add_argument("--source", type=Path, default=Path("/opt/sqlglot"))
    parser.add_argument("--map", type=Path, default=Path("/app/dependencies.json.gz"))
    args = parser.parse_args()
    tests = json.loads((args.data / "tests.json").read_text())["tests"]
    selector = Selector(tests, args.source, args.map)
    schedules = {}
    for patch in sorted((args.data / "commits").glob("*.patch")):
        mask = selector.select(patch.read_text(encoding="utf-8"))
        if mask not in schedules:
            schedules[mask] = split(selector.chosen(mask))
        print(json.dumps({"commit": patch.stem, "workers": schedules[mask]}, separators=(",", ":")))


if __name__ == "__main__":
    main()
