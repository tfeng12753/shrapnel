"""Generate held-out single-edit mutants of the pinned checkout (evaluation only).

The sample patches look machine-made: one-token operator, boolean, or number
edits in the pinned library, corrupted lines in SQL fixtures, comment-only edits, and a few
multi-file combinations. This writes fresh patches of the same kinds so selector
changes can be checked against data they were not tuned on. Lines touched by the
sample patches are excluded.

Nothing here is copied into the selector image, and no output of this tool may
be used as selector input: it exists only to measure misses and cost.

    python3 tools/mutants.py --source /path/to/pinned --out mutants --count 40 --seed 1
    python3 tools/mutants.py --source /path/to/pinned --out mutants --count 40 --seed 1 \
        --families setting-flip,comparison-swap,fixture-entry

Families (recorded as "family" in manifest.json):
  code, fixture, comment, multi   the original generators; with no --families the
                                  original 16/8/3/3 mix is drawn, so old seeds
                                  reproduce byte-identical patches
  setting-flip      flip an UPPER_CASE bool class attribute in dialect, parser,
                    tokenizer or generator classes
  typeddict-total   flip (or add) total= on a TypedDict class
  dict-value        change one value of a dict literal assigned in a class body
  comparison-swap   swap one comparison operator; len(...) == N also gets N +/- 1
  fixture-entry     change one token in one entry of the TPC optimizer fixtures,
                    recording the entry index and which half (input/expected)
"""
import argparse
import ast
import difflib
import json
from pathlib import Path
import random
import re
import sys

# (pattern, replacement) applied to one occurrence on one source line.
OPERATORS = [
    (r" and ", " or "), (r" or ", " and "),
    (r" == ", " != "), (r" != ", " == "),
    (r" is not ", " is "), (r" is (?!not )", " == "),
    (r" < ", " <= "), (r" <= ", " < "), (r" > ", " >= "), (r" >= ", " > "),
    (r"\bTrue\b", "False"), (r"\bFalse\b", "True"),
    (r"\bif not ", "if "), (r"\bnot (?=[a-z_(])", ""),
]
NUMBER = re.compile(r"(?<![\w.])(\d+)(?![\w.])")
FIXTURE_TOKENS = ["42", "0", "NULL", "x"]


def diff(name, old, new):
    return "diff --git a/{0} b/{0}\n".format(name) + "".join(
        difflib.unified_diff(old.splitlines(True), new.splitlines(True), "a/" + name, "b/" + name))


def sample_lines(sample):
    """(file, line) pairs the sample patches touch, so mutants stay independent."""
    touched = set()
    for patch in Path(sample, "commits").glob("*.patch"):
        name, line = None, 0
        for row in patch.read_text().splitlines():
            if row.startswith("--- a/"):
                name = row[6:]
            elif row.startswith("@@"):
                line = int(re.match(r"@@ -(\d+)", row)[1]) - 1
            elif name and row[:1] in " -":
                line += 1
                touched.add((name, line))
    return touched


def code_mutation(rng, root, files, touched):
    for _ in range(500):
        name = rng.choice(files)
        text = (root / name).read_text()
        lines = text.splitlines(True)
        index = rng.randrange(len(lines))
        line = lines[index]
        stripped = line.strip()
        if (name, index + 1) in touched or not stripped or stripped.startswith(("#", '"', "'")):
            continue
        options = [(p, r) for p, r in OPERATORS if re.search(p, line)]
        if NUMBER.search(line):
            options.append((None, None))
        if not options:
            continue
        pattern, repl = rng.choice(options)
        if pattern is None:
            matches = list(NUMBER.finditer(line))
            m = rng.choice(matches)
            value = int(m[1])
            new_line = line[:m.start()] + str(value + rng.choice([1, -1] if value else [1])) + line[m.end():]
            kind = "number"
        else:
            matches = list(re.finditer(pattern, line))
            m = rng.choice(matches)
            new_line = line[:m.start()] + repl + line[m.end():]
            kind = "operator"
        new = "".join(lines[:index] + [new_line] + lines[index + 1:])
        try:
            if ast.dump(ast.parse(text)) == ast.dump(ast.parse(new)):
                continue
        except SyntaxError:
            continue
        return name, text, new, kind
    raise RuntimeError("no code mutation found")


