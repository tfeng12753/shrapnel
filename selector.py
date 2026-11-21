"""Dependency-based test selection. No patch identifiers or sample answers are used."""
from __future__ import annotations

import argparse
import ast
import gzip
import json
import re
import sys
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

REVISION = "eb11aec4be8cb2122afe713a4816662c3f0fb2fa"
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

# Executor receivers can be passed to helpers or stored on shared classes.
# Treat these methods as potential dispatch even when the receiver's type is unknown.
DISPATCH_METHODS = {"submit", "map", "imap", "imap_unordered", "starmap", "apply", "apply_async",
                    "map_async", "starmap_async"}
PROCESS_APIS = {
    "concurrent.futures": {"ProcessPoolExecutor"},
    "multiprocessing": {"Process", "Pool", "Manager"},
    "multiprocessing.pool": {"Pool"},
    "multiprocessing.context": {"Process", "Pool", "Manager"},
    "subprocess": {"Popen", "run", "call", "check_call", "check_output", "getoutput", "getstatusoutput"},
    "asyncio": {"create_subprocess_exec", "create_subprocess_shell"},
    "os": {"fork", "forkpty", "system", "popen", "posix_spawn", "posix_spawnp", "startfile",
           "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe"},
}
PROCESS_NAMES = set().union(*PROCESS_APIS.values())
PROCESS_HINT = re.compile(
    r"\b(?:" + "|".join(sorted(DISPATCH_METHODS | PROCESS_NAMES | {"get_context"})) + r")\b"
)
SHARED_HOOKS = {"setUpClass", "tearDownClass", "setUpModule", "tearDownModule",
                "setup_class", "teardown_class", "setup_module", "teardown_module"}


def child_work_sites(source):
    """Find potential dispatch references and flag shared lifecycle contexts."""
    if not PROCESS_HINT.search(source):
        return
    tree = ast.parse(source)
    aliases = defaultdict(set)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                name = item.name.split(".")[0]
                aliases[item.asname or name].add(item.name if item.asname else name)
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            for item in node.names:
                aliases[item.asname or item.name].add(node.module + "." + item.name)

    def qualified(node):
        if isinstance(node, ast.Name):
            return aliases.get(node.id, {node.id})
        if isinstance(node, ast.Attribute):
            return {name + "." + node.attr for name in qualified(node.value)}
        if isinstance(node, ast.Call) and "multiprocessing.get_context" in qualified(node.func):
            return {"multiprocessing.context"}
        return set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and "multiprocessing.context" in qualified(node.value):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    aliases[target.id].add("multiprocessing.context")

    shared_scopes = {None}  # Module and class bodies can run before test attribution.
    references = defaultdict(set)
    sites = set()

    def walk(node, scope):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if scope is not None:
                references[scope].add(node.name)
            scope = node.name
            if node.name in SHARED_HOOKS or any(
                isinstance(decorator, ast.Call)
                and any(name.rsplit(".", 1)[-1] == "fixture" for name in qualified(decorator.func))
                and any(keyword.arg == "scope" and
                        (not isinstance(keyword.value, ast.Constant) or keyword.value.value != "function")
                        for keyword in decorator.keywords)
                for decorator in node.decorator_list
            ):
                shared_scopes.add(scope)
            # Decorators and defaults execute in the enclosing scope.
            for child in ast.iter_child_nodes(node):
                if child not in node.body:
                    walk(child, None)
            for statement in node.body:
                walk(statement, scope)
            return
        if isinstance(node, (ast.Name, ast.Attribute)) and isinstance(node.ctx, ast.Load):
            # ProcessPoolExecutor starts work on dispatch. A constructor in a
            # shared hook does not by itself make every test a worker user.
            references[scope].add(node.id if isinstance(node, ast.Name) else node.attr)
            for name in qualified(node):
                module, _, member = name.rpartition(".")
                if member in PROCESS_APIS.get(module, ()) and name != "concurrent.futures.ProcessPoolExecutor":
                    sites.add((node.lineno, scope))
            if isinstance(node, ast.Attribute) and node.attr in DISPATCH_METHODS:
                sites.add((node.lineno, scope))
        if (isinstance(node, ast.Call) and "getattr" in qualified(node.func) and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str)):
            attr = node.args[1].value
            if attr in DISPATCH_METHODS or any(attr in PROCESS_APIS.get(name, ()) for name in qualified(node.args[0])):
                sites.add((node.lineno, scope))
        for child in ast.iter_child_nodes(node):
            walk(child, scope)

    walk(tree, None)
    pending = list(shared_scopes)
    while pending:
        for name in references[pending.pop()] - shared_scopes:
            shared_scopes.add(name)
            pending.append(name)

    for line, scope in sites:
        yield line, scope in shared_scopes


