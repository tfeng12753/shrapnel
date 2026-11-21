"""Dependency-based test selection. No patch identifiers or sample answers are used."""
from __future__ import annotations

import argparse
import ast
import gzip
import json
import math
import re
import sys
from bisect import bisect_left
from collections import defaultdict
from difflib import SequenceMatcher
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


def schedule_seconds(tests: list[dict]) -> float:
    """Cost of the schedule split() chooses for these tests: slowest worker plus 2 s per worker."""
    workers = split(tests)
    durations = {t["number"]: round(t["seconds"] * 1000) for t in tests}
    return (2000 * len(workers) + max((sum(durations[n] for n in w) for w in workers), default=0)) / 1000


def schedule_saving(tests: list[dict], dropped: int) -> float:
    """Seconds saved by dropping the tests whose bits are in `dropped` from a final selection."""
    return schedule_seconds(tests) - schedule_seconds([t for t in tests if not dropped >> t["number"] & 1])


# A probe must save at least 1.5 times its expected CPU plus half a second.
ADMISSION_FACTOR, ADMISSION_MARGIN = 1.5, 0.5
# Expected CPU per probe kind, measured on the pinned checkout. A run replaces
# these with its own measurements per probe key once it has any.
PRIOR_CPU = {"setup": 2.0, "fixture": 0.05, "lazy": 1.35, "plan": 4.0}


def pays_for_itself(saving: float, cpu: float) -> bool:
    return math.isfinite(saving) and math.isfinite(cpu) and saving >= ADMISSION_FACTOR * cpu + ADMISSION_MARGIN


def break_even_cpu(saving: float) -> float:
    """Largest CPU a probe with this saving may spend and still pay for itself."""
    return (saving - ADMISSION_MARGIN) / ADMISSION_FACTOR


def plain_text_change(patch):
    metadata = ("old mode ", "new mode ", "rename ", "copy ", "Binary files ", "GIT binary patch")
    return not any(line.startswith(metadata) for line in patch.splitlines())


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
    return import_hazard_kind(old, new) is not None


def import_hazard_kind(old: str, new: str):
    """None, "initializer" (only assignment values evaluated at import changed), or "structural"."""
    def scaffold(source):
        tree = ast.parse(source, type_comments=True)
        imports, other = [], []
        def walk(body):
            for node in body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if isinstance(node, ast.ClassDef):
                    other.append(("class", node.name, tuple(ast.dump(x, include_attributes=False) for x in [*node.bases, *node.decorator_list, *(k.value for k in node.keywords)])))
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
                        other.append(("assign", tuple(ast.dump(t, include_attributes=False) for t in targets), signature))
                elif isinstance(node, ast.Pass):
                    continue
                elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                    continue
                else:
                    other.append(("other", ast.dump(node, include_attributes=False)))
        walk(tree.body)
        return imports, other
    (old_imports, old_other), (new_imports, new_other) = scaffold(old), scaffold(new)
    if (old_imports, old_other) == (new_imports, new_other):
        return None
    if old_imports != new_imports or len(old_other) != len(new_other):
        return "structural"
    for a, b in zip(old_other, new_other):
        if a != b and not (a[0] == b[0] == "assign" and a[1] == b[1]):
            return "structural"
    return "initializer"


def module_for(path):
    """Dotted module for a pinned sqlglot source path, or None."""
    if not path.startswith("sqlglot/") or not path.endswith(".py"):
        return None
    parts = path[:-3].split("/")
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) if all(part.isidentifier() for part in parts) else None


def relation_outcome(state, op):
    # Reject states outside the comparison observer's format rather than
    # interpreting unsupported bits as evidence for a false result.
    if state not in ("e:0", "e:1", "e:8", "e:9", "p:0", "p:1", "p:2", "p:4", "p:8", "p:9"):
        return None
    kind, _, bits = state.partition(":")
    value = int(bits)
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


# Value operators whose outcome on built-in int/str/None operands the map's
# relation states determine. Identity depends on object caching, so a swap to
# or from `is` never uses exact evidence.
EXACT_OPERATORS = ("Eq", "NotEq", "Lt", "LtE", "Gt", "GtE")


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
        if isinstance(a, ast.TypeIgnore):
            # "# type: ignore" comments have no runtime effect, but their line
            # number is a field: any line inserted or removed above one would
            # otherwise make a comment-only edit look like a code change.
            return a.tag == b.tag
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


