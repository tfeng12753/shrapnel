"""Behavioral checks independent of sqlglot, Docker, and optional packages."""
import difflib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch as mock_patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from selector import Selector, REVISION, apply_change, import_hazard, parse_patch, split

spec = importlib.util.spec_from_file_location("build_map", ROOT / "tools/build_map.py")
map_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(map_module)


def diff(name, old, new):
    return "diff --git a/{0} b/{0}\n".format(name) + "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), "a/" + name, "b/" + name))


class SchedulingTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(split([]), [])

    def test_worker_overhead_changes_decision(self):
        self.assertEqual(len(split([{"number": i, "seconds": 1} for i in range(3)])), 1)
        self.assertEqual(len(split([{"number": i, "seconds": 10} for i in range(2)])), 2)

    def test_full_suite_fallback_score(self):
        tests = json.loads((ROOT / "sample/tests.json").read_text())["tests"]
        workers = split(tests)
        selected = [n for worker in workers for n in worker]
        self.assertEqual(sorted(selected), list(range(len(tests))))
        self.assertEqual(len(workers), 4)
        costs = {t["number"]: round(1000 * t["seconds"]) for t in tests}
        self.assertEqual(max(sum(costs[n] for n in w) for w in workers) + 2000 * len(workers), 36817)


