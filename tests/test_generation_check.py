"""Static eligibility and planning of the generated-code check (standard library only)."""
import hashlib
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import generation_check as g  # noqa: E402

NAME = "sqlglot/generators/python.py"
OLD = '''import itertools

CACHE = {}


def _rename(self, e):
    values = list(e.args.values())
    if len(values) == 1:
        return self.func(e.key, values[0])
    return self.func(e.key, *values)


def _helper(x, y=1):
    return x + y


class Gen:
    TRANSFORMS = {
        "is": lambda self, e: self.binary(e, "!=" if e.args.get("negate") else "=="),
        "bool": lambda self, e: "True" if e.this else "False",
    }

    def slice_sql(self, expression):
        step = self.sql(expression, "step")
        parts = (p for p in expression.parts if p)
        return f"{step}:{list(parts)}"

    @staticmethod
    def decorated(a):
        return a + 1
'''


def keys(new):
    return g.eligible_keys(NAME, OLD, new)


class EligibleKeysTests(unittest.TestCase):
    def test_function_body_edit(self):
        self.assertEqual(keys(OLD.replace("if len(values) == 1:", "if len(values) != 1:")),
                         {f"{NAME}:6:_rename"})

    def test_lambda_body_in_class_dict(self):
        self.assertEqual(keys(OLD.replace('"!=" if e.args', '"==" if e.args')), {f"{NAME}:19:<lambda>"})

    def test_generator_expression_element(self):
        self.assertEqual(keys(OLD.replace("for p in expression.parts if p", "for p in expression.parts if not p")),
                         {f"{NAME}:25:<genexpr>"})

    def test_generator_expression_first_iterable_belongs_to_the_enclosing_function(self):
        self.assertEqual(keys(OLD.replace("in expression.parts if p", "in expression.other if p")),
                         {f"{NAME}:23:slice_sql"})

    def test_default_argument_is_not_a_body(self):
        self.assertIsNone(keys(OLD.replace("def _helper(x, y=1):", "def _helper(x, y=2):")))

    def test_new_dict_entry_is_not_a_body(self):
        self.assertIsNone(keys(OLD.replace('"bool": lambda', '"null": lambda *_: "None",\n        "bool": lambda')))

    def test_module_constant_is_not_a_body(self):
        self.assertIsNone(keys(OLD.replace("CACHE = {}", "CACHE = {1: 2}")))

    def test_decorated_functions_are_ineligible(self):
        self.assertIsNone(keys(OLD.replace("return a + 1", "return a + 2")))

    def test_global_writes_are_ineligible(self):
        self.assertIsNone(keys(OLD.replace("    return x + y", "    CACHE[x] = y\n    return x + y")))
        self.assertIsNone(keys(OLD.replace("    return x + y", "    global CACHE\n    CACHE = 1\n    return x + y")))

    def test_parameter_writes_are_ineligible(self):
        self.assertIsNone(keys(OLD.replace("        step = self.sql", "        self.flag = 1\n        step = self.sql")))

    def test_local_writes_are_allowed(self):
        new = OLD.replace("    return x + y", "    seen = {}\n    seen[x] = y\n    return x + y")
        self.assertEqual(keys(new), {f"{NAME}:13:_helper"})

    def test_banned_calls_are_ineligible(self):
        self.assertIsNone(keys(OLD.replace("return x + y", "return setattr(x, 'a', y)")))
        self.assertIsNone(keys(OLD.replace("return x + y", "return x.__dict__")))

    def test_identical_and_unparsable_files(self):
        self.assertIsNone(keys(OLD))
        self.assertIsNone(keys(OLD + "def broken(:\n"))

    def test_float_and_bool_constants_differ(self):
        self.assertEqual(keys(OLD.replace("y=1):\n    return x + y", "y=1):\n    return x + True")),
                         {f"{NAME}:13:_helper"})

    def test_compiled_keys_match_static_keys(self):
        compiled = g.compiled_keys(NAME, OLD)
        for key in (f"{NAME}:6:_rename", f"{NAME}:19:<lambda>", f"{NAME}:20:<lambda>",
                    f"{NAME}:25:<genexpr>", f"{NAME}:23:slice_sql", f"{NAME}:13:_helper"):
            self.assertEqual(compiled.get(key), 1, key)


class EquivalenceTests(unittest.TestCase):
    def test_text_and_syntax_tree_equivalence(self):
        self.assertTrue(g.equivalent("DIV(a, b)", "DIV(a, b)"))
        self.assertTrue(g.equivalent("DIV(a, b)", "DIV(a,b)"))
        self.assertTrue(g.equivalent("(a)", "a"))
        self.assertFalse(g.equivalent("a == None", "a != None"))
        self.assertFalse(g.equivalent("a", "a)"))


