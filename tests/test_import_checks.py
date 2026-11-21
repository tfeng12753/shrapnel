"""Import checks for initializer-only edits and import-closure narrowing of external tests."""
import difflib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from selector import REVISION, Selector, import_hazard_kind, module_for

MODULE = '''def make(flag):
    return lambda: flag


class P:
    TABLE = {
        "a": make(True),
    }
'''


def diff(name, old, new):
    return "diff --git a/{0} b/{0}\n".format(name) + "".join(
        difflib.unified_diff(old.splitlines(True), new.splitlines(True), "a/" + name, "b/" + name))


class HazardKindTests(unittest.TestCase):
    def test_classification(self):
        self.assertIsNone(import_hazard_kind("def f():\n return 1\n", "def f():\n return 2\n"))
        self.assertIsNone(import_hazard_kind("N = 1\n", "N = 2\n"))
        self.assertEqual(import_hazard_kind("N = f(1)\n", "N = f(2)\n"), "initializer")
        self.assertEqual(import_hazard_kind("class A:\n T = {'a': g(True)}\n", "class A:\n T = {'a': g(False)}\n"), "initializer")
        self.assertEqual(import_hazard_kind("import math\n", "import absent\n"), "structural")
        self.assertEqual(import_hazard_kind("class A(B):\n pass\n", "class A(C):\n pass\n"), "structural")
        self.assertEqual(import_hazard_kind("N = f(1)\n", "M = f(1)\n"), "structural")
        self.assertEqual(import_hazard_kind("f(1)\n", "f(2)\n"), "structural")

    def test_module_for(self):
        self.assertEqual(module_for("sqlglot/parsers/singlestore.py"), "sqlglot.parsers.singlestore")
        self.assertEqual(module_for("sqlglot/__init__.py"), "sqlglot")
        self.assertIsNone(module_for("tests/test_parser.py"))
        self.assertIsNone(module_for("sqlglot/fixture.sql"))


class SelectorImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "sqlglot").mkdir()
        (self.root / "sqlglot/__init__.py").write_text("")
        (self.root / "sqlglot/m.py").write_text(MODULE)
        # Map order B, A, C: hex mask 2 is A (number 0, bit 1), 1 is B (bit 2), 4 is C (bit 4).
        self.tests = [{"number": i, "id": name, "file": "tests/test_m.py", "seconds": .1} for i, name in enumerate("ABC")]
        self.mapping = {"version": 1, "revision": REVISION, "complete": True, "tests": ["B", "A", "C"],
                        "external": "4", "files": {
                            "sqlglot/__init__.py": {"lines": {}, "regions": [], "executed": "7", "readers": "0"},
                            "sqlglot/m.py": {"lines": {"2": "1", "7": "2"}, "regions": [[1, 2, "function", "1"], [6, 8, "attribute", "2"]],
                                             "executed": "3", "readers": "0"}}}
        self.closure = {"revision": REVISION, "tests": ["C"], "files": ["sqlglot/__init__.py"]}

    def tearDown(self):
        self.tmp.cleanup()

    def selector(self, **options):
        path = self.root / "map.json"
        path.write_text(json.dumps(self.mapping))
        return Selector(self.tests, self.root, path, **options)

    def test_initializer_change_without_budget_selects_everything(self):
        patch = diff("sqlglot/m.py", MODULE, MODULE.replace("make(True)", "make(False)"))
        self.assertEqual(self.selector().select(patch), 7)

    @unittest.skipUnless(hasattr(os, "fork"), "forked import checks need POSIX")
    def test_initializer_change_that_imports_uses_definition_users(self):
        patch = diff("sqlglot/m.py", MODULE, MODULE.replace("make(True)", "make(False)"))
        selector = self.selector(import_check_budget=4.0)
        self.assertEqual(selector.select(patch), 1 | 4)
        self.assertLess(selector.import_check_budget, 4.0)

    @unittest.skipUnless(hasattr(os, "fork"), "forked import checks need POSIX")
    def test_initializer_change_that_raises_selects_everything(self):
        patch = diff("sqlglot/m.py", MODULE, MODULE.replace("make(True)", "make(1 / 0)"))
        self.assertEqual(self.selector(import_check_budget=4.0).select(patch), 7)

    def test_closure_drops_lazy_external_outside_import_closure(self):
        patch = diff("sqlglot/m.py", MODULE, MODULE.replace("return lambda: flag", "return lambda: not flag"))
        self.assertEqual(self.selector().select(patch), 2 | 4)
        self.assertEqual(self.selector(import_closure=self.closure).select(patch), 2)

    def test_closure_keeps_external_inside_import_closure(self):
        (self.root / "sqlglot/__init__.py").write_text("X = 1\n")
        patch = diff("sqlglot/__init__.py", "X = 1\n", "X = 2\n")
        self.assertEqual(self.selector(import_closure=self.closure).select(patch) & 4, 4)

    def test_closure_only_narrows_listed_external_tests(self):
        self.closure["tests"] = ["A"]
        patch = diff("sqlglot/m.py", MODULE, MODULE.replace("return lambda: flag", "return lambda: not flag"))
        self.assertEqual(self.selector(import_closure=self.closure).select(patch), 2 | 4)


if __name__ == "__main__":
    unittest.main()
