"""Downstream import check: patches that break test collection through import-time execution."""
import difflib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch as mock_patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from import_effects import ImportEffects, assigned_names, module_name  # noqa: E402
from selector import REVISION, Selector  # noqa: E402

SETTINGS = '''class Dialect:
    CONCAT_IS_PIPE = True
    TABLES = {
        "a": 1,
        "b": 2,
    }

    def parse(self, text):
        if not self.CONCAT_IS_PIPE:
            raise ValueError(text)
        return text
'''


def diff(name, old, new):
    return "diff --git a/{0} b/{0}\n".format(name) + "".join(
        difflib.unified_diff(old.splitlines(True), new.splitlines(True), "a/" + name, "b/" + name))


INDEX = {
    "revision": REVISION,
    "closures": {
        "tests/test_gen.py": ["lib/__init__.py", "lib/settings.py", "lib/gen.py"],
        "tests/test_other.py": ["lib/__init__.py", "lib/settings.py"],
    },
    # Importing lib/gen.py runs Dialect.parse (lines 8-11 of lib/settings.py).
    "import_time": {"lib/gen.py": {"lines": {"lib/settings.py": [8, 9, 11]}, "attrs": ["CONCAT_IS_PIPE"]}},
}
TESTS = [{"number": 0, "id": "tests/test_gen.py::T::test_a", "file": "tests/test_gen.py", "seconds": .1},
         {"number": 1, "id": "tests/test_gen.py::T::test_b", "file": "tests/test_gen.py", "seconds": .1},
         {"number": 2, "id": "tests/test_other.py::T::test_c", "file": "tests/test_other.py", "seconds": .1}]


class OwnerTests(unittest.TestCase):
    def setUp(self):
        self.effects = ImportEffects(INDEX, TESTS)

    def test_assigned_names(self):
        self.assertEqual(assigned_names(SETTINGS, frozenset({2})), {"CONCAT_IS_PIPE"})
        self.assertEqual(assigned_names(SETTINGS, frozenset({4})), {"TABLES"})  # inside a multi-line dict
        self.assertEqual(assigned_names(SETTINGS, frozenset({9})), set())

    def test_changed_setting_read_during_import(self):
        self.assertEqual(self.effects.owners([("lib/settings.py", SETTINGS, {2})]), ["lib/gen.py"])

    def test_changed_line_executed_during_import(self):
        self.assertEqual(self.effects.owners([("lib/settings.py", SETTINGS, {10})]), [])
        self.assertEqual(self.effects.owners([("lib/settings.py", SETTINGS, {9})]), ["lib/gen.py"])

    def test_same_name_in_a_file_the_import_never_runs(self):
        self.assertEqual(self.effects.owners([("lib/elsewhere.py", "CONCAT_IS_PIPE = True\n", {1})]), [])

    def test_unread_setting(self):
        self.assertEqual(self.effects.owners([("lib/settings.py", SETTINGS, {4})]), [])

    def test_class_attribute_of_a_class_used_during_import(self):
        # lib/nodes.py's own code never runs during the import, but an instance of
        # its class Values does, and the import's code reads ``arg_types``.
        index = {**INDEX, "import_time": {"lib/gen.py": {"lines": {"lib/core.py": [5]}, "attrs": ["arg_types"],
                                                         "classes": ["lib/nodes.py::Values"]}}}
        effects = ImportEffects(index, TESTS)
        nodes = "class Values:\n    arg_types = {'this': False}\n\nclass Other:\n    arg_types = {'this': False}\n"
        self.assertEqual(effects.owners([("lib/nodes.py", nodes, {2})]), ["lib/gen.py"])
        self.assertEqual(effects.owners([("lib/nodes.py", nodes, {5})]), [])  # Other was never used

    def test_dependents_and_tests(self):
        files = self.effects.dependents(["lib/gen.py"])
        self.assertEqual(files, ["tests/test_gen.py"])
        self.assertEqual(self.effects.tests_of(files), 0b011)

    def test_module_name(self):
        self.assertEqual(module_name("lib/gen.py"), "lib.gen")
        self.assertEqual(module_name("lib/__init__.py"), "lib")
        self.assertIsNone(module_name("fixtures/a.sql"))


class SelectorDownstreamTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "lib").mkdir()
        (self.root / "lib/settings.py").write_text(SETTINGS)
        mapping = {"version": 1, "revision": REVISION, "complete": True, "tests": [t["id"] for t in TESTS],
                   "files": {"lib/settings.py": {"lines": {"2": "4"}, "regions": [[2, 2, "attribute", "4"]],
                                                 "executed": "4", "readers": "0"}}}
        (self.root / "map.json").write_text(json.dumps(mapping))
        self.patch = diff("lib/settings.py", SETTINGS, SETTINGS.replace("CONCAT_IS_PIPE = True", "CONCAT_IS_PIPE = False"))

    def tearDown(self):
        self.tmp.cleanup()

    def selector(self, budget=6.0, effects=True):
        return Selector(TESTS, self.root, self.root / "map.json", relations=True,
                        import_effects=ImportEffects(INDEX, TESTS) if effects else None,
                        downstream_import_budget=budget)

    def test_failed_import_adds_every_test_of_dependent_files(self):
        with mock_patch("native_probes.verify_imports", return_value=(["lib.gen"], 1.0)) as verify:
            self.assertEqual(self.selector().select(self.patch), 0b111)
        self.assertEqual(verify.call_args[0][2], ["lib.gen"])

    def test_successful_import_adds_nothing(self):
        with mock_patch("native_probes.verify_imports", return_value=([], 1.0)):
            self.assertEqual(self.selector().select(self.patch), 0b100)

    def test_unknown_result_keeps_candidates(self):
        with mock_patch("native_probes.verify_imports", return_value=(None, 1.0)):
            self.assertEqual(self.selector().select(self.patch), 0b111)

    def test_no_budget_keeps_candidates_without_probing(self):
        with mock_patch("native_probes.verify_imports") as verify:
            self.assertEqual(self.selector(budget=0.0).select(self.patch), 0b111)
        verify.assert_not_called()

    def test_without_index_behaves_as_before(self):
        with mock_patch("native_probes.verify_imports") as verify:
            self.assertEqual(self.selector(effects=False).select(self.patch), 0b100)
        verify.assert_not_called()

    def test_unexpected_error_never_crashes_selection(self):
        with mock_patch("native_probes.verify_imports", side_effect=RuntimeError("boom")):
            self.assertEqual(self.selector().select(self.patch), 0b100)

    def test_no_probe_when_candidates_already_selected(self):
        mapping = json.loads((self.root / "map.json").read_text())
        mapping["files"]["lib/settings.py"]["regions"] = [[2, 2, "attribute", "7"]]
        (self.root / "map.json").write_text(json.dumps(mapping))
        with mock_patch("native_probes.verify_imports") as verify:
            self.assertEqual(self.selector().select(self.patch), 0b111)
        verify.assert_not_called()


class HazardTreeReuseTests(unittest.TestCase):
    def test_shared_trees_are_not_modified(self):
        import ast
        from selector import import_hazard_kind
        old = "HANDLER = make(lambda x: x + 1)\n"
        new = "HANDLER = make(lambda x: x + 2)\n"
        old_tree, new_tree = ast.parse(old, type_comments=True), ast.parse(new, type_comments=True)
        before = ast.dump(old_tree)
        self.assertEqual(import_hazard_kind(old, new, old_tree, new_tree), import_hazard_kind(old, new))
        self.assertEqual(ast.dump(old_tree), before)


@unittest.skipUnless(hasattr(os, "fork"), "forked import checks need POSIX")
class RealImportProbeTests(unittest.TestCase):
    def run_probe(self, flag):
        from native_probes import verify_imports
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "libx").mkdir()
            (root / "libx/__init__.py").write_text("")
            (root / "libx/settings.py").write_text("FLAG = True\n")
            (root / "libx/gen.py").write_text("from libx.settings import FLAG\nif not FLAG:\n    raise ValueError('import-time failure')\n")
            return verify_imports(root, {"libx/settings.py": f"FLAG = {flag}\n"}, ["libx.gen"], wall_seconds=10.0, cpu_seconds=5.0)

    def test_detects_import_failure(self):
        failed, cpu = self.run_probe(False)
        self.assertEqual(failed, ["libx.gen"])
        self.assertGreaterEqual(cpu, 0.0)

    def test_passing_import(self):
        failed, _ = self.run_probe(True)
        self.assertEqual(failed, [])


if __name__ == "__main__":
    unittest.main()