def comment_mutation(rng, root, files, touched):
    for _ in range(500):
        name = rng.choice(files)
        text = (root / name).read_text()
        lines = text.splitlines(True)
        index = rng.randrange(len(lines))
        line = lines[index]
        if (name, index + 1) in touched or not line.strip() or line.strip().startswith(("#", '"', "'", ")", "]", "}")):
            continue
        indent = re.match(r"[ \t]*", line)[0]
        new = "".join(lines[:index] + [indent + "# Reviewed for clarity\n"] + lines[index:])
        try:
            if ast.dump(ast.parse(text)) != ast.dump(ast.parse(new)):
                continue
        except SyntaxError:
            continue
        return name, text, new, "comment"
    raise RuntimeError("no comment mutation found")


def fixture_mutation(rng, root, fixtures, touched):
    for _ in range(500):
        name = rng.choice(fixtures)
        text = (root / name).read_text()
        lines = text.splitlines(True)
        index = rng.randrange(len(lines))
        line = lines[index]
        if (name, index + 1) in touched or not line.strip() or line.lstrip().startswith(("#", "--")):
            continue
        words = line.rstrip("\n").split(" ")
        if len(words) < 2:
            continue
        position = rng.randrange(1, len(words))
        if rng.random() < .5:
            words.insert(position, rng.choice(FIXTURE_TOKENS))
        else:
            del words[position]
        new_line = " ".join(words) + ("\n" if line.endswith("\n") else "")
        if new_line == line:
            continue
        return name, text, "".join(lines[:index] + [new_line] + lines[index + 1:]), "fixture"
    raise RuntimeError("no fixture mutation found")


# --- Targeted families. Each candidate is an exact source span plus replacement
# options; spans on lines the sample patches touch are never offered.

# Dialect classes plus the per-dialect parser/generator modules split out of them.
SETTING_FILES = re.compile(r"^sqlglot/((dialects|parsers|generators)/[^/]+|parser|tokens|tokenizer_core|generator)\.py$")
UPPER = re.compile(r"^[A-Z][A-Z0-9_]*$")
TPC_FIXTURES = ("tests/fixtures/optimizer/tpc-h/tpc-h.sql", "tests/fixtures/optimizer/tpc-ds/tpc-ds.sql")
SWAP = {ast.Eq: "!=", ast.NotEq: "==", ast.Lt: "<=", ast.LtE: "<", ast.Gt: ">=", ast.GtE: ">",
        ast.Is: "is not", ast.IsNot: "is", ast.In: "not in", ast.NotIn: "in"}
OP_PATTERN = {ast.Eq: r"==", ast.NotEq: r"!=", ast.Lt: r"<(?!=)", ast.LtE: r"<=", ast.Gt: r">(?!=)",
              ast.GtE: r">=", ast.Is: r"\bis\b(?!\s+not\b)", ast.IsNot: r"\bis\s+not\b",
              ast.In: r"(?<!not )\bin\b", ast.NotIn: r"\bnot\s+in\b"}
SQL_OPERATORS = {"=": ["<>"], "<>": ["="], "!=": ["="], "<": ["<=", ">"], "<=": ["<"], ">": [">=", "<"], ">=": [">"]}
SQL_TOKEN = re.compile(r'(?P<num>(?<![\w.])\d+(?![\w]))|(?P<op><>|!=|<=|>=|=|<|>)'
                       r'|(?P<quoted>"[A-Za-z_][A-Za-z0-9_]*")|(?P<ident>\b[a-z]+_[a-z0-9_]+\b)')


class Candidate:
    """Replace text[start:end] (absolute offsets) by one of options."""

    def __init__(self, name, first, last, start, end, options, edit_kind, detail):
        self.name, self.first, self.last, self.start, self.end = name, first, last, start, end
        self.options, self.edit_kind, self.detail = options, edit_kind, detail


class Context:
    def __init__(self, root, files, touched):
        self.root, self.files, self.touched = root, files, touched
        self._text, self._tree, self._starts, self._candidates = {}, {}, {}, {}
        self.used = set()

    def text(self, name):
        if name not in self._text:
            self._text[name] = (self.root / name).read_text()
        return self._text[name]

    def tree(self, name):
        if name not in self._tree:
            try:
                self._tree[name] = ast.parse(self.text(name))
            except SyntaxError:
                self._tree[name] = None
        return self._tree[name]

    def offset(self, name, lineno, col):
        """Absolute character offset of an AST (lineno, utf-8 byte col) position."""
        if name not in self._starts:
            starts, total = [], 0
            for line in self.text(name).splitlines(True):
                starts.append(total)
                total += len(line)
            self._starts[name] = (starts, self.text(name).splitlines(True))
        starts, lines = self._starts[name]
        return starts[lineno - 1] + len(lines[lineno - 1].encode()[:col].decode(errors="ignore"))

    def span(self, name, node):
        return self.offset(name, node.lineno, node.col_offset), self.offset(name, node.end_lineno, node.end_col_offset)

    def clear(self, name, first, last):
        return not any((name, n) in self.touched for n in range(first, last + 1))

    def node_candidate(self, name, node, options, edit_kind, detail):
        if not options or not self.clear(name, node.lineno, node.end_lineno):
            return None
        start, end = self.span(name, node)
        return Candidate(name, node.lineno, node.end_lineno, start, end, options, edit_kind, detail)

    def candidates(self, family):
        if family not in self._candidates:
            self._candidates[family] = [c for c in FAMILY_CANDIDATES[family](self) if c]
        return self._candidates[family]


