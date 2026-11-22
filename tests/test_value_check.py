"""Comparison-edit detection and evidence evaluation for recorded operands (standard library only)."""
import ast
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import value_check as v  # noqa: E402

OLD = '''def f(args, x):
    if len(args) < 2:
        return 1
    if x == "a" and len(args) > 0:
        return 2
    return x != -1
'''


def edit(new):
    return v.comparison_edit(ast.parse(OLD), ast.parse(new))


def mask(*numbers):
    result = 0
    for n in numbers:
        result |= 1 << n
    return format(result, "x")


class ComparisonEditTests(unittest.TestCase):
    def test_operator_swap(self):
        self.assertEqual(edit(OLD.replace("< 2", "<= 2")), ("2:7", "Lt", "LtE", None, None, None))

    def test_literal_change(self):
        self.assertEqual(edit(OLD.replace("< 2", "< 3")), ("2:7", "Lt", "Lt", 1, 2, 3))
        self.assertEqual(edit(OLD.replace('x == "a"', 'x == "b"')), ("4:7", "Eq", "Eq", 1, "a", "b"))
        self.assertEqual(edit(OLD.replace("x != -1", "x != -2")), ("6:11", "NotEq", "NotEq", 1, -1, -2))

    def test_operator_and_literal(self):
        self.assertEqual(edit(OLD.replace("< 2", "<= 3")), ("2:7", "Lt", "LtE", 1, 2, 3))

    def test_other_edits_are_ineligible(self):
        for new in (OLD.replace("len(args) < 2", "len(x) < 2"),       # non-literal operand
                    OLD.replace("< 2", "is 2"),                        # identity
                    OLD.replace("< 2", "< 2.0"),                       # same value, other type: still a change
                    OLD.replace("return 1", "return 3"),               # not a comparison
                    OLD.replace("< 2", "<= 2").replace("> 0", ">= 0"),  # two comparisons
                    OLD.replace("< 2", "< y"),
                    OLD):
            with self.subTest(new=new):
                result = edit(new)
                if new == OLD.replace("< 2", "< 2.0"):
                    self.assertEqual(result, ("2:7", "Lt", "Lt", 1, 2, 2.0))
                else:
                    self.assertIsNone(result)


def record(info, tests=4, failed=(), ran=None, global_sites=(), external=None, outside_spawn=True):
    data = {"tests": [f"t{i}" for i in range(tests)], "failed": list(failed),
            "ran": list(range(tests)) if ran is None else ran, "global": list(global_sites),
            "outside_spawn": outside_spawn, **({} if external is None else {"external": external}),
            "files": {"mod.py": {"2:7": info}}}
    return v.Record(data, {f"t{i}": 1 << i for i in range(tests)})