class PatchTests(unittest.TestCase):
    def test_separated_hunks(self):
        old = "".join(f"x{i} = {i}\n" for i in range(25))
        new = old.replace("x1 = 1", "x1 = 8").replace("x24 = 24", "x24 = 6")
        result, removed, inserted = apply_change(old, parse_patch(diff("m.py", old, new))[0])
        self.assertEqual(result, new)
        self.assertEqual(removed, {2, 25})
        self.assertEqual(inserted, set())

    def test_insertion_anchors(self):
        old, new = "x = 1\ny = 2\n", "x = 1\nz = 3\ny = 2\n"
        result, removed, inserted = apply_change(old, parse_patch(diff("m.py", old, new))[0])
        self.assertEqual((result, removed, inserted), (new, set(), {1}))

    def test_reject_inconsistent_new_position(self):
        patch = diff("m.py", "x = 1\n", "x = 2\n").replace("@@ -1 +1 @@", "@@ -1 +8 @@")
        with self.assertRaises(ValueError):
            apply_change("x = 1\n", parse_patch(patch)[0])

    def test_reject_unhandled_file_in_mixed_patch(self):
        rename = "diff --git a/old b/new\nsimilarity index 100%\nrename from old\nrename to new\n"
        edit = diff("m.py", "x = 1\n", "x = 2\n")
        for patch in (edit + rename, rename + edit):
            with self.assertRaises(ValueError):
                parse_patch(patch)

    def test_reject_incorrect_base(self):
        patch = diff("m.py", "x = 1\n", "x = 2\n")
        with self.assertRaises(ValueError):
            apply_change("z = 1\n", parse_patch(patch)[0])

    def test_import_initialization_hazards(self):
        self.assertTrue(import_hazard("import math\n", "import absent\n"))
        self.assertTrue(import_hazard("N = 1 / 1\n", "N = 1 / 0\n"))
        self.assertFalse(import_hazard("class A:\n N = 16\n", "class A:\n N = 17\n"))
        self.assertFalse(import_hazard("def f():\n return 1\n", "def f():\n return 2\n"))


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.tests = [{"number": i, "id": name, "file": "tests/test_m.py", "seconds": .1} for i, name in enumerate(["A", "B", "C"])]
        self.mapping = {"version": 1, "revision": REVISION, "complete": True, "tests": ["B", "A", "C"], "files": {}}
        (self.root / "tests").mkdir()
        self.source("tests/test_m.py", "def test_m():\n    pass\n")
        self.entry("tests/test_m.py")
        self.source("m.py", "def f():\n    return 1\n")
        self.entry("m.py", {"2": "2"}, [[1, 2, "function", "3"], [1, 1, "header", "3"]])
        self.source("fixture.sql", "SELECT 1;\n")
        self.entry("fixture.sql", readers="4")

    def tearDown(self):
        self.tmp.cleanup()

    def source(self, name, content):
        (self.root / name).write_text(content)

    def entry(self, name, lines=None, regions=None, readers="0"):
        self.mapping["files"][name] = {"lines": lines or {}, "regions": regions or [], "executed": "3", "readers": readers}

    def selector(self, **options):
        path = self.root / "map.json"
        path.write_text(json.dumps(self.mapping))
        return Selector(self.tests, self.root, path, **options)

    def test_uses_test_ids_instead_of_assuming_number_order(self):
        patch = diff("m.py", "def f():\n    return 1\n", "def f():\n    return 2\n")
        self.assertEqual(self.selector().select(patch), 1)

    def test_trace_uses_remapped_test_numbers_and_baseline_lines(self):
        patch = diff("m.py", "def f():\n    return 1\n", "def f():\n    return 2\n")
        events = []
        selected = self.selector().select(patch, lambda *event: events.append(event))
        self.assertEqual(selected, 1)
        self.assertEqual(events, [(1, "affected line or anchor", "m.py", 2)])

    def test_comment_is_semantically_unchanged(self):
        patch = diff("m.py", "def f():\n    return 1\n", "# comment\ndef f():\n    return 1\n")
        self.assertEqual(self.selector().select(patch), 0)

    def test_mixed_comment_and_fixture_patch(self):
        patch = diff("m.py", "def f():\n    return 1\n", "# comment\ndef f():\n    return 1\n")
        patch += diff("fixture.sql", "SELECT 1;\n", "SELECT invalid syntax;\n")
        self.assertEqual(self.selector().select(patch), 4)

    def test_pure_insertion_covers_function_users(self):
        patch = diff("m.py", "def f():\n    return 1\n", "def f():\n    raise Exception()\n    return 1\n")
        self.assertEqual(self.selector().select(patch), 3)

    def test_fixture_comment_is_not_ignored(self):
        patch = diff("fixture.sql", "SELECT 1;\n", "-- semantic hint\nSELECT 1;\n")
        self.assertEqual(self.selector().select(patch), 4)

    def test_conservative_mode_distinguishes_code_from_comments_and_fixture_edits(self):
        s = self.selector()
        s.conservative_python = True
        old = self.root.joinpath("m.py").read_text()
        comment = diff("m.py", old, "# note\n" + old)
        fixture = diff("fixture.sql", "SELECT 1;\n", "SELECT 2;\n")
        self.assertEqual(s.select(comment + fixture), 4)
        self.assertEqual(s.select(fixture + diff("m.py", old, old.replace("1", "2"))), s.all)

    def test_mixed_replacement_and_insertion_keeps_other_function_users(self):
        old = "def f(flag):\n    if flag:\n        return 1\n    return 0\n"
        new = old.replace("        return 1", "        return 2\n    raise ValueError()")
        self.source("m.py", old)
        self.entry("m.py", {"3": "2"}, [[1, 4, "function", "3"]])
        self.assertEqual(self.selector().select(diff("m.py", old, new)), 3)

    def test_syntax_error_requires_full_suite(self):
        patch = diff("m.py", "def f():\n    return 1\n", "def f(:\n    return 1\n")
        self.assertEqual(self.selector().select(patch), 7)

    def test_missing_test_requires_full_suite(self):
        self.mapping["tests"].remove("C")
        self.assertEqual(self.selector().select("any patch"), 7)

    def test_revision_mismatch_requires_full_suite(self):
        self.mapping["revision"] = "wrong"
        self.assertEqual(self.selector().select("any patch"), 7)

    def test_unknown_file_requires_full_suite(self):
        self.source("unmapped.py", "x = 1\n")
        patch = diff("unmapped.py", "x = 1\n", "x = 2\n")
        self.assertEqual(self.selector().select(patch), 7)

    def test_direct_source_reader_is_included(self):
        self.mapping["files"]["m.py"]["readers"] = "4"
        patch = diff("m.py", "def f():\n    return 1\n", "def f():\n    return 2\n")
        self.assertEqual(self.selector().select(patch), 5)

    def test_path_traversal_requires_full_suite(self):
        patch = diff("../secret.py", "x = 1\n", "x = 2\n")
        self.assertEqual(self.selector().select(patch), 7)

    def test_comment_preserves_tests_that_read_source(self):
        self.mapping["files"]["m.py"]["readers"] = "4"
        patch = diff("m.py", "def f():\n    return 1\n", "# comment\ndef f():\n    return 1\n")
        self.assertEqual(self.selector().select(patch), 4)

    def test_subprocess_tests_are_kept_for_executable_changes(self):
        self.mapping["external"] = "4"
        patch = diff("m.py", "def f():\n    return 1\n", "def f():\n    return 2\n")
        self.assertEqual(self.selector().select(patch), 5)

    def test_child_work_in_helper_uses_observed_callers(self):
        self.source("helper.py", "def execute(pool, work):\n    launch = pool.submit\n    return launch(work)\n")
        self.entry("helper.py", {"2": "1", "3": "1"})
        self.mapping["native"] = ["A", "B"]
        old = self.root.joinpath("m.py").read_text()
        patch = diff("m.py", old, old.replace("1", "2"))
        self.assertEqual(self.selector().select(patch), 1)
        selector = self.selector(child_work=True)
        events = []
        self.assertEqual(selector.select(patch, lambda *event: events.append(event)), 3)
        self.assertIn((2, "potential untraced child work", "m.py", None), events)
        with mock_patch("native_probes.verify") as verify:
            self.assertEqual(selector.probe(patch, 3, 1), (3, 0.0))
            verify.assert_not_called()
        self.assertEqual(selector.select(diff("m.py", old, "# note\n" + old)), 0)

    def test_child_work_keeps_shared_hook_users(self):
        test_file = "test_example.py"
        for test in self.tests[1:]:
            test["file"] = test_file
        self.source(test_file, "from subprocess import run as spawn\n"
                    "def load():\n    spawn(['python'])\n"
                    "def setUpModule():\n    load()\n")
        self.entry(test_file, {"3": "1"})
        old = self.root.joinpath("m.py").read_text()
        # Record the shared subprocess only for the first test in the module.
        selector = self.selector(child_work=True)
        self.assertEqual(selector.child_work, 6)
        self.assertEqual(selector.select(diff("m.py", old, old.replace("1", "2"))), 7)

    def test_child_work_finds_pool_from_aliased_context(self):
        self.source("helper.py", "from multiprocessing import get_context as context\n"
                    "def start_pool():\n    ctx = context('spawn')\n    return ctx.Pool(2)\n")
        self.entry("helper.py", {"4": "1"})
        self.assertEqual(self.selector(child_work=True).child_work, 2)

    def test_child_work_preserves_missing_evidence(self):
        self.source("helper.py", "def execute(pool, work):\n    return pool.map(work, [])\n")
        self.entry("helper.py")
        old = self.root.joinpath("m.py").read_text()
        selector = self.selector(child_work=True)
        self.assertEqual(selector.select(diff("m.py", old, old.replace("1", "2"))), 7)
        self.assertEqual(selector.select(diff("m.py", old, "# note\n" + old)), 0)

    def test_child_work_preserves_missing_source(self):
        self.entry("helper.py", {"2": "1"})
        old = self.root.joinpath("m.py").read_text()
        self.assertEqual(self.selector(child_work=True).select(diff("m.py", old, old.replace("1", "2"))), 7)