def class_assignments(tree):
    """(class, target name, value) for simple assignments directly in a class body."""
    for cls in ast.walk(tree):
        if not isinstance(cls, ast.ClassDef):
            continue
        for stmt in cls.body:
            if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                yield cls, stmt.targets[0].id, stmt.value
            elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.value is not None:
                yield cls, stmt.target.id, stmt.value


def setting_flip_candidates(ctx):
    for name in ctx.files:
        if not SETTING_FILES.match(name) or ctx.tree(name) is None:
            continue
        for cls, target, value in class_assignments(ctx.tree(name)):
            if UPPER.match(target) and isinstance(value, ast.Constant) and isinstance(value.value, bool):
                yield ctx.node_candidate(name, value, [str(not value.value)], "setting-flip",
                                         {"class": cls.name, "setting": target, "line": value.lineno})


def typeddict_total_candidates(ctx):
    for name in ctx.files:
        tree = ctx.tree(name)
        if tree is None:
            continue
        typed = set()
        for cls in ast.walk(tree):
            if not isinstance(cls, ast.ClassDef):
                continue
            bases = {b.id if isinstance(b, ast.Name) else b.attr if isinstance(b, ast.Attribute) else None
                     for b in cls.bases}
            if not bases & ({"TypedDict"} | typed):
                continue
            typed.add(cls.name)
            detail = {"class": cls.name, "line": cls.lineno}
            total = next((k for k in cls.keywords if k.arg == "total"), None)
            if total is not None:
                if isinstance(total.value, ast.Constant) and isinstance(total.value.value, bool):
                    yield ctx.node_candidate(name, total.value, [str(not total.value.value)], "typeddict-total", detail)
            elif cls.bases or cls.keywords:
                last = max(cls.bases + [k.value for k in cls.keywords], key=lambda n: (n.end_lineno, n.end_col_offset))
                if ctx.clear(name, last.end_lineno, last.end_lineno):
                    end = ctx.offset(name, last.end_lineno, last.end_col_offset)
                    yield Candidate(name, last.end_lineno, last.end_lineno, end, end, [", total=False"],
                                    "typeddict-total", {**detail, "added": True})


def value_kind(node):
    if isinstance(node, ast.Constant):
        return type(node.value).__name__
    return type(node).__name__


def dict_value_candidates(ctx):
    for name in ctx.files:
        tree = ctx.tree(name)
        if tree is None:
            continue
        text = ctx.text(name)
        for cls, target, value in class_assignments(tree):
            if not isinstance(value, ast.Dict):
                continue
            entries = [(k, v) for k, v in zip(value.keys, value.values)
                       if k is not None and v.lineno == v.end_lineno]
            sources = {}
            for _, v in entries:
                start, end = ctx.span(name, v)
                sources.setdefault(value_kind(v), set()).add(text[start:end])
            for k, v in entries:
                start, end = ctx.span(name, v)
                source = text[start:end]
                if isinstance(v, ast.Constant) and isinstance(v.value, bool):
                    options = [str(not v.value)]
                elif isinstance(v, ast.Constant) and isinstance(v.value, int):
                    options = [str(v.value + 1)]
                else:
                    options = sorted(sources.get(value_kind(v), set()) - {source})
                key = ast.get_source_segment(text, k) or ""
                yield ctx.node_candidate(name, v, options, "dict-value",
                                         {"class": cls.name, "setting": target, "key": key[:80], "line": v.lineno})


def is_len_int(node, index):
    """True if comparison `index` of node is len(...) <op> int (either side)."""
    left = node.left if index == 0 else node.comparators[index - 1]
    right = node.comparators[index]

    def is_len(n):
        return isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "len"

    def is_int(n):
        return isinstance(n, ast.Constant) and type(n.value) is int

    return (is_len(left) and is_int(right)) or (is_int(left) and is_len(right))


