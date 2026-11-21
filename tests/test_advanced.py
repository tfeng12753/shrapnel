import ast
from collections import defaultdict
import difflib
from enum import Enum
import importlib.util
import json
import operator
from pathlib import Path
import sys
import tempfile
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from instrument import Instrumentation, OPS, Transformer, outcome, relation
from selector import Selector, REVISION, classify_python_edit
from native_probes import verify
from build_map import Recorder, SourceModel, write_map


def diff(path, old, new):
    return f"diff --git a/{path} b/{path}\n" + "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), "a/"+path, "b/"+path))


class ComparisonTests(unittest.TestCase):
    def test_relations_match_real_builtin_operators(self):
        values = [None, False, True, -3, 0, 2, 2.0, float("nan"), "", "x", b"x", (1, 2)]
        for a in values:
            for b in values:
                state = relation(a, b)
                if state is None:
                    continue  # Unsupported pairs deliberately remain conservative.
                for name, function in OPS.items():
                    predicted = outcome(state, name)
                    if predicted is not None:
                        self.assertEqual(predicted, function(a, b), (a, b, name))

    def test_observation_does_not_call_custom_comparators(self):
        class Dangerous:
            def __eq__(self, other):
                raise AssertionError("observer invoked user code")
        self.assertIsNone(relation(Dangerous(), None))

    def test_identity_equality_for_default_objects(self):
        class Table:
            pass
        for value in (None, Table(), [], {}, 3, "x"):
            state = relation(value, None)
            self.assertEqual(outcome(state, "Is"), outcome(state, "Eq"))

    def test_string_enum_identity_and_equality(self):
        class Strategy(str, Enum):
            UPPER = "upper"
            LOWER = "lower"
        for left in Strategy:
            for right in Strategy:
                state = relation(left, right)
                self.assertIsNotNone(state)
                self.assertEqual(outcome(state, "Eq"), left == right)
                self.assertEqual(outcome(state, "Is"), left is right)

    def test_operator_only_matching_rejects_operand_changes(self):
        def comparison_changes(a, b):
            return classify_python_edit(ast.parse(a), ast.parse(b))
        self.assertIsNone(comparison_changes("x = 1", "x = True"))
        self.assertEqual(comparison_changes("x = 1", "# note\nx = 1"), [])
        self.assertEqual(comparison_changes("x = a < 3\n", "x = a <= 3\n"), [("1:4", "Lt", "LtE")])
        self.assertIsNone(comparison_changes("x = a < 3\n", "x = a <= 4\n"))
        self.assertIsNone(comparison_changes("x = a < 3\n", "x = a > 3\ny = 1\n"))

    def test_boundary_tests_are_selected_individually(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = "def f(a):\n    return a < 3\n"
            (root / "m.py").write_text(old)
            catalog = [{"number": i, "id": str(i), "seconds": .01} for i in range(3)]
            data = {"version": 1, "revision": REVISION, "complete": True, "tests": ["0", "1", "2"], "files": {
                "m.py": {"lines": {"2": "7"}, "comparisons": {"2:11": {"op": "Lt", "unknown": "4", "states": {"p:2": "1", "p:1": "2"}}}}}}
            path = root / "map.json"
            path.write_text(json.dumps(data))
            selector = Selector(catalog, root, path)
            self.assertEqual(selector.select(diff("m.py", old, old.replace("<", "<="))), 6)

    def test_safe_identity_change_can_select_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = "def f(a):\n    return a is None\n"
            (root / "m.py").write_text(old)
            data = {"version": 1, "revision": REVISION, "complete": True, "tests": ["A"], "files": {
                "m.py": {"lines": {"2": "1"}, "comparisons": {"2:11": {"op": "Is", "unknown": "0", "states": {"e:9": "1", "e:0": "1"}}}}}}
            path = root / "map.json"
            path.write_text(json.dumps(data))
            selector = Selector([{"number": 0, "id": "A", "seconds": 21.0}], root, path)
            self.assertEqual(selector.select(diff("m.py", old, old.replace("is None", "== None"))), 0)


class InstrumentationTests(unittest.TestCase):
    def test_annotations_and_short_circuit_are_preserved(self):
        source = 'from __future__ import annotations\nclass Base:\n    FIELD = int\ndef f(x: Base.FIELD) -> Base.FIELD:\n    return x is None or 0 < x < 3\n'
        original = {}
        exec(compile(source, "m.py", "exec"), original)
        attrs, observations = [], []
        recorder = types.SimpleNamespace(current="A", model=types.SimpleNamespace(attr_names={"FIELD"}),
            record_object_attr=lambda obj, name: attrs.append(name), record_comparison=lambda *args: observations.append(args))
        with tempfile.TemporaryDirectory() as directory:
            instrumentation = Instrumentation(Path(directory), recorder)
            instrumentation.install()
            try:
                transformed = ast.fix_missing_locations(Transformer("m.py", {"FIELD"}).visit(ast.parse(source)))
                result = {}
                exec(compile(transformed, "m.py", "exec"), result)
                self.assertEqual(result["f"].__annotations__, original["f"].__annotations__)
                for value in (None, -1, 1, 5):
                    self.assertEqual(result["f"](value), original["f"](value))
                self.assertTrue(observations)
            finally:
                instrumentation.restore()

    def test_classmethod_attribute_access_is_observed(self):
        source = 'class Base:\n    FIELD = 3\n    @classmethod\n    def f(cls):\n        return cls.FIELD\n'
        observed = []
        recorder = types.SimpleNamespace(current="A", model=types.SimpleNamespace(attr_names={"FIELD"}),
            record_object_attr=lambda obj, name: observed.append((obj.__name__, name)), record_comparison=lambda *args: None)
        with tempfile.TemporaryDirectory() as directory:
            instrumentation = Instrumentation(Path(directory), recorder)
            instrumentation.install()
            try:
                transformed = ast.fix_missing_locations(Transformer("m.py", {"FIELD"}).visit(ast.parse(source)))
                result = {}
                exec(compile(transformed, "m.py", "exec"), result)
                self.assertEqual(result["Base"].f(), 3)
                self.assertIn(("Base", "FIELD"), observed)
            finally:
                instrumentation.restore()

    def test_shadowed_getattr_retains_original_behavior(self):
        source = 'def f(getattr):\n    return getattr(3, "FIELD")\n'
        recorder = types.SimpleNamespace(current="A", model=types.SimpleNamespace(attr_names={"FIELD"}),
            record_object_attr=lambda *args: self.fail("shadowed function observed as builtin"), record_comparison=lambda *args: None)
        with tempfile.TemporaryDirectory() as directory:
            instrumentation = Instrumentation(Path(directory), recorder)
            instrumentation.install()
            try:
                result = {}
                exec(compile(ast.fix_missing_locations(Transformer("m.py", {"FIELD"}).visit(ast.parse(source))), "m.py", "exec"), result)
                self.assertEqual(result["f"](lambda a,b: (a,b)), (3,"FIELD"))
            finally:
                instrumentation.restore()


class TraceIntegrationTests(unittest.TestCase):
    def test_real_baseline_observations_drive_boundary_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sqlglot").mkdir()
            (root / "tests").mkdir()
            text = "def f(a):\n    return a < 3\n"
            (root / "sqlglot/core.py").write_text(text)
            model = SourceModel(root)
            recorder = Recorder(root, model, None)
            recorder.tests = ["below", "equal", "above"]
            instrumentation = Instrumentation(root, recorder)
            instrumentation.install()
            try:
                scope = {}
                transformed = ast.fix_missing_locations(Transformer("sqlglot/core.py", model.attr_names).visit(ast.parse(text)))
                exec(compile(transformed, str(root / "sqlglot/core.py"), "exec"), scope)
                for name, value in [("below", 2), ("equal", 3), ("above", 4)]:
                    recorder.current = name
                    scope["f"](value)
                recorder.current = None
            finally:
                instrumentation.restore()
            class Coverage:
                def measured_files(self):
                    return [str(root / "sqlglot/core.py")]
                def contexts_by_lineno(self, filename):
                    return {2: recorder.tests}
            path = root / "map.json"
            write_map(root, model, recorder, Coverage(), path)
            catalog = [{"number": i, "id": name, "seconds": .01} for i, name in enumerate(recorder.tests)]
            selector = Selector(catalog, root, path)
            selected = selector.select(diff("sqlglot/core.py", text, text.replace("<", "<=")))
            self.assertEqual(selected, 2)

    def test_constant_reference_uses_actual_line_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sqlglot").mkdir()
            (root / "tests").mkdir()
            text = "VALUE = 1\ndef f(flag):\n    if flag:\n        return VALUE\n    return 0\n"
            path = root / "sqlglot/core.py"
            path.write_text(text)
            model = SourceModel(root)
            recorder = Recorder(root, model, None)
            recorder.tests = ["uses_value", "other_branch"]
            class Coverage:
                def measured_files(self): return [str(path)]
                def contexts_by_lineno(self, filename):
                    return {3: recorder.tests, 4: ["uses_value"], 5: ["other_branch"]}
            mapping = root / "map.json"
            write_map(root, model, recorder, Coverage(), mapping)
            catalog = [{"number": i, "id": name, "seconds": .01} for i, name in enumerate(recorder.tests)]
            selector = Selector(catalog, root, mapping)
            self.assertEqual(selector.select(diff("sqlglot/core.py", text, text.replace("VALUE = 1", "VALUE = 2"))), 1)


class NativeProbeTests(unittest.TestCase):
    def run_probe(self, test_source, overlay="VALUE = 2\n", wall=1.0):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tests").mkdir()
            (root / "tests/__init__.py").write_text("")
            (root / "subject.py").write_text("VALUE = 1\n")
            (root / "tests/test_subject.py").write_text(test_source)
            catalog = [{"id": "tests/test_subject.py::Case::test_value", "number": 0, "seconds": .01}]
            return verify(root, {"subject.py": overlay}, catalog, wall_seconds=wall)[0]

    def test_overlay_failure_is_retained(self):
        passed = self.run_probe("import unittest\nimport subject\nclass Case(unittest.TestCase):\n def test_value(self):\n  self.assertEqual(subject.VALUE, 1)\n")
        self.assertEqual(passed, [])

    def test_confirmed_pass_is_removed(self):
        passed = self.run_probe("import unittest\nimport subject\nclass Case(unittest.TestCase):\n def test_value(self):\n  self.assertEqual(subject.VALUE, 1)\n", "VALUE = 3 - 2\n")
        self.assertEqual(passed, ["tests/test_subject.py::Case::test_value"])

    def test_failed_subtest_is_retained(self):
        passed = self.run_probe("import unittest\nimport subject\nclass Case(unittest.TestCase):\n def test_value(self):\n  for n in [1, 2]:\n   with self.subTest(n=n):\n    self.assertNotEqual(subject.VALUE, n)\n")
        self.assertEqual(passed, [])

    def test_class_teardown_error_invalidates_passes(self):
        passed = self.run_probe("import unittest\nclass Case(unittest.TestCase):\n def test_value(self):\n  pass\n @classmethod\n def tearDownClass(cls):\n  raise ValueError('teardown')\n")
        self.assertEqual(passed, [])

    def test_timeout_retains_selection(self):
        passed = self.run_probe("import unittest\nclass Case(unittest.TestCase):\n def test_value(self):\n  while True: pass\n", wall=.1)
        self.assertEqual(passed, [])


if __name__ == "__main__":
    unittest.main()