def selector_for(old, readers=0):
    tests = {"tests/test_executor.py::TestExecutor::test_execute_tpcds": 1 << 1,
             "tests/test_executor.py::TestExecutor::test_execute_tpch": 1 << 2}
    return SimpleNamespace(test_bits=tests, files={NAME: {"readers": readers}}, external=0, child_work=0,
                           source=Path("/nonexistent"))


def record(old, inside, outside, tpcds_keys):
    return {"checkout": {NAME: hashlib.sha256(old.encode()).hexdigest()},
            "inside": inside, "outside": outside,
            "tests": {"tests/test_executor.py::TestExecutor::test_execute_tpcds":
                      {"calls": [["{}", "x", []]], "keys": set(tpcds_keys), "replay_cpu": .5, "state": "s"},
                      "tests/test_executor.py::TestExecutor::test_execute_tpch":
                      {"calls": [], "keys": set()}}}


class PlanTests(unittest.TestCase):
    def setUp(self):
        audited = mock.patch.object(g, "_audited_files_match", return_value=True)
        audited.start()
        self.addCleanup(audited.stop)

    def plan(self, data, new, mask=0b110, readers=0):
        return g.plan(selector_for(OLD, readers), data, mask, NAME, OLD, new)

    def test_code_run_inside_calls_is_replayed_and_unrun_code_is_not(self):
        key = f"{NAME}:6:_rename"
        data = record(OLD, [key], [], [key])
        new = OLD.replace("if len(values) == 1:", "if len(values) != 1:")
        self.assertEqual(self.plan(data, new), {"tests/test_executor.py::TestExecutor::test_execute_tpcds": True,
                                                "tests/test_executor.py::TestExecutor::test_execute_tpch": False})

    def test_code_run_outside_calls_keeps_everything(self):
        key = f"{NAME}:6:_rename"
        new = OLD.replace("if len(values) == 1:", "if len(values) != 1:")
        self.assertEqual(self.plan(record(OLD, [], [key], []), new), {})

    def test_unselected_and_source_reading_tests_are_not_candidates(self):
        key = f"{NAME}:6:_rename"
        new = OLD.replace("if len(values) == 1:", "if len(values) != 1:")
        self.assertEqual(self.plan(record(OLD, [key], [], [key]), new, mask=0b10, readers=0b10), {})
        self.assertEqual(list(self.plan(record(OLD, [key], [], [key]), new, mask=0b10)),
                         ["tests/test_executor.py::TestExecutor::test_execute_tpcds"])

    def test_pinned_file_mismatch_keeps_everything(self):
        key = f"{NAME}:6:_rename"
        data = record(OLD, [key], [], [key])
        data["checkout"][NAME] = "0" * 64
        self.assertEqual(self.plan(data, OLD.replace("== 1:", "!= 1:")), {})

    def test_other_trees_are_ignored(self):
        data = record(OLD, [], [], [])
        self.assertEqual(g.plan(selector_for(OLD), data, 0b110, "tests/helpers.py", OLD, OLD + "\n"), {})


class RefineTests(unittest.TestCase):
    def setUp(self):
        audited = mock.patch.object(g, "_audited_files_match", return_value=True)
        audited.start()
        self.addCleanup(audited.stop)
        self.key = f"{NAME}:6:_rename"
        self.new = OLD.replace("if len(values) == 1:", "if len(values) != 1:")

    def refine(self, outcome, saving=20.0, budget=4.0):
        data = record(OLD, [self.key], [], [self.key])
        with mock.patch("native_probes._forked", return_value=(outcome, .5)) as forked:
            mask, _ = g.refine(selector_for(OLD), data, 0b110, NAME, None, OLD, self.new, budget,
                               lambda mask, drop: saving)
        return mask, forked.called

    def test_verified_replay_drops_the_test(self):
        mask, called = self.refine({"tests/test_executor.py::TestExecutor::test_execute_tpcds": True})
        self.assertEqual(mask, 0)
        self.assertTrue(called)

    def test_failed_or_missing_replay_keeps_the_test(self):
        for outcome in ({"tests/test_executor.py::TestExecutor::test_execute_tpcds": False}, {}, None, []):
            with self.subTest(outcome=outcome):
                mask, _ = self.refine(outcome)
                self.assertEqual(mask, 0b10)  # The unrun test is still dropped.

    def test_small_saving_skips_the_replay(self):
        mask, called = self.refine({"tests/test_executor.py::TestExecutor::test_execute_tpcds": True}, saving=.5)
        self.assertEqual(mask, 0b10)
        self.assertFalse(called)

    def test_budget_below_expected_cpu_skips_the_replay(self):
        mask, called = self.refine({"tests/test_executor.py::TestExecutor::test_execute_tpcds": True}, budget=.3)
        self.assertEqual(mask, 0b10)
        self.assertFalse(called)


if __name__ == "__main__":
    unittest.main()