class MapModelTests(unittest.TestCase):
    def test_aliases_reexports_and_class_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "sqlglot").mkdir()
            (root / "tests").mkdir()
            (root / "sqlglot/__init__.py").write_text("from sqlglot.core import Root as Alias\n")
            (root / "sqlglot/core.py").write_text("class Root:\n    LIMIT = 8\n    def f(self):\n        return self.LIMIT\n")
            (root / "sqlglot/use.py").write_text("from sqlglot import Alias\ndef g():\n    return Alias.LIMIT\n")
            model = map_module.SourceModel(root)
            target = ("sqlglot/core.py", "Root.LIMIT")
            self.assertEqual(model.resolve("sqlglot/use.py", "Alias.LIMIT"), target)
            self.assertEqual(model.class_fields[("sqlglot.core", "Root", "LIMIT")], target)
            node = model.symbols[("sqlglot/use.py", "g")]["node"]
            self.assertIn(target, set(model.references("sqlglot/use.py", node)))

    def test_inherited_attribute_read_uses_defining_class(self):
        class Parent:
            LIMIT = 8
        class Child(Parent):
            pass
        key = ("source.py", "Parent.LIMIT")
        model = types.SimpleNamespace(attr_names={"LIMIT"}, class_fields={(Parent.__module__, Parent.__qualname__, "LIMIT"): key})
        rec = map_module.Recorder(Path("/tmp"), model, None)
        rec.current = "test_example"
        rec.record_attr(Child, "LIMIT")
        self.assertEqual(rec.attrs[key], {"test_example"})