def split(tests: list[dict]) -> list[list[int]]:
    """Choose the cheapest LPT schedule over 1..8 workers, in integer milliseconds."""
    if not tests:
        return []
    ordered = sorted(((round(t["seconds"] * 1000), t["number"]) for t in tests), reverse=True)
    total = sum(duration for duration, _ in ordered)
    longest = ordered[0][0]
    best, best_cost = [], None
    for count in range(1, min(8, len(tests)) + 1):
        # Even perfect packing cannot beat either the longest test or the mean.
        lower_bound = max(longest, (total + count - 1) // count) + 2000 * count
        if best_cost is not None and lower_bound >= best_cost:
            continue
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
    expecting_file = False
    for raw in text.splitlines(keepends=True):
        if raw.startswith("diff --git "):
            if expecting_file:
                raise ValueError("unsupported diff section")
            expecting_file = True
            current, hunk = None, None
        elif raw.startswith("--- ") and hunk is None:
            old = raw[4:].strip().split("\t")[0]
            current = {"old": old[2:] if old.startswith("a/") else old, "hunks": []}
        elif raw.startswith("+++ ") and current is not None and hunk is None:
            new = raw[4:].strip().split("\t")[0]
            current["new"] = new[2:] if new.startswith("b/") else new
            changes.append(current)
            expecting_file = False
        elif raw.startswith("@@ ") and current is not None:
            m = HUNK.match(raw)
            if not m:
                raise ValueError("unsupported hunk")
            hunk = {"start": int(m[1]), "new_start": int(m[3]), "old_count": int(m[2] or 1), "new_count": int(m[4] or 1), "rows": []}
            current["hunks"].append(hunk)
        elif hunk is not None and raw[:1] in (" ", "+", "-"):
            hunk["rows"].append(raw)
        elif raw.startswith("\\ No newline") and hunk is not None and hunk["rows"]:
            hunk["rows"][-1] = hunk["rows"][-1].rstrip("\r\n")
    if expecting_file or not changes or any("new" not in c or not c["hunks"] for c in changes):
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
        if len(output) != h["new_start"] - (1 if h["new_count"] else 0):
            raise ValueError("new hunk position does not match patched output")
        cursor, old_count, new_count = start, 0, 0
        block_start, added_count = cursor, 0
        def flush_block():
            removed_count = cursor - block_start
            if added_count and not removed_count:
                inserted.add(block_start)
            elif added_count and (removed_count, added_count) != (1, 1):
                # Adjacent deletions don't explain all additions. Include the old
                # regions touched by the whole block, even for equal row counts.
                inserted.update(range(block_start + 1, cursor + 1))
        for row in h["rows"]:
            kind, content = row[0], row[1:]
            if kind == " ":
                flush_block()
            if kind in " -":
                if cursor >= len(lines) or lines[cursor] != content:
                    raise ValueError("patch does not match pinned source")
                if kind == "-":
                    removed.add(cursor + 1)
                cursor += 1
                old_count += 1
            if kind in " +":
                output.append(content)
                new_count += 1
                if kind == "+":
                    added_count += 1
                    if cursor - block_start == 1 and added_count == 1:
                        old_line = lines[block_start]
                        if re.match(r"[ \t]*", old_line)[0] != re.match(r"[ \t]*", content)[0]:
                            inserted.add(cursor)
            if kind == " ":
                block_start, added_count = cursor, 0
        flush_block()
        if old_count != h["old_count"] or new_count != h["new_count"]:
            raise ValueError("hunk length mismatch")
    output.extend(lines[cursor:])
    return "".join(output), removed, inserted


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


def relation_outcome(state, op):
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


@lru_cache(maxsize=8)
def baseline_tree(source):
    return ast.parse(source, type_comments=True)


def classify_python_edit(before, after):
    """Return [] for identical ASTs, operator changes for refinable edits, else None."""
    changes = []
    supported = (ast.Eq, ast.NotEq, ast.Is, ast.IsNot, ast.Lt, ast.LtE, ast.Gt, ast.GtE)

    def matches(a, b):
        if type(a) is not type(b):
            return False
        if isinstance(a, list):
            return len(a) == len(b) and all(matches(x, y) for x, y in zip(a, b))
        if not isinstance(a, ast.AST):
            return a == b
        for field, value in ast.iter_fields(a):
            other = getattr(b, field)
            if (field == "ops" and isinstance(a, ast.Compare)
                    and len(value) == len(other) == 1 and type(value[0]) is not type(other[0])):
                if not isinstance(value[0], supported) or not isinstance(other[0], supported):
                    return False
                changes.append((f"{a.lineno}:{a.col_offset}", type(value[0]).__name__, type(other[0]).__name__))
            elif not matches(value, other):
                return False
        return True

    return changes if matches(before, after) else None


def comparison_changes(old, new):
    """Return operator-only differences using baseline source positions."""
    return classify_python_edit(baseline_tree(old), ast.parse(new, type_comments=True)) or None


class Selector:
    def __init__(self, tests: list[dict], source: Path, map_path: Path, *, conservative_python=False,
                 relations=True, child_work=False):
        self.tests, self.source = tests, source
        self.conservative_python = conservative_python
        self.all = (1 << len(tests)) - 1
        self.files, self.complete = {}, False
        self.external = 0
        self.child_work = 0
        self.native = set()
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
            self.native = set(data.get("native", ()))
            for name, entry in data["files"].items():
                readers = decode(entry.get("readers", "0"))
                if conservative_python:
                    self.files[name] = {"readers": readers}
                    continue
                comparison_sites = entry.get("comparisons", {}) if relations else {}
                self.files[name] = {
                    "lines": {int(k): decode(v) for k, v in entry.get("lines", {}).items()},
                    "regions": [(a, b, kind, decode(v)) for a, b, kind, v in entry.get("regions", [])],
                    "anchors": {int(k): decode(v) for k, v in entry.get("anchors", {}).items()},
                    "comparisons": {site: {"op": info["op"], "unknown": decode(info["unknown"]),
                        "states": {state: decode(v) for state, v in info["states"].items()}}
                        for site, info in comparison_sites.items()},
                    "readers": readers,
                    "executed": decode(entry.get("executed", "0")),
                }
            mapped_ids = set(data["tests"])
            missing = [t["id"] for t in tests if t["id"] not in mapped_ids]
            self.complete = bool(data.get("complete")) and not missing
            if not self.complete:
                print("dependency map incomplete; using full-suite fallback", file=sys.stderr)
            elif child_work and not conservative_python:
                self.child_work = self.child_work_mask()
        except (OSError, ValueError, KeyError, IndexError, TypeError) as e:
            print(f"dependency map unavailable ({type(e).__name__}); using full-suite fallback", file=sys.stderr)

    def read_source(self, name):
        if name not in self.sources:
            path = (self.source / name).resolve()
            if not path.is_relative_to(self.source.resolve()):
                raise ValueError("invalid patch path")
            self.sources[name] = path.read_text(encoding="utf-8")
        return self.sources[name]

    def child_work_mask(self):
        catalog = {}
        for test in self.tests:
            name = test.get("file", test["id"].split("::", 1)[0])
            catalog[name] = catalog.get(name, 0) | (1 << test["number"])

        result = 0
        try:
            for name in sorted(self.files.keys() | catalog.keys()):
                if not name.endswith(".py"):
                    continue
                entry = self.files.get(name)
                if not entry:
                    result |= catalog.get(name, 0)
                    continue
                for line, shared in child_work_sites(self.read_source(name)):
                    observed = entry["lines"].get(line, entry["anchors"].get(line, 0))
                    if shared or not observed:
                        # Class/module setup may be attributed to the first
                        # test only. Modules without local tests can have
                        # callers from other files.
                        local_tests = catalog.get(name, 0)
                        if not local_tests:
                            return self.all
                        observed |= local_tests
                    result |= observed
        except (ValueError, SyntaxError, OSError, UnicodeError, RecursionError):
            return self.all
        return result

    def select(self, patch: str, trace=None) -> int:
        # The offline profiler supplies trace(mask, reason, path, location).
        def include(mask, reason, path=None, location=None):
            if trace is not None and mask:
                trace(mask, reason, path, location)
            return mask

        # An incomplete map must never silently omit a test absent from map generation.
        if not self.complete:
            return include(self.all, "dependency map unavailable or incomplete")
        try:
            changes = parse_patch(patch)
            result = 0
            for c in changes:
                name = c["old"]
                if name != c["new"]:
                    return include(self.all, "path changed", name)
                old = self.read_source(name)
                new, removed, inserted = apply_change(old, c)
                entry = self.files.get(name)
                if name.endswith(".py"):
                    # Compare every file: a harmless-looking comment may accompany a fixture edit.
                    changes_at_sites = classify_python_edit(baseline_tree(old), ast.parse(new, type_comments=True))
                    if changes_at_sites == []:
                        # Tests may inspect comments or exact source text.
                        if entry is None:
                            return include(self.all, "unmapped source", name)
                        result |= include(entry["readers"], "file reader", name)
                        continue
                    if self.conservative_python:
                        return include(self.all, "conservative executable Python selection", name)
                    result |= include(self.child_work, "potential untraced child work", name)
                    refined = self.refine_comparisons(changes_at_sites, entry)
                    if refined is not None:
                        result |= include(refined, "comparison boundary or unknown", name)
                        result |= include(entry["readers"], "file reader", name)
                        result |= include(self.external, "external dependency", name)
                        continue
                    if import_hazard(old, new):
                        return include(self.all, "import-time change", name)
                result |= include(self.external, "external dependency", name)
                if entry is None:
                    return include(self.all, "unmapped file", name)
                if not name.endswith(".py"):
                    result |= include(entry["readers"], "file reader", name)
                    # Unknown readers are uncertainty, not proof of irrelevance.
                    if not entry["readers"]:
                        return include(self.all, "unknown file readers", name)
                    continue
                result |= include(entry["readers"], "file reader", name)
                for line in removed:
                    result |= include(entry["lines"].get(line, entry["anchors"].get(line, 0)), "affected line or anchor", name, line)
                for anchor in inserted:
                    # An insertion can create a new branch or override, so select users
                    # of the containing symbol, including presently unexecuted paths.
                    regions = [r for r in entry["regions"] if r[0] <= anchor <= r[1]]
                    if regions:
                        size = min(b - a for a, b, _, _ in regions)
                        for a, b, _, mask in regions:
                            if b - a == size:
                                result |= include(mask, "insertion region", name, (a, b))
                    else:
                        result |= include(entry["executed"], "file execution for unscoped insertion", name, anchor)
                # Constants, defaults, decorators, and class-body edits execute at
                # import time. Regions include their runtime attribute/symbol users.
                for a, b, kind, mask in entry["regions"]:
                    if kind not in ("function", "class") and any(a <= line <= b for line in removed):
                        result |= include(mask, "definition dependency", name, (a, b))
                if not removed and not inserted:
                    return include(self.all, "no changed positions", name)
            return result
        except (ValueError, SyntaxError, OSError, UnicodeError, RecursionError) as error:
            # Syntax/import-shape uncertainty can break every test's collection.
            return include(self.all, "analysis failed: " + type(error).__name__)

    def refine_comparisons(self, changes, entry):
        if not changes or not entry or not entry["comparisons"]:
            return None
        result = 0
        for site, before, after in changes:
            info = entry["comparisons"].get(site)
            if info is None or info["op"] != before:
                return None
            result |= info["unknown"]
            for state, users in info["states"].items():
                a, b = relation_outcome(state, before), relation_outcome(state, after)
                if a is None or b is None or a != b:
                    result |= users
        return result

    def probe(self, patch, mask, budget):
        tests = self.chosen(mask)
        if not tests or budget < .1 or not self.native or mask & (self.external | self.child_work):
            return mask, 0.0
        if any(t["id"] not in self.native or t["seconds"] > .15 for t in tests):
            return mask, 0.0
        if sum(t["seconds"] for t in tests) > .8:
            return mask, 0.0
        changes = parse_patch(patch)
        if any(not c["old"].endswith(".py") or c["old"].startswith("tests/") or c["old"] != c["new"] for c in changes):
            return mask, 0.0
        overlays = {c["old"]: apply_change(self.read_source(c["old"]), c)[0] for c in changes}
        from native_probes import verify
        passed, cpu = verify(self.source, overlays, tests, wall_seconds=min(1.5, budget + .1), cpu_seconds=min(1.0, budget))
        for test in tests:
            if test["id"] in passed:
                mask &= ~(1 << test["number"])
        return mask, cpu

    def chosen(self, mask):
        return [t for t in self.tests if mask & (1 << t["number"])]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=Path("/data"))
    parser.add_argument("--source", type=Path, default=Path("/opt/sqlglot"))
    parser.add_argument("--map", type=Path, default=Path("/app/dependencies.json.gz"))
    parser.add_argument("--probe-budget", type=float)
    python_mode = parser.add_mutually_exclusive_group()
    python_mode.add_argument("--conservative-python", action=argparse.BooleanOptionalAction, default=None)
    python_mode.add_argument("--child-work", action=argparse.BooleanOptionalAction, default=None,
                             help="Use targeted selection plus potential untraced child work")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--relations", dest="relations", action="store_true")
    group.add_argument("--no-relations", dest="relations", action="store_false")
    parser.set_defaults(relations=None)
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("selector-config.json"))
    args = parser.parse_args()
    try:
        config = json.loads(args.config.read_text())
    except (OSError, ValueError):
        config = {}
    if args.probe_budget is None:
        args.probe_budget = float(config.get("probe_budget", 8.0))
    if args.relations is None:
        args.relations = bool(config.get("relations", True))
    tests = json.loads((args.data / "tests.json").read_text())["tests"]
    conservative = args.conservative_python
    child_work = args.child_work
    if conservative is None:
        conservative = not child_work and config.get("conservative_python", False)
    if child_work is None:
        child_work = args.conservative_python is None and not conservative and config.get("child_work", False)
    selector = Selector(tests, args.source, args.map, conservative_python=conservative, relations=args.relations,
                        child_work=child_work)
    schedules = {}
    remaining = max(0.0, args.probe_budget)
    for patch in sorted((args.data / "commits").glob("*.patch")):
        text = patch.read_text(encoding="utf-8")
        mask = selector.select(text)
        if selector.complete and remaining > 0:
            try:
                mask, cpu = selector.probe(text, mask, remaining)
                remaining -= cpu
            except (OSError, ValueError, SyntaxError):
                pass
        if mask not in schedules:
            schedules[mask] = split(selector.chosen(mask))
        print(json.dumps({"commit": patch.stem, "workers": schedules[mask]}, separators=(",", ":")))


if __name__ == "__main__":
    main()