@lru_cache(maxsize=8)
def comparison_contexts(tree):
    """Index comparison spans and whether they are inside function bodies."""
    found = {}

    def visit(node, deferred=False):
        if isinstance(node, ast.Compare):
            site = f"{node.lineno}:{node.col_offset}"
            found[site] = (node.lineno, node.end_lineno, deferred)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.iter_child_nodes(node):
                visit(child, child in node.body and node.name not in SHARED_HOOKS)
        else:
            # A module-level lambda can be called during import. Inside a
            # function, lambdas, comprehensions and their defaults stay local.
            for child in ast.iter_child_nodes(node):
                visit(child, deferred)

    visit(tree)
    return found


def short_circuit_edits(before, after, affected_lines):
    """Yield baseline spans whose operand evaluation order may have changed."""
    affected = sorted(affected_lines)

    def affected_span(start, end):
        index = bisect_left(affected, start)
        return index < len(affected) and affected[index] <= end

    def old_boolean_spans(node):
        if isinstance(node, ast.AST):
            for child in ast.walk(node):
                if isinstance(child, ast.BoolOp) and affected_span(child.lineno, child.end_lineno):
                    yield child.lineno, child.end_lineno

    def shape(node):
        if isinstance(node, ast.BoolOp):
            return type(node.op), tuple(shape(value) for value in node.values)
        return None

    def compare(left, right):
        if isinstance(left, ast.expr) and shape(left) != shape(right):
            # Include the outer expression when precedence changes move operands
            # across operators. A test may have stopped before the edited line.
            yield left.lineno, left.end_lineno
            return
        if isinstance(left, list) and isinstance(right, list):
            if len(left) == len(right):
                for old_item, new_item in zip(left, right):
                    yield from compare(old_item, new_item)
                return
            if not left:
                return
            if len(left) + len(right) > 128:
                # Keep alignment bounded on large, repetitive statement lists.
                for item in left:
                    yield from old_boolean_spans(item)
                return
            # Align unchanged items when a patch also inserts or removes
            # statements. Pairing by position can hide the relevant edit.
            def keys(items):
                return [ast.dump(item) if isinstance(item, ast.AST) else item for item in items]

            matcher = SequenceMatcher(None, keys(left), keys(right), autojunk=False)
            for tag, i, j, k, end in matcher.get_opcodes():
                if tag in ("replace", "delete"):
                    if j - i == end - k:
                        yield from compare(left[i:j], right[k:end])
                    else:
                        for item in left[i:j]:
                            yield from compare(item, None)
            return
        if not isinstance(left, ast.AST):
            return
        if type(left) is type(right):
            for field, value in ast.iter_fields(left):
                yield from compare(value, getattr(right, field))
        else:
            yield from old_boolean_spans(left)

    yield from compare(before, after)