class MapIntegrationTests(unittest.TestCase):
    def test_observations_propagate_to_global_constants_and_fixture_readers(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "sqlglot").mkdir()
            (root / "tests").mkdir()
            source = root / "sqlglot/core.py"
            text = "LIMIT = 8\ndef f():\n    return LIMIT\ndef g():\n    return 99\n"
            source.write_text(text)
            fixture = root / "data.sql"
            fixture.write_text("SELECT 1;\n")
            model = map_module.SourceModel(root)
            rec = map_module.Recorder(root, model, None)
            rec.tests = ["A", "B"]
            rec.reads["data.sql"].add(("test", "B"))
            class Observations:
                def measured_files(self):
                    return [str(source)]
                def contexts_by_lineno(self, filename):
                    return {3: ["A"], 5: ["B"]}
            output = root / "map.json.gz"
            map_module.write_map(root, model, rec, Observations(), output)
            tests = [{"number": i, "id": name, "seconds": .1} for i, name in enumerate(rec.tests)]
            selector = Selector(tests, root, output)
            self.assertTrue(selector.complete)
            self.assertEqual(selector.select(diff("sqlglot/core.py", text, text.replace("LIMIT = 8", "LIMIT = 9"))), 1)
            self.assertEqual(selector.select(diff("data.sql", "SELECT 1;\n", "SELECT 2;\n")), 2)

    def test_observed_class_fields_select_runtime_users(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "sqlglot").mkdir()
            (root / "tests").mkdir()
            source = root / "sqlglot/core.py"
            text = "class Parent:\n    LIMIT = 8\n"
            source.write_text(text)
            model = map_module.SourceModel(root)
            rec = map_module.Recorder(root, model, None)
            rec.tests = ["A", "B"]
            rec.current = "B"
            parent = type("Parent", (), {"LIMIT": 8, "__module__": "sqlglot.core"})
            rec.record_attr(parent, "LIMIT")
            class Observations:
                def measured_files(self):
                    return []
            output = root / "map.json"
            map_module.write_map(root, model, rec, Observations(), output)
            tests = [{"number": i, "id": name, "seconds": .1} for i, name in enumerate(rec.tests)]
            selector = Selector(tests, root, output)
            self.assertEqual(selector.select(diff("sqlglot/core.py", text, text.replace("8", "9"))), 2)


if __name__ == "__main__":
    unittest.main()
