"""Class-setup fixture attribution: data flow, per-test refinement, and the selector's setup check."""
import ast
import difflib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch as mock_patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from setup_flow import class_level_access, find_function, setup_flow  # noqa: E402
from selector import REVISION, Selector  # noqa: E402

TEST_MODULE = '''import unittest
from helpers import load


class TestData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = object()
        cls.a_rows = list(load("a.sql"))
        cls.b_rows = list(load("b.sql"))
        merged = cls.a_rows + []
        cls.summary = len(merged)

    def test_a(self):
        self.assertTrue(self.a_rows)

    def test_b(self):
        self.assertTrue(self.b_rows)

    def test_summary(self):
        self.assertTrue(self.summary)
'''


def setup_func(source=TEST_MODULE):
    tree = ast.parse(source)
    klass = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef))
    return tree, next(n for n in klass.body if isinstance(n, ast.FunctionDef) and n.name == "setUpClass")


def opened_lines(func, files):
    """Simulate the recorder: each file opens on the statement whose source mentions it."""
    lines = {}
    for name in files:
        for statement in func.body:
            if name in ast.unparse(statement):
                lines.setdefault(name, set()).add(statement.lineno)
    return lines


class SetupFlowTests(unittest.TestCase):
    def test_files_reach_only_their_attributes_and_derived_ones(self):
        _, func = setup_func()
        flow, problems = setup_flow(func, opened_lines(func, ["a.sql", "b.sql"]))
        self.assertEqual(problems, [])
        self.assertEqual(flow["a.sql"], ["a_rows", "summary"])
        self.assertEqual(flow["b.sql"], ["b_rows"])

    def test_global_store_disables_refinement(self):
        source = TEST_MODULE.replace("        cls.conn = object()\n", "        helpers.STATE = load('a.sql')\n")
        _, func = setup_func(source)
        self.assertNotEqual(setup_flow(func, opened_lines(func, ["a.sql"]))[1], [])

    def test_bare_cls_and_super_disable_refinement(self):
        for line in ("        prepare(cls)\n", "        super().setUpClass()\n"):
            with self.subTest(line=line.strip()):
                _, func = setup_func(TEST_MODULE.replace("        cls.conn = object()\n", line))
                self.assertNotEqual(setup_flow(func, {})[1], [])

    def test_nested_helper_storing_into_parameters_is_allowed(self):
        source = TEST_MODULE.replace("        cls.conn = object()\n",
                                     "        def fill(target):\n            target['x'] = load('a.sql')\n        cls.cache = {}\n        fill(cls.cache)\n")
        _, func = setup_func(source)
        flow, problems = setup_flow(func, opened_lines(func, ["a.sql"]))
        self.assertEqual(problems, [])
        self.assertIn("cache", flow["a.sql"])

    def test_unmapped_open_line_keeps_class_wide(self):
        _, func = setup_func()
        flow, _ = setup_flow(func, {"a.sql": {1}})
        self.assertNotIn("a.sql", flow)

    def test_class_level_access_is_detected(self):
        tree = ast.parse(TEST_MODULE + "\n\ndef helper():\n    return TestData.a_rows\n")
        self.assertTrue(class_level_access(tree, "TestData"))
        self.assertTrue(class_level_access(ast.parse(TEST_MODULE.replace("self.summary", "type(self).summary")), "TestData"))
        self.assertEqual(class_level_access(ast.parse(TEST_MODULE), "TestData"), [])

    def test_find_function_handles_decorator_line(self):
        tree, func = setup_func()
        self.assertIs(find_function(tree, func.decorator_list[0].lineno, "setUpClass"), func)


class RefineClassReadersTests(unittest.TestCase):
    def test_refinement_uses_instance_reads(self):
        import build_map
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tests").mkdir()
            (root / "tests/test_data.py").write_text(TEST_MODULE)
            _, func = setup_func()
            rec = types.SimpleNamespace(
                root=root, tests=[f"tests/test_data.py::TestData::{n}" for n in ("test_a", "test_b", "test_summary")],
                setup_code={"tests/test_data.py::TestData": (str(root / "tests/test_data.py"), func.decorator_list[0].lineno, "setUpClass")},
                setup_opens={"tests/test_data.py::TestData": opened_lines(func, ["a.sql", "b.sql"])},
                instance_reads={"tests/test_data.py::TestData::test_a": {"a_rows", "assertTrue"},
                                "tests/test_data.py::TestData::test_b": {"b_rows"},
                                "tests/test_data.py::TestData::test_summary": {"summary"}})
            by_file = build_map.refine_class_readers(rec, "tests/test_data.py::TestData")
            self.assertEqual(by_file["a.sql"], {"tests/test_data.py::TestData::test_a", "tests/test_data.py::TestData::test_summary"})
            self.assertEqual(by_file["b.sql"], {"tests/test_data.py::TestData::test_b"})


