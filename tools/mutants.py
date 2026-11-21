"""Generate held-out single-edit mutants of the pinned sqlglot checkout (evaluation only).

The sample patches look machine-made: one-token operator, boolean, or number
edits in sqlglot, corrupted lines in SQL fixtures, comment-only edits, and a few
multi-file combinations. This writes fresh patches of the same kinds so selector
changes can be checked against data they were not tuned on. Lines touched by the
sample patches are excluded.

Nothing here is copied into the selector image, and no output of this tool may
be used as selector input: it exists only to measure misses and cost.

    python3 tools/mutants.py --source /path/to/pinned/sqlglot --out mutants --count 40 --seed 1
"""
import argparse
import ast
import difflib
import json
from pathlib import Path
import random
import re

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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--sample", type=Path, default=Path(__file__).resolve().parents[1] / "sample")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--count", type=int, default=40)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()
    root = args.source.resolve()
    rng = random.Random(args.seed)
    files = sorted(p.relative_to(root).as_posix() for p in (root / "sqlglot").rglob("*.py") if p.stat().st_size)
    fixtures = sorted(p.relative_to(root).as_posix() for p in (root / "tests/fixtures").rglob("*.sql") if p.stat().st_size)
    touched = sample_lines(args.sample)
    # Roughly the public mix: 16 code, 8 fixture, 3 comment, 3 multi per 30.
    plan = rng.choices(["code", "fixture", "comment", "multi"], weights=[16, 8, 3, 3], k=args.count)
    commits = args.out / "commits"
    commits.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for i, kind in enumerate(plan):
        if kind == "code":
            edits = [code_mutation(rng, root, files, touched)]
        elif kind == "fixture":
            edits = [fixture_mutation(rng, root, fixtures, touched)]
        elif kind == "comment":
            edits = [comment_mutation(rng, root, files, touched)]
        else:
            makers = [lambda: code_mutation(rng, root, files, touched), lambda: fixture_mutation(rng, root, fixtures, touched),
                      lambda: comment_mutation(rng, root, files, touched)]
            edits, names = [], set()
            for make in rng.sample(makers, k=rng.choice([2, 3])):
                edit = make()
                if edit[0] not in names:
                    names.add(edit[0])
                    edits.append(edit)
        commit = f"m{args.seed:02d}{i:03d}"
        (commits / f"{commit}.patch").write_text("".join(diff(name, old, new) for name, old, new, _ in edits))
        manifest[commit] = {"kind": kind, "edits": [[name, edit_kind] for name, _, _, edit_kind in edits]}
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(f"wrote {len(plan)} mutants to {commits}")


if __name__ == "__main__":
    main()