class Selector:
    def __init__(self, tests: list[dict], source: Path, map_path: Path, *, conservative_python=False,
                 relations=None, child_work=False, import_closure=None, import_check_budget=0.0,
                 setup_check_budget=0.0, executor_fixtures=False, executor_plans=None, executor_plan_budget=0.0,
                 lazy_test_budget=0.0, schedule_admission=False, exact_comparisons=False):
        self.tests, self.source = tests, source
        # Admit every narrowing probe only after a patch's selection is final,
        # and only if dropping its tests saves enough schedule time for its CPU.
        self.schedule_admission = schedule_admission
        self.measured_cpu = {}
        # Narrow value comparisons only with map evidence that every evaluation
        # completed on built-in int/str/None operands (see refine_comparisons).
        self.exact_comparisons = exact_comparisons
        self.executor_fixtures = executor_fixtures
        self.executor_plans = executor_plans.resolve() if executor_plans and executor_plan_budget > 0 else None
        self.executor_plan_budget = executor_plan_budget
        self.lazy_test_budget = lazy_test_budget
        self.test_bits = {test["id"]: 1 << test["number"] for test in tests}
        # Fixture reads in setUpClass may be attributed to the tests that use the
        # loaded attributes. Relying on that requires setUpClass to still succeed on
        # the patched files, which a forked run checks (CPU charged to this budget).
        self.setup_check_budget = setup_check_budget
        # Changes confined to assignment values evaluated at import time can only
        # break collection by raising. A forked import of the patched module checks that.
        self.import_check_budget = import_check_budget
        self.import_closure = None
        self.closure_external = 0
        self.conservative_python = conservative_python
        # Historical direct callers omit this option. An explicit opt-in
        # (including the CLI) requires evidence for every invocation of the
        # old operator. Map v1 records only completed comparisons.
        self.relations = relations is None or bool(relations)
        self.strict_relations = relations is not None and self.relations
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
            translated = [self.test_bits.get(t, 0) for t in data["tests"]]
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
            if import_closure is not None:
                # External tests whose child process only runs `import sqlglot` can
                # only be affected by files that import executes or reads.
                for test_id in import_closure.get("tests", ()):
                    self.closure_external |= self.test_bits.get(test_id, 0)
                self.closure_external &= self.external
                self.import_closure = set(import_closure.get("files", ()))
            self.native = set(data.get("native", ()))
            for name, entry in data["files"].items():
                readers = decode(entry.get("readers", "0"))
                setup = {cid: decode(m) for cid, m in entry.get("setup_classes", {}).items()}
                if conservative_python or name.endswith(".py"):
                    # Only fixture edits use the per-test class-setup attribution.
                    for members in setup.values():
                        readers |= members
                    setup = {}
                if conservative_python:
                    self.files[name] = {"readers": readers}
                    continue
                comparison_sites = entry.get("comparisons", {}) if self.relations else {}
                self.files[name] = {
                    "lines": {int(k): decode(v) for k, v in entry.get("lines", {}).items()},
                    "regions": [(a, b, kind, decode(v)) for a, b, kind, v in entry.get("regions", [])],
                    "anchors": {int(k): decode(v) for k, v in entry.get("anchors", {}).items()},
                    "comparisons": {site: {"op": info["op"], "unknown": decode(info["unknown"]),
                        "states": {state: decode(v) for state, v in info["states"].items()},
                        "exact": {"complete": decode(info["exact"]["complete"]),
                                  "states": {state: decode(v) for state, v in info["exact"]["states"].items()}}
                                 if "exact" in info else None}
                        for site, info in comparison_sites.items()},
                    "readers": readers,
                    "executed": decode(entry.get("executed", "0")),
                    "setup": setup,
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
            # Narrowing probes deferred until every file of the patch is processed.
            pending = []
            for c in changes:
                name = c["old"]
                if name != c["new"]:
                    return include(self.all, "path changed", name)
                old = self.read_source(name)
                new, removed, inserted = apply_change(old, c)
                entry = self.files.get(name)
                if name.endswith(".py"):
                    before, after = baseline_tree(old), ast.parse(new, type_comments=True)
                    # Compare every file: a harmless-looking comment may accompany a fixture edit.
                    changes_at_sites = classify_python_edit(before, after)
                    if changes_at_sites == []:
                        # Tests may inspect comments or exact source text.
                        if entry is None:
                            return include(self.all, "unmapped source", name)
                        result |= include(entry["readers"], "file reader", name)
                        continue
                    if self.conservative_python:
                        return include(self.all, "conservative executable Python selection", name)
                    result |= include(self.child_work, "potential untraced child work", name)
                    affected_comparisons = []
                    refined = None
                    if self.relations:
                        contexts = comparison_contexts(before)
                        affected = sorted(removed | inserted)
                        for start, end, deferred in contexts.values():
                            index = bisect_left(affected, start)
                            if index < len(affected) and affected[index] <= end:
                                if not deferred:
                                    return include(self.all, "comparison in import or shared setup", name)
                                if start < end:
                                    affected_comparisons.append((start, end))
                        if changes_at_sites and len(changes) == 1 and len(changes_at_sites) == 1:
                            refined = self.refine_comparisons(changes_at_sites, entry, contexts)
                    if refined is not None:
                        result |= include(refined, "comparison boundary or unknown", name)
                        result |= include(entry["readers"], "file reader", name)
                        result |= include(self.external_for(name), "external dependency", name)
                        continue
                    hazard = import_hazard_kind(old, new)
                    if hazard == "initializer" and self.import_succeeds(name, changes):
                        hazard = None
                    if hazard:
                        return include(self.all, "import-time change", name)
                result |= include(self.external_for(name), "external dependency", name)
                if entry is None:
                    return include(self.all, "unmapped file", name)
                if not name.endswith(".py"):
                    setup = entry.get("setup") or {}
                    readers = entry["readers"]
                    checked_class = None
                    if (self.executor_fixtures and len(changes) == 1 and setup
                            and name.startswith("tests/fixtures/") and name.endswith(".sql")
                            and plain_text_change(patch)):
                        from executor_fixtures import CLASS, fixture_effect
                        if CLASS in setup and self.schedule_admission:
                            checked_class = CLASS
                            # The class's tests stay until the deferred loader check runs.
                            pending.append(self.fixture_candidate(name, new, readers & setup[CLASS], setup[CLASS]))
                            readers &= ~setup[CLASS]
                        elif CLASS in setup:
                            effect = fixture_effect(self.source, name, new)
                            if effect is not None:
                                # Both pinned setup paths only materialize this loader. A
                                # successful parse proves setup for this class on this edit.
                                checked_class = CLASS
                                item, identical = effect
                                if identical:
                                    readers &= ~self.test_bits.get(item, 0)
                    result |= include(readers, "file reader", name)
                    for class_id, members in sorted(setup.items()):
                        if class_id == checked_class:
                            continue
                        if self.schedule_admission:
                            pending.append(self.setup_candidate(class_id, members, entry["readers"], changes))
                            continue
                        result |= include(self.setup_fallback(class_id, members, entry["readers"], changes),
                                          "class setup reader", name, class_id)
                    # Unknown readers are uncertainty, not proof of irrelevance.
                    if not entry["readers"] and not setup:
                        return include(self.all, "unknown file readers", name)
                    continue
                result |= include(entry["readers"], "file reader", name)
                # Operator-only lines may lack their own coverage, including
                # in chains and mixed edits without usable comparison evidence.
                for start, end in affected_comparisons:
                    result |= include(self.span_users(entry, start, end), "comparison operand coverage", name,
                                      (start, end))
                # An operator-only edit (classify_python_edit returned a list) leaves
                # every and/or expression's shape unchanged, so it has no such spans.
                short_circuit = short_circuit_edits(before, after, removed | inserted) if changes_at_sites is None else ()
                for start, end in sorted(set(short_circuit)):
                    result |= include(self.span_users(entry, start, end),
                                      "short-circuit evaluation change", name, (start, end))
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
            if pending:
                result = self.resolve(result, pending, include)
            if (self.lazy_test_budget >= 0.7 and result and result == self.closure_external
                    and result.bit_count() == 1 and len(changes) == 1 and not self.conservative_python
                    and name.startswith("sqlglot/") and name.endswith(".py") and plain_text_change(patch)):
                from isolated_pytest import cpu_seconds, passed
                item, = self.chosen(result)
                if self.schedule_admission and not self.admit("lazy", "lazy", schedule_seconds([item])):
                    return result
                start = cpu_seconds()
                # A single scheduled worker costs two seconds plus the test. Limit
                # wall time per launch and charge actual CPU even when verification fails.
                deadline = min(2 * self.lazy_test_budget, 2 + item["seconds"])
                verified = passed(self.source, name, new, item["id"], deadline)
                self.record("lazy", "lazy", cpu_seconds() - start)
                self.lazy_test_budget -= cpu_seconds() - start
                if verified:
                    return 0
            if (self.executor_plans and self.executor_plan_budget >= 1 and len(changes) == 1
                    and name.startswith("sqlglot/optimizer/") and name.endswith(".py")
                    and not self.conservative_python and not inserted and len(removed) == 1 and plain_text_change(patch)):
                result = self.check_executor_plan(result, name, old, new, next(iter(removed)))
            return result
        except (ValueError, SyntaxError, OSError, UnicodeError, RecursionError) as error:
            # Syntax/import-shape uncertainty can break every test's collection.
            return include(self.all, "analysis failed: " + type(error).__name__)

    def check_executor_plan(self, mask, name, before, after, line):
        from executor_plans import ITEM, cpu_seconds, matched
        target = self.test_bits.get(ITEM, 0)
        if not mask & target or not self.executor_plans.is_dir():
            return mask
        start = cpu_seconds()
        try:
            chosen = self.chosen(mask)
            durations = {t["number"]: round(t["seconds"] * 1000) for t in chosen}
            def schedule_cost(tests):
                workers = split(tests)
                return 2000 * len(workers) + max((sum(durations[n] for n in w) for w in workers), default=0)

            without = [t for t in chosen if t["id"] != ITEM]
            benefit = (schedule_cost(chosen) - schedule_cost(without)) / 1000
            if self.schedule_admission:
                if not self.admit("plan", "plan", benefit):
                    return mask
                # Never spend more than the CPU at which the replay still pays.
                allowance = min(self.executor_plan_budget, break_even_cpu(benefit)) - (cpu_seconds() - start)
            else:
                # The audit may spend at most half the benefit on an individual edit.
                allowance = min(self.executor_plan_budget, benefit / 2) - (cpu_seconds() - start)
            if allowance >= 1:
                replay = cpu_seconds()
                ok = matched(self.source.resolve(), name, before, after, line, self.executor_plans, allowance)
                self.record("plan", "plan", cpu_seconds() - replay)
                if ok:
                    return mask & ~target
            return mask
        finally:
            self.executor_plan_budget -= cpu_seconds() - start

    def external_for(self, name):
        if self.import_closure is None or name in self.import_closure:
            return self.external
        return self.external & ~self.closure_external

    def expected_cpu(self, kind, key):
        measured = self.measured_cpu.get((kind, key)) or self.measured_cpu.get(kind)
        return sum(measured) / len(measured) if measured else PRIOR_CPU[kind]

    def record(self, kind, key, cpu):
        self.measured_cpu.setdefault((kind, key), []).append(cpu)
        if key != kind:
            self.measured_cpu.setdefault(kind, []).append(cpu)

    def admit(self, kind, key, saving):
        return pays_for_itself(saving, self.expected_cpu(kind, key))

    def resolve(self, result, pending, include):
        """Run deferred probes that pay for themselves against the patch's final selection.

        Each candidate is (kind, key, keep, run): `keep` is kept unless run() is
        admitted, and run() returns the subset of `keep` that must stay.
        """
        kept = result
        for index, (kind, key, keep, run) in enumerate(pending):
            rest = kept
            for _, _, other, _ in pending[index + 1:]:
                rest |= other
            droppable = keep & ~rest
            if droppable and self.admit(kind, key, schedule_saving(self.chosen(rest | keep), droppable)):
                kept |= include(run() & keep, f"{kind} probe kept", None, key)
            else:
                kept |= include(keep, f"{kind} probe not admitted", None, key)
        return kept

    def setup_candidate(self, class_id, members, readers, changes):
        def run():
            return self.setup_fallback(class_id, members, readers, changes, admitted=True)
        return "setup", class_id, members, run

    def fixture_candidate(self, name, new, readers, members):
        def run():
            from executor_fixtures import fixture_effect
            from isolated_pytest import cpu_seconds
            start = cpu_seconds()
            effect = fixture_effect(self.source, name, new)
            self.record("fixture", "fixture", cpu_seconds() - start)
            if effect is None:
                # The pinned setup's loader did not succeed: keep the whole class.
                return members
            item, identical = effect
            return readers & ~self.test_bits.get(item, 0) if identical else readers
        return "fixture", "fixture", members, run

    def setup_fallback(self, class_id, members, readers, changes, *, admitted=False):
        """Tests of a class to keep beyond attributed readers: none if its setUpClass verifiably succeeds."""
        dropped = members & ~readers
        if not dropped:
            return 0
        if self.setup_check_budget < 1.0:
            return members
        if not admitted and sum(t["seconds"] for t in self.chosen(dropped)) < 3.0:
            return members
        module_path, class_name = class_id.split("::")[0], class_id.split("::")[-1]
        if not module_path.endswith(".py"):
            return members
        overlays = {}
        for c in changes:
            if c["old"] != c["new"]:
                return members
            overlays[c["old"]] = apply_change(self.read_source(c["old"]), c)[0]
        from native_probes import verify_setup
        ok, cpu = verify_setup(self.source, overlays, module_path[:-3].replace("/", "."), class_name,
                               wall_seconds=12.0, cpu_seconds=min(6.0, self.setup_check_budget))
        self.record("setup", class_id, cpu)
        self.setup_check_budget -= cpu
        return 0 if ok else members

    def import_succeeds(self, name, changes):
        module = module_for(name)
        if module is None or self.import_check_budget < .1:
            return False
        overlays = {}
        for c in changes:
            if c["old"] != c["new"]:
                return False
            overlays[c["old"]] = apply_change(self.read_source(c["old"]), c)[0]
        from native_probes import verify_import
        ok, cpu = verify_import(self.source, overlays, module,
                                wall_seconds=4.0, cpu_seconds=min(2.0, self.import_check_budget))
        self.import_check_budget -= cpu
        return ok

    @staticmethod
    def span_users(entry, start, end):
        users = 0
        for line in range(start, end + 1):
            users |= entry["lines"].get(line, entry["anchors"].get(line, 0))
        return users

    def refine_comparisons(self, changes, entry, contexts):
        if not changes or not entry or not entry["comparisons"]:
            return None
        result = 0
        for site, before, after in changes:
            info = entry["comparisons"].get(site)
            if info is None or info["op"] != before or site not in contexts or not contexts[site][2]:
                return None
            # Unlike identity, ordering can call user code and raise before
            # the map observer records anything. An apparently safe hit does
            # not rule out an earlier unrecorded failure in the same test.
            if self.strict_relations and before not in ("Is", "IsNot"):
                exact = self.exact_comparison(info, before, after, contexts[site], entry)
                if exact is None:
                    return None
                result |= exact
                continue
            observed = unknown = info["unknown"]
            for state, users in info["states"].items():
                observed |= users
                a, b = relation_outcome(state, before), relation_outcome(state, after)
                if a is None or b is None or a != b:
                    unknown |= users
            start, end, _ = contexts[site]
            result |= unknown | (self.span_users(entry, start, end) & ~observed)
        return result

    def exact_comparison(self, info, before, after, context, entry):
        """Tests to keep for a value-operator swap, or None without exact evidence.

        A test may be dropped only if the map proves, for every evaluation of
        the site by that test, that both operands completed, were built-in
        int, str or None, the original operator completed, and the original
        and replacement operators give the same outcome.
        """
        exact = info.get("exact")
        if not self.exact_comparisons or exact is None:
            return None
        if before not in EXACT_OPERATORS or after not in EXACT_OPERATORS:
            return None
        start, end, _ = context
        touched = self.span_users(entry, start, end) | info["unknown"] | exact["complete"]
        for users in info["states"].values():
            touched |= users
        evidenced = changed = 0
        for state, users in exact["states"].items():
            touched |= users
            evidenced |= users
            a, b = relation_outcome(state, before), relation_outcome(state, after)
            if a is None or b is None or a != b:
                changed |= users
        safe = exact["complete"] & evidenced & ~changed & ~info["unknown"]
        return touched & ~safe

    def probe(self, patch, mask, budget, *, cost_aware=False):
        tests = self.chosen(mask)
        if not tests or budget < .1 or not self.native or mask & (self.external | self.child_work):
            return mask, 0.0
        if any(t["id"] not in self.native for t in tests):
            return mask, 0.0
        if cost_aware:
            if not math.isfinite(budget) or any(not math.isfinite(t["seconds"]) or t["seconds"] < 0 for t in tests):
                return mask, 0.0
            durations = {t["number"]: round(t["seconds"] * 1000) for t in tests}
            total = sum(durations.values()) / 1000
            # Reserve the full deadline, even after a quick pass. Admission then
            # depends on catalog durations and patch order, not on measured CPU.
            cpu_seconds = 1.1 + 2 * total
            if cpu_seconds > min(2.1, budget):
                return mask, 0.0
            workers = split(tests)
            saving = 2 * len(workers) + max(sum(durations[n] for n in w) for w in workers) / 1000
            expected_cpu = .55 + 1.25 * total
            # Only an all-pass batch saves a worker. Discount the upper
            # bound for each additional test and require a margin over CPU.
            expected_saving = saving * .8 ** len(tests)
            if (not pays_for_itself(expected_saving, expected_cpu) if self.schedule_admission
                    else expected_saving <= 1.25 * expected_cpu):
                return mask, 0.0
            wall_seconds = 3 * cpu_seconds
        else:
            if any(t["seconds"] > .15 for t in tests) or sum(t["seconds"] for t in tests) > .8:
                return mask, 0.0
            wall_seconds, cpu_seconds = min(1.5, budget + .1), min(1.0, budget)
            # Even if every test passes, the drop must pay for the CPU limit.
            if self.schedule_admission and not pays_for_itself(schedule_saving(tests, mask), cpu_seconds):
                return mask, 0.0
        changes = parse_patch(patch)
        if any(not c["old"].endswith(".py") or c["old"].startswith("tests/") or c["old"] != c["new"] for c in changes):
            return mask, 0.0
        if cost_aware and (any(module_for(c["old"]) is None for c in changes)
                           or len({c["old"] for c in changes}) != len(changes)):
            return mask, 0.0
        if cost_aware and any(mask & self.files.get(c["old"], {}).get("readers", 0) for c in changes):
            # Code loaded by Python sees the overlay. Tests that read source
            # directly can use I/O paths the import overlay does not wrap.
            return mask, 0.0
        overlays = {c["old"]: apply_change(self.read_source(c["old"]), c)[0] for c in changes}
        from native_probes import verify
        passed, cpu = verify(self.source, overlays, tests, wall_seconds=wall_seconds, cpu_seconds=cpu_seconds,
                             strict_cpu=cost_aware)
        if cost_aware:
            all_passed = set(passed) == {t["id"] for t in tests}
            return (0 if all_passed else mask), cpu_seconds
        for test in tests:
            if test["id"] in passed:
                mask &= ~(1 << test["number"])
        return mask, cpu

    def chosen(self, mask):
        return [t for t in self.tests if mask & (1 << t["number"])]


def parse_args(argv=None):
    """Parse flags and fill unset settings from the config file; args.settings holds the result."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=Path("/data"))
    parser.add_argument("--source", type=Path, default=Path("/opt/sqlglot"))
    parser.add_argument("--map", type=Path, default=Path("/app/dependencies.json.gz"))
    parser.add_argument("--probe-budget", type=float)
    parser.add_argument("--cost-aware-probes", action=argparse.BooleanOptionalAction, default=None,
                        help="Reserve probe budget for whole selections with sufficient schedule savings")
    python_mode = parser.add_mutually_exclusive_group()
    python_mode.add_argument("--conservative-python", action=argparse.BooleanOptionalAction, default=None)
    python_mode.add_argument("--child-work", action=argparse.BooleanOptionalAction, default=None,
                             help="Use targeted selection plus potential untraced child work")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--relations", dest="relations", action="store_true")
    group.add_argument("--no-relations", dest="relations", action="store_false")
    parser.set_defaults(relations=None)
    parser.add_argument("--import-closure", type=Path, default=Path("/app/import-closure.json"))
    parser.add_argument("--import-check-budget", type=float)
    parser.add_argument("--setup-check-budget", type=float)
    parser.add_argument("--executor-fixtures", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--executor-plans", type=Path, default=Path("/app/executor-plans"))
    parser.add_argument("--executor-plan-budget", type=float)
    parser.add_argument("--lazy-test-budget", type=float)
    parser.add_argument("--schedule-admission", action=argparse.BooleanOptionalAction, default=None,
                        help="Admit narrowing probes only when their schedule saving pays for their CPU")
    parser.add_argument("--exact-comparisons", action=argparse.BooleanOptionalAction, default=None,
                        help="Narrow value-operator swaps with exact comparison evidence from the map")
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("selector-config.json"))
    args = parser.parse_args(argv)
    try:
        config = json.loads(args.config.read_text())
    except (OSError, ValueError):
        config = {}
    probe_settings = config.get("probe_budget", 8.0)
    probe_seconds = probe_settings.get("seconds", 0.0) if isinstance(probe_settings, dict) else probe_settings
    if args.cost_aware_probes is None:
        # An explicit numeric flag reproduces existing benchmark configurations.
        args.cost_aware_probes = (args.probe_budget is None and isinstance(probe_settings, dict)
                                  and probe_settings.get("cost_aware") is True)
    if args.probe_budget is None:
        args.probe_budget = float(probe_seconds)
    if args.relations is None:
        args.relations = bool(config.get("relations", True))
    conservative = args.conservative_python
    child_work = args.child_work
    if conservative is None:
        conservative = not child_work and config.get("conservative_python", False)
    if child_work is None:
        child_work = args.conservative_python is None and not conservative and config.get("child_work", False)
    if args.import_check_budget is None:
        args.import_check_budget = float(config.get("import_check_budget", 0.0))
    if args.setup_check_budget is None:
        args.setup_check_budget = float(config.get("setup_check_budget", 0.0))
    if args.executor_fixtures is None:
        args.executor_fixtures = config.get("executor_fixtures", False)
    if args.executor_plan_budget is None:
        args.executor_plan_budget = float(config.get("executor_plan_budget", 0.0))
    if args.lazy_test_budget is None:
        args.lazy_test_budget = float(config.get("lazy_test_budget", 0.0))
    if args.schedule_admission is None:
        args.schedule_admission = config.get("schedule_admission", False)
    if args.exact_comparisons is None:
        args.exact_comparisons = config.get("exact_comparisons", False)
    args.settings = {"lazy_test_budget": args.lazy_test_budget, "executor_fixtures": bool(args.executor_fixtures), "executor_plan_budget": args.executor_plan_budget,"conservative_python": bool(conservative), "child_work": bool(child_work),
                     "relations": bool(args.relations), "probe_budget": float(args.probe_budget),
                     "import_check_budget": float(args.import_check_budget),
                     "setup_check_budget": float(args.setup_check_budget)}
    args.settings["schedule_admission"] = bool(args.schedule_admission)
    args.settings["exact_comparisons"] = bool(args.exact_comparisons)
    if args.cost_aware_probes:
        args.settings["probe_budget"] = {"seconds": args.probe_budget, "cost_aware": True}
    return args


def resolve_settings(argv=None):
    return parse_args(argv).settings


def main():
    args = parse_args()
    settings = args.settings
    tests = json.loads((args.data / "tests.json").read_text())["tests"]
    try:
        closure = json.loads(args.import_closure.read_text())
        if closure.get("revision") != REVISION:
            closure = None
    except (OSError, ValueError, AttributeError):
        closure = None
    selector = Selector(tests, args.source, args.map, conservative_python=settings["conservative_python"],
                        relations=settings["relations"], child_work=settings["child_work"], import_closure=closure,
                        import_check_budget=max(0.0, settings["import_check_budget"]),
                        setup_check_budget=max(0.0, settings["setup_check_budget"]),
                        executor_fixtures=settings["executor_fixtures"], executor_plans=args.executor_plans,
                        executor_plan_budget=max(0.0, settings["executor_plan_budget"]),
                        lazy_test_budget=max(0.0, settings["lazy_test_budget"]),
                        schedule_admission=settings["schedule_admission"],
                        exact_comparisons=settings["exact_comparisons"])
    schedules = {}
    remaining = max(0.0, args.probe_budget)
    for patch in sorted((args.data / "commits").glob("*.patch")):
        text = patch.read_text(encoding="utf-8")
        mask = selector.select(text)
        if selector.complete and remaining > 0:
            try:
                mask, charged = selector.probe(text, mask, remaining, cost_aware=args.cost_aware_probes)
                remaining -= charged
            except (OSError, ValueError, SyntaxError):
                pass
        if mask not in schedules:
            schedules[mask] = split(selector.chosen(mask))
        print(json.dumps({"commit": patch.stem, "workers": schedules[mask]}, separators=(",", ":")))


if __name__ == "__main__":
    main()