def diff(name, old, new):
    return "diff --git a/{0} b/{0}\n".format(name) + "".join(
        difflib.unified_diff(old.splitlines(True), new.splitlines(True), "a/" + name, "b/" + name))


class SelectorSetupCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "fixtures").mkdir()
        (self.root / "fixtures/a.sql").write_text("SELECT 1;\nSELECT 2;\n")
        # A reads a.sql through setup; B (slow) belongs to the class but does not; C is unrelated.
        self.tests = [{"number": 0, "id": "tests/test_data.py::TestData::test_a", "file": "tests/test_data.py", "seconds": .1},
                      {"number": 1, "id": "tests/test_data.py::TestData::test_b", "file": "tests/test_data.py", "seconds": 20.0},
                      {"number": 2, "id": "tests/test_other.py::TestOther::test_c", "file": "tests/test_other.py", "seconds": .1}]
        self.mapping = {"version": 1, "revision": REVISION, "complete": True, "tests": [t["id"] for t in self.tests],
                        "files": {"fixtures/a.sql": {"lines": {}, "regions": [], "executed": "0", "readers": "1",
                                                     "setup_classes": {"tests/test_data.py::TestData": "3"}}}}
        (self.root / "map.json").write_text(json.dumps(self.mapping))
        self.patch = diff("fixtures/a.sql", "SELECT 1;\nSELECT 2;\n", "SELECT 1;\nSELECT 42 2;\n")

    def tearDown(self):
        self.tmp.cleanup()

    def selector(self, budget):
        return Selector(self.tests, self.root, self.root / "map.json", setup_check_budget=budget)

    def test_verified_setup_narrows_to_attributed_readers(self):
        with mock_patch("native_probes.verify_setup", return_value=(True, 2.0)) as verify:
            selector = self.selector(8.0)
            self.assertEqual(selector.select(self.patch), 1)
        verify.assert_called_once()
        self.assertEqual(verify.call_args[0][2:4], ("tests.test_data", "TestData"))
        self.assertEqual(selector.setup_check_budget, 6.0)

    def test_failed_setup_keeps_the_whole_class(self):
        with mock_patch("native_probes.verify_setup", return_value=(False, 1.0)):
            self.assertEqual(self.selector(8.0).select(self.patch), 3)

    def test_no_budget_keeps_the_whole_class_without_probing(self):
        with mock_patch("native_probes.verify_setup") as verify:
            self.assertEqual(self.selector(0.0).select(self.patch), 3)
        verify.assert_not_called()

    def test_small_saving_skips_the_probe(self):
        self.tests[1]["seconds"] = 1.0
        with mock_patch("native_probes.verify_setup") as verify:
            self.assertEqual(self.selector(8.0).select(self.patch), 3)
        verify.assert_not_called()

    def test_conservative_mode_ignores_refinement(self):
        selector = Selector(self.tests, self.root, self.root / "map.json", conservative_python=True, setup_check_budget=8.0)
        self.assertEqual(selector.select(self.patch), 3)


@unittest.skipUnless(hasattr(__import__("os"), "fork"), "forked setup checks need POSIX")
class RealSetupProbeTests(unittest.TestCase):
    MODULE = '''import unittest


class TestNumbers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open("fixtures/n.txt") as f:
            cls.numbers = [int(x) for x in f.read().split()]

    def test_sum(self):
        self.assertTrue(self.numbers)
'''

    def run_probe(self, fixture_text):
        from native_probes import verify_setup
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tests").mkdir()
            (root / "tests/__init__.py").write_text("")
            (root / "tests/test_numbers.py").write_text(self.MODULE)
            (root / "fixtures").mkdir()
            (root / "fixtures/n.txt").write_text("1 2 3\n")
            return verify_setup(root, {"fixtures/n.txt": fixture_text}, "tests.test_numbers", "TestNumbers",
                                wall_seconds=10.0, cpu_seconds=5.0)

    def test_patched_fixture_that_loads(self):
        ok, cpu = self.run_probe("1 42 3\n")
        self.assertTrue(ok)
        self.assertGreaterEqual(cpu, 0.0)

    def test_patched_fixture_that_crashes_setup(self):
        ok, _ = self.run_probe("1 x 3\n")
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
