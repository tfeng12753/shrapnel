"""Targeted mutant families in tools/mutants.py (evaluation tooling)."""
import ast
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
import mutants  # noqa: E402
from selector import apply_change, parse_patch  # noqa: E402

DIALECT = '''\
class Dialect:
    DPIPE_IS_STRING_CONCAT = True
    """Docstring."""

    STRICT_STRING_CONCAT = False
    lower_case = True
    TYPED: bool = False
    SETTINGS = {"a": True, "b": 1, "c": "x", "d": "y", "e": Kind.ONE, "f": Kind.TWO}

    class Tokenizer:
        KEYWORDS = {"SELECT": Token.SELECT, "FROM": Token.FROM}
        SKIP_ME = True


def helper(items, key):
    if len(items) == 2 and key is not None:
        return key in items
    return items[0] < 3
'''
TYPING = '''\
import typing as t


class Base(t.TypedDict, total=False):
    a: int


class Child(Base):
    b: int


class Plain:
    c: int
'''
OTHER = "ENABLED = True\n\n\nclass Unrelated:\n    FLAG = True\n"
FIXTURE = """\
--------------------------------------
-- Q1
--------------------------------------
select l_quantity from lineitem where l_tax > 5;
SELECT "lineitem"."l_quantity" AS "l_quantity" FROM "lineitem" AS "lineitem" WHERE "lineitem"."l_tax" > 5;

# execute: false
select o_orderkey from orders where o_total = 10;
SELECT "orders"."o_orderkey" AS "o_orderkey" FROM "orders" AS "orders" WHERE "orders"."o_total" = 10;
"""


def fixture_pairs(text):
    """Same split as the suite's fixture-pair loader (meta lines dropped)."""
    statements = "\n".join(l for l in text.splitlines() if l and not l.startswith("--")).split(";")
    pairs = []
    for i in range(0, len(statements) - 1, 2):
        sql = "\n".join(l for l in statements[i].strip().split("\n") if not l.startswith("#"))
        pairs.append((sql, statements[i + 1].strip()))
    return pairs


class FamilyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.source = base / "src"
        for name, text in {"sqlglot/dialects/dialect.py": DIALECT, "sqlglot/_typing.py": TYPING,
                           "sqlglot/other.py": OTHER, "sqlglot/parser.py": "X = 1\n",
                           mutants.TPC_FIXTURES[0]: FIXTURE, "tests/fixtures/other.sql": "SELECT 1 FROM t\nSELECT 2 FROM u\n"}.items():
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        # A sample patch touching STRICT_STRING_CONCAT: that line must never be mutated.
        self.sample = base / "sample"
        (self.sample / "commits").mkdir(parents=True)
        (self.sample / "commits" / "s.patch").write_text(
            "--- a/sqlglot/dialects/dialect.py\n+++ b/sqlglot/dialects/dialect.py\n@@ -5,1 +5,1 @@\n"
            "-    STRICT_STRING_CONCAT = False\n+    STRICT_STRING_CONCAT = True\n")
        self.base = base

    def tearDown(self):
        self.tmp.cleanup()

    def generate(self, out, *extra, count=6, seed=3):
        argv = ["--source", str(self.source), "--sample", str(self.sample), "--out", str(self.base / out),
                "--count", str(count), "--seed", str(seed), *extra]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            mutants.main(argv)
        manifest = json.loads((self.base / out / "manifest.json").read_text())
        patches = {c: (self.base / out / "commits" / f"{c}.patch").read_text() for c in manifest}
        return manifest, patches

    def applied(self, patch):
        files = {}
        for change in parse_patch(patch):
            old = (self.source / change["old"]).read_text()
            files[change["old"]], _, _ = apply_change(old, change)
        return files

    def test_setting_flip(self):
        manifest, patches = self.generate("sf", "--families", "setting-flip", count=3)
        seen = set()
        for commit, entry in manifest.items():
            self.assertEqual((entry["kind"], entry["family"]), ("code", "setting-flip"))
            detail = entry["details"][0]
            seen.add(detail["setting"])
            (name, new), = self.applied(patches[commit]).items()
            self.assertEqual(name, "sqlglot/dialects/dialect.py")
            ast.parse(new)
        # Only UPPER_CASE bools directly in classes of the setting files; touched line excluded.
        self.assertEqual(seen, {"DPIPE_IS_STRING_CONCAT", "TYPED", "SKIP_ME"})

    def test_match_selects_the_base_dpipe_flip(self):
        manifest, patches = self.generate("dp", "--families", "setting-flip", "--match", "DPIPE_IS_STRING_CONCAT",
                                          count=1)
        (patch,) = patches.values()
        self.assertIn("-    DPIPE_IS_STRING_CONCAT = True\n+    DPIPE_IS_STRING_CONCAT = False\n", patch)
        self.assertTrue(patch.startswith("diff --git a/sqlglot/dialects/dialect.py b/sqlglot/dialects/dialect.py\n"))

    def test_typeddict_total_and_exhaustion(self):
        manifest, patches = self.generate("td", "--families", "typeddict-total", count=5)
        # Two TypedDicts (Child inherits) -> two mutants, then the family is used up.
        self.assertEqual(len(manifest), 2)
        texts = sorted(self.applied(p)["sqlglot/_typing.py"] for p in patches.values())
        self.assertTrue(any("class Base(t.TypedDict, total=True):" in t for t in texts))
        self.assertTrue(any("class Child(Base, total=False):" in t for t in texts))

    def test_dict_value_keeps_the_key(self):
        manifest, patches = self.generate("dv", "--families", "dict-value", count=8)
        for commit, entry in manifest.items():
            detail = entry["details"][0]
            self.assertNotEqual(detail["old"], detail["new"])
            for row in patches[commit].splitlines():
                if row.startswith("+") and not row.startswith("+++"):
                    self.assertIn(detail["key"] + ": " + detail["new"], row)
            if detail["old"].startswith("Kind."):
                self.assertTrue(detail["new"].startswith("Kind."))
            if detail["old"] in ('"x"', '"y"'):
                self.assertIn(detail["new"], ('"x"', '"y"'))

    def test_comparison_swap(self):
        manifest, patches = self.generate("cs", "--families", "comparison-swap", count=6)
        pairs = {(d["old"], d["new"]) for e in manifest.values() for d in e["details"]}
        allowed = {("==", "!="), ("is not", "is"), ("in", "not in"), ("<", "<="), ("2", "1"), ("2", "3"),
                   ("3", "2"), ("3", "4")}
        self.assertTrue(pairs <= allowed, pairs)
        self.assertTrue(any(e["edits"][0][1] == "len-compare" for e in manifest.values()))
        for patch in patches.values():
            for new in self.applied(patch).values():
                ast.parse(new)

    def test_fixture_entry_records_entry_and_half(self):
        manifest, patches = self.generate("fx", "--families", "fixture-entry", count=10)
        for commit, entry in manifest.items():
            self.assertEqual((entry["kind"], entry["family"]), ("fixture", "fixture-entry"))
            detail = entry["details"][0]
            new = self.applied(patches[commit])[mutants.TPC_FIXTURES[0]]
            before, after = fixture_pairs(FIXTURE), fixture_pairs(new)
            changed = [(i, h) for i in range(len(before)) for h in (0, 1) if before[i][h] != after[i][h]]
            self.assertEqual(changed, [(detail["entry"], ["input", "expected"].index(detail["half"]))])
            self.assertIn("# execute: false", new)

    def test_seeded_and_default_unchanged(self):
        one = self.generate("a", "--families", "comparison-swap,fixture-entry,dict-value", count=8)
        two = self.generate("b", "--families", "dict-value,fixture-entry,comparison-swap", count=8)
        self.assertEqual(one, two)
        default = self.generate("c", count=8)
        full = self.generate("d", "--families", "multi,comment,fixture,code", count=8)
        self.assertEqual(default, full)
        self.assertEqual({e["family"] for e in default[0].values()} - set(mutants.OLD_FAMILIES), set())
        for entry in default[0].values():
            self.assertEqual(entry["family"], entry["kind"])
            self.assertNotIn("details", entry)

    def test_unknown_family_is_rejected(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.generate("x", "--families", "nope")


if __name__ == "__main__":
    unittest.main()