class EvaluateTests(unittest.TestCase):
    def test_strict_to_loose_ordering_keeps_only_tests_that_saw_equality(self):
        info = {"op": "Lt", "reached": mask(0, 1, 2), "unknown": "0",
                "states": {"p:2": mask(0, 1), "p:4": mask(0), "p:1": mask(2)}, "pairs": {}}
        kept = v.evaluate(record(info), "mod.py", ("2:7", "Lt", "LtE", None, None, None), 0b111)
        self.assertEqual(kept, 0b100)

    def test_unknown_and_unreached_coverage_users_are_kept(self):
        info = {"op": "Lt", "reached": mask(0, 1), "unknown": mask(1),
                "states": {"p:2": mask(0)}, "pairs": {}}
        kept = v.evaluate(record(info), "mod.py", ("2:7", "Lt", "LtE", None, None, None), 0b1011)
        self.assertEqual(kept, 0b1010)

    def test_tests_after_the_first_changed_test_are_kept(self):
        info = {"op": "Lt", "reached": mask(0, 1, 2), "unknown": "0",
                "states": {"p:2": mask(0, 2), "p:1": mask(1)}, "pairs": {}}
        kept = v.evaluate(record(info), "mod.py", ("2:7", "Lt", "LtE", None, None, None), 0b111)
        self.assertEqual(kept, 0b110)

    def test_equality_states_cannot_decide_ordering(self):
        info = {"op": "Eq", "reached": mask(0), "unknown": "0", "states": {"e:0": mask(0)}, "pairs": {}}
        self.assertEqual(v.evaluate(record(info), "mod.py", ("2:7", "Eq", "Lt", None, None, None), 0b1), 0b1)
        info["op"] = "NotEq"
        self.assertEqual(v.evaluate(record(info), "mod.py", ("2:7", "NotEq", "Eq", None, None, None), 0b1), 0b1)

    def test_literal_change_uses_recorded_pairs(self):
        info = {"op": "Lt", "reached": mask(0, 1, 2), "unknown": "0",
                "states": {"p:2": mask(0, 1), "p:4": mask(2)},
                "pairs": {"0": [["i", 0, "i", 2]], "1": [["i", 1, "i", 2], ["i", 2, "i", 2]], "2": [["i", 5, "i", 2]]}}
        # x < 2 -> x < 3: only a value of 2 changes the outcome.
        kept = v.evaluate(record(info), "mod.py", ("2:7", "Lt", "Lt", 1, 2, 3), 0b111)
        self.assertEqual(kept, 0b110)

    def test_literal_change_without_pairs_or_with_mismatched_literal_is_kept(self):
        info = {"op": "Lt", "reached": mask(0, 1), "unknown": "0", "states": {"p:2": mask(0, 1)},
                "pairs": {"1": [["i", 0, "i", 7]]}}
        self.assertEqual(v.evaluate(record(info), "mod.py", ("2:7", "Lt", "Lt", 1, 2, 3), 0b11), 0b11)

    def test_type_change_of_literal(self):
        info = {"op": "Eq", "reached": mask(0), "unknown": "0", "states": {"p:0": mask(0)},
                "pairs": {"0": [["s", "b", "s", "a"]]}}
        self.assertEqual(v.evaluate(record(info), "mod.py", ("2:7", "Eq", "Eq", 1, "a", 3), 0b1), 0)
        info["pairs"] = {"0": [["s", "a", "s", "a"]]}
        self.assertEqual(v.evaluate(record(info), "mod.py", ("2:7", "Eq", "Eq", 1, "a", "c"), 0b1), 0b1)

    def test_failed_unrun_and_global_sites(self):
        info = {"op": "Lt", "reached": mask(0), "unknown": "0", "states": {"p:2": mask(0)}, "pairs": {}}
        edit = ("2:7", "Lt", "LtE", None, None, None)
        self.assertEqual(v.evaluate(record(info, failed=[0]), "mod.py", edit, 0b1), 0b1)
        self.assertEqual(v.evaluate(record(info, ran=[]), "mod.py", edit, 0b1), 0b1)
        self.assertIsNone(v.evaluate(record(info, global_sites=["mod.py:2:7"]), "mod.py", edit, 0b1))

    def test_operator_mismatch_has_no_evidence(self):
        info = {"op": "Gt", "reached": mask(0), "unknown": "0", "states": {}, "pairs": {}}
        self.assertIsNone(v.evaluate(record(info), "mod.py", ("2:7", "Lt", "LtE", None, None, None), 0b1))

    def test_fully_observed_tests_without_evaluations_are_dropped(self):
        info = {"op": "Lt", "reached": mask(0), "unknown": "0", "states": {"p:2": mask(0)}, "pairs": {}}
        edit = ("2:7", "Lt", "LtE", None, None, None)
        # Tests 1-3 cover the line; 3 started a process and 2 did not run in the recording.
        rec = record(info, ran=[0, 1, 3], external=[3], outside_spawn=False)
        self.assertEqual(v.evaluate(rec, "mod.py", edit, 0b1111), 0b1100)
        # A process started outside any test's call phase may have served any test.
        rec = record(info, ran=[0, 1, 3], external=[3], outside_spawn=True)
        self.assertEqual(v.evaluate(rec, "mod.py", edit, 0b1111), 0b1110)
        # A failed test is never evidence.
        rec = record(info, ran=[0, 1, 3], external=[3], outside_spawn=False, failed=[1])
        self.assertEqual(v.evaluate(rec, "mod.py", edit, 0b1111), 0b1110)

    def test_relation_outcomes(self):
        self.assertTrue(v.relation_outcome("p:1", "LtE"))
        self.assertFalse(v.relation_outcome("p:1", "Lt"))
        self.assertTrue(v.relation_outcome("p:9", "GtE"))
        self.assertIsNone(v.relation_outcome("e:1", "Lt"))
        self.assertIsNone(v.relation_outcome("x:1", "Eq"))
        self.assertTrue(v.relation_outcome("e:0", "NotEq"))


if __name__ == "__main__":
    unittest.main()
