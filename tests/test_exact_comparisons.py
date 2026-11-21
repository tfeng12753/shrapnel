"""Value-operator narrowing from exact comparison evidence (synthetic maps).

Bits: each test is one bit. The site is the comparison on line 2 of m.py.
"""
import json
from pathlib import Path
import tempfile
import unittest

from selector import REVISION, Selector, resolve_settings
from test_advanced import diff

OLD = "def f(x):\n    return len(x) == 1\n"
SITE = "2:11"


class ExactComparisonTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.tests = [{"number": n, "id": str(n), "seconds": .01} for n in range(8)]

    def select(self, new, *, exact, states=None, unknown="0", lines=None, op="Eq", old=OLD, enabled=True,
               relations=True, site=SITE):
        (self.root / "m.py").write_text(old)
        info = {"op": op, "states": states or {}, "unknown": unknown}
        if exact is not None:
            info["exact"] = exact
        mapping = self.root / "map.json"
        mapping.write_text(json.dumps({
            "version": 1, "revision": REVISION, "complete": True, "tests": [t["id"] for t in self.tests],
            "files": {"m.py": {"lines": lines or {"2": "ff"}, "comparisons": {site: info}}},
        }))
        selector = Selector(self.tests, self.root, mapping, relations=relations, exact_comparisons=enabled)
        return selector.select(diff("m.py", old, new))

    # Tests 0-3 completed every evaluation with int operands:
    #   0: len 2 (gt), 1: len 1 (eq, small-int identity), 2: len 0 (lt), 3: len 2 and len 1.
    EXACT = {"complete": "f", "states": {"p:4": "9", "p:9": "a", "p:2": "4"}}

    def test_eq_to_lt_drops_only_tests_with_the_same_outcome_everywhere(self):
        # 0 stays False under both operators; 3 saw an equal pair once.
        self.assertEqual(self.select(OLD.replace("==", "<"), exact=self.EXACT), 0xfe)
        # Under >, test 2 keeps False (0 > 1) but runs after test 0, which changes.
        self.assertEqual(self.select(OLD.replace("==", ">"), exact=self.EXACT), 0xff)
        # Under !=, every outcome flips.
        self.assertEqual(self.select(OLD.replace("==", "!="), exact=self.EXACT), 0xff)
        # Under >=, 1 >= 1 and 0 >= 1 keep their outcomes; 2 >= 1 flips. With the
        # flipping test running last, the two safe tests before it are dropped.
        first = {"complete": "7", "states": {"p:9": "1", "p:2": "2", "p:4": "4"}}
        self.assertEqual(self.select(OLD.replace("==", ">="), exact=first), 0xfc)
        # Under >, test 0 (1 > 1) flips first, so nothing after it is dropped.
        self.assertEqual(self.select(OLD.replace("==", ">"), exact=first), 0xff)

    def test_safe_tests_after_a_changing_test_are_kept(self):
        # A counter guarded by the comparison: test 0 changes its outcome and
        # leaves a different count for test 1, whose own evaluation is safe.
        old = "count = 0\ndef bump(limit):\n    if count < limit:\n        return 1\n"
        exact = {"complete": "3", "states": {"p:9": "1", "p:2": "2"}}
        self.assertEqual(self.select(old.replace("<", "<="), exact=exact, old=old, op="Lt", site="3:7",
                                     lines={"3": "3"}), 3)
        # The same safe test running first may be dropped.
        exact = {"complete": "3", "states": {"p:2": "1", "p:9": "2"}}
        self.assertEqual(self.select(old.replace("<", "<="), exact=exact, old=old, op="Lt", site="3:7",
                                     lines={"3": "3"}), 2)

    def test_disabled_missing_or_non_strict_evidence_does_not_narrow(self):
        new = OLD.replace("==", "<")
        self.assertEqual(self.select(new, exact=self.EXACT, enabled=False), 0xff)
        self.assertEqual(self.select(new, exact=None), 0xff)
        self.assertEqual(self.select(new, exact={"complete": "0", "states": {}}), 0xff)

    def test_incomplete_evaluations_keep_the_test(self):
        # Test 0 reached the site but some evaluation raised, or an operand was
        # a user-defined object or a bool: it is not in "complete".
        exact = {"complete": "e", "states": {"p:4": "9", "p:9": "a", "p:2": "4"}}
        self.assertEqual(self.select(OLD.replace("==", "<"), exact=exact), 0xff)

    def test_unknown_observations_keep_the_test(self):
        self.assertEqual(self.select(OLD.replace("==", "<"), exact=self.EXACT, unknown="1"), 0xff)

    def test_complete_test_without_any_exact_state_is_kept(self):
        exact = {"complete": "f", "states": {"p:9": "a", "p:2": "4"}}
        self.assertEqual(self.select(OLD.replace("==", "<"), exact=exact), 0xff)

    def test_state_outside_the_format_keeps_its_tests(self):
        for state in ("p:6", "p:3", "x:4", "p:"):
            with self.subTest(state=state):
                exact = {"complete": "f", "states": {"p:4": "9", state: "1", "p:9": "2", "p:2": "4"}}
                # Test 0 also has a valid unchanged state but must be kept, and
                # test 3, safe on its own, runs after it.
                self.assertEqual(self.select(OLD.replace("==", "<"), exact=exact), 0xff)

    def test_unorderable_operands_keep_tests_for_ordering_swaps(self):
        # None == 1 (e:0): ordering would raise TypeError instead of returning.
        exact = {"complete": "1", "states": {"e:0": "1"}}
        self.assertEqual(self.select(OLD.replace("==", "<"), exact=exact, lines={"2": "1"}), 1)
        # Equality-to-inequality always flips the outcome.
        self.assertEqual(self.select(OLD.replace("==", "!="), exact=exact, lines={"2": "1"}), 1)
        # None == None (e:9) never orders.
        exact = {"complete": "1", "states": {"e:9": "1"}}
        self.assertEqual(self.select(OLD.replace("==", "<="), exact=exact, lines={"2": "1"}), 1)

    def test_identity_swaps_never_use_exact_evidence(self):
        self.assertEqual(self.select(OLD.replace("==", "is"), exact=self.EXACT), 0xff)
        self.assertEqual(self.select(OLD.replace("==", "is not"), exact=self.EXACT), 0xff)

    def test_tests_covering_the_line_without_exact_evidence_are_kept(self):
        # Tests 4-7 executed line 2 but have no comparison record at all.
        self.assertEqual(self.select(OLD.replace("==", "<"), exact=self.EXACT, lines={"2": "f0"}), 0xfe)

    def test_multi_line_comparison_keeps_users_of_every_line(self):
        old = "def f(x):\n    return (len(x)\n            == 1)\n"
        new = old.replace("==", "<")
        self.assertEqual(self.select(new, exact=self.EXACT, old=old, site="2:12",
                                     lines={"2": "f", "3": "30"}), 0x3e)

    def test_chained_mixed_and_repeated_edits_never_use_exact_evidence(self):
        chained = "def f(x):\n    return 0 < len(x) == 1\n"
        self.assertEqual(self.select(chained.replace("==", "<"), exact=self.EXACT, old=chained), 0xff)
        mixed = OLD.replace("== 1", "< 2")
        self.assertEqual(self.select(mixed, exact=self.EXACT), 0xff)
        twice = "def f(x):\n    return len(x) == 1 or len(x) == 3\n"
        self.assertEqual(self.select(twice.replace("==", "<"), exact=self.EXACT, old=twice), 0xff)

    def test_module_level_comparison_selects_everything(self):
        old = "FLAG = len('a') == 1\n"
        self.assertEqual(self.select(old.replace("==", "<"), exact=self.EXACT, old=old, site="1:7",
                                     lines={"1": "1"}), 0xff)

    def test_config_key_round_trip(self):
        self.assertFalse(resolve_settings(["--config", str(self.root / "missing.json")])["exact_comparisons"])
        self.assertTrue(resolve_settings(["--exact-comparisons"])["exact_comparisons"])


if __name__ == "__main__":
    unittest.main()