def comparison_candidates(ctx):
    for name in ctx.files:
        tree = ctx.tree(name)
        if tree is None:
            continue
        text = ctx.text(name)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            for index, op in enumerate(node.ops):
                left = node.left if index == 0 else node.comparators[index - 1]
                right = node.comparators[index]
                gap_start = ctx.offset(name, left.end_lineno, left.end_col_offset)
                gap_end = ctx.offset(name, right.lineno, right.col_offset)
                gap = text[gap_start:gap_end]
                if "#" in gap:
                    continue
                found = list(re.finditer(OP_PATTERN[type(op)], gap))
                if len(found) != 1:
                    continue
                line = text.count("\n", 0, gap_start + found[0].start()) + 1
                if not ctx.clear(name, line, line):
                    continue
                len_int = is_len_int(node, index)
                detail = {"line": line, "op": found[0][0], "len_int": len_int}
                yield Candidate(name, line, line, gap_start + found[0].start(), gap_start + found[0].end(),
                                [SWAP[type(op)]], "len-compare" if len_int else "comparison", detail)
                if len_int:
                    number = right if isinstance(right, ast.Constant) else left
                    yield ctx.node_candidate(name, number, [str(v) for v in (number.value - 1, number.value + 1) if v >= 0],
                                             "len-compare", {"line": number.lineno, "len_int": True,
                                                             "number": number.value})


def fixture_entry_candidates(ctx):
    """Tokens of TPC fixture entries, split exactly like the suite's fixture-pair loader."""
    for name in TPC_FIXTURES:
        if not (ctx.root / name).exists():
            continue
        lines = ctx.text(name).splitlines(True)
        total = sum(line.count(";") for line in lines if line.strip("\n") and not line.startswith("--"))
        # The loader drops a trailing statement with no partner.
        pairs = (total + 1) // 2 if (total + 1) % 2 == 0 else total // 2
        statement, offset = 0, 0
        for lineno, line in enumerate(lines, 1):
            body = line.rstrip("\n")
            if body and not body.startswith("--") and not body.lstrip().startswith("#") and ctx.clear(name, lineno, lineno):
                for m in SQL_TOKEN.finditer(body):
                    here = statement + body.count(";", 0, m.start())
                    if here // 2 >= pairs:
                        continue
                    token, kind = m[0], m.lastgroup
                    if kind == "num":
                        value = int(token)
                        width = len(token) if token.startswith("0") else 1
                        options = [str(v).zfill(width) for v in (value - 1, value + 1) if v >= 0]
                    elif kind == "op":
                        options = SQL_OPERATORS[token]
                    else:
                        same = sorted({t[0] for t in SQL_TOKEN.finditer(body) if t.lastgroup == kind} - {token})
                        options = same or ([token[:-1] + '_x"'] if kind == "quoted" else [token + "_x"])
                    yield Candidate(name, lineno, lineno, offset + m.start(), offset + m.end(), options,
                                    {"num": "number", "op": "operator"}.get(kind, "identifier"),
                                    {"entry": here // 2, "half": "input" if here % 2 == 0 else "expected",
                                     "line": lineno, "token": token})
            if body and not body.startswith("--"):
                statement += body.count(";")
            offset += len(line)


FAMILY_CANDIDATES = {"setting-flip": setting_flip_candidates, "typeddict-total": typeddict_total_candidates,
                     "dict-value": dict_value_candidates, "comparison-swap": comparison_candidates,
                     "fixture-entry": fixture_entry_candidates}


def targeted_mutation(rng, ctx, family, match=None):
    """One edit from a targeted family: (name, old, new, edit_kind, detail)."""
    candidates = ctx.candidates(family)
    if match:
        candidates = [c for c in candidates
                      if re.search(match, f"{c.name} {json.dumps(c.detail)} {ctx.text(c.name)[c.start:c.end]}")]
    if family == "comparison-swap" and rng.random() < 1 / 3:
        # Bias toward len(...) == N checks, which plain operator flips rarely reach.
        candidates = [c for c in candidates if c.detail.get("len_int")] or candidates
    def attempt(c, replacement):
        old = ctx.text(c.name)
        new = old[:c.start] + replacement + old[c.end:]
        if (c.name, c.start, replacement) in ctx.used or new == old:
            return None
        if c.name.endswith(".py"):
            try:
                if ast.dump(ast.parse(old)) == ast.dump(ast.parse(new)):
                    return None
            except SyntaxError:
                return None
        ctx.used.add((c.name, c.start, replacement))
        detail = {**c.detail, "old": old[c.start:c.end][:80], "new": replacement[:80]}
        return c.name, old, new, c.edit_kind, detail

    for _ in range(500 if candidates else 0):
        c = rng.choice(candidates)
        edit = attempt(c, rng.choice(c.options))
        if edit:
            return edit
    # Small or nearly used-up families: scan what is left in a seeded order.
    remaining = [(c, o) for c in candidates for o in c.options if (c.name, c.start, o) not in ctx.used]
    rng.shuffle(remaining)
    for c, replacement in remaining:
        edit = attempt(c, replacement)
        if edit:
            return edit
    raise Exhausted(f"no unused {family} mutation left")


class Exhausted(RuntimeError):
    pass


OLD_FAMILIES = {"code": 16, "fixture": 8, "comment": 3, "multi": 3}
# Weight of each targeted family when mixed with the original ones.
TARGETED_WEIGHT = 4
FAMILIES = list(OLD_FAMILIES) + list(FAMILY_CANDIDATES)
FAMILY_KIND = {"setting-flip": "code", "typeddict-total": "code", "dict-value": "code",
               "comparison-swap": "code", "fixture-entry": "fixture"}


def parse_families(value):
    if not value:
        return list(OLD_FAMILIES)
    names = [n.strip() for n in value.split(",") if n.strip()]
    unknown = [n for n in names if n not in FAMILIES]
    if unknown or not names:
        raise argparse.ArgumentTypeError(f"unknown families {unknown}; choose from {','.join(FAMILIES)}")
    # Canonical order, so the full original set reproduces the default plan exactly.
    return [n for n in FAMILIES if n in names]


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--sample", type=Path, default=Path(__file__).resolve().parents[1] / "sample")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--count", type=int, default=40)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--families", type=parse_families, default=list(OLD_FAMILIES),
                        help=f"comma-separated subset of {','.join(FAMILIES)} (default: the original four)")
    parser.add_argument("--match", help="regex: only targeted-family candidates whose file, detail or text match")
    args = parser.parse_args(argv)
    root = args.source.resolve()
    rng = random.Random(args.seed)
    files = sorted(p.relative_to(root).as_posix() for p in (root / "sqlglot").rglob("*.py") if p.stat().st_size)
    fixtures = sorted(p.relative_to(root).as_posix() for p in (root / "tests/fixtures").rglob("*.sql") if p.stat().st_size)
    touched = sample_lines(args.sample)
    ctx = Context(root, files, touched)
    # Roughly the public mix: 16 code, 8 fixture, 3 comment, 3 multi per 30.
    weights = [OLD_FAMILIES.get(f, TARGETED_WEIGHT) for f in args.families]
    plan = rng.choices(args.families, weights=weights, k=args.count)
    commits = args.out / "commits"
    commits.mkdir(parents=True, exist_ok=True)
    manifest, exhausted = {}, set()
    for i, family in enumerate(plan):
        details = None
        if family == "code":
            edits = [code_mutation(rng, root, files, touched)]
        elif family == "fixture":
            edits = [fixture_mutation(rng, root, fixtures, touched)]
        elif family == "comment":
            edits = [comment_mutation(rng, root, files, touched)]
        elif family == "multi":
            makers = [lambda: code_mutation(rng, root, files, touched), lambda: fixture_mutation(rng, root, fixtures, touched),
                      lambda: comment_mutation(rng, root, files, touched)]
            edits, names = [], set()
            for make in rng.sample(makers, k=rng.choice([2, 3])):
                edit = make()
                if edit[0] not in names:
                    names.add(edit[0])
                    edits.append(edit)
        else:
            while True:
                try:
                    name, old, new, edit_kind, detail = targeted_mutation(rng, ctx, family, args.match)
                    break
                except Exhausted as error:
                    exhausted.add(family)
                    left = [f for f in args.families if f in FAMILY_CANDIDATES and f not in exhausted]
                    if not left:
                        print(f"{error}; every selected targeted family is used up, stopping at {i}", file=sys.stderr)
                        family = None
                        break
                    print(f"{error}; using another family for {i}", file=sys.stderr)
                    family = rng.choice(left)
            if family is None:
                break
            edits, details = [(name, old, new, edit_kind)], [detail]
        commit = f"m{args.seed:02d}{i:03d}"
        (commits / f"{commit}.patch").write_text("".join(diff(name, old, new) for name, old, new, _ in edits))
        manifest[commit] = {"kind": FAMILY_KIND.get(family, family), "family": family,
                            "edits": [[name, edit_kind] for name, _, _, edit_kind in edits]}
        if details:
            manifest[commit]["details"] = details
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(f"wrote {len(manifest)} mutants to {commits}")


if __name__ == "__main__":
    main()
