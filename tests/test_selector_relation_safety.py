import json
from pathlib import Path
import tempfile
import unittest

from selector import REVISION, Selector, comparison_changes, relation_outcome
from test_advanced import diff
from instrument import relation


class RelationSafetyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.tests = [{"number": n, "id": str(n), "seconds": .01} for n in range(3)]

    def selector(self, source, lines, *, states=None, unknown="0", site=None, op="Is", relations=True):
        (self.root / "m.py").write_text(source)
        entry = {"lines": lines}
        if site:
            entry["comparisons"] = {site: {"op": op, "states": states or {}, "unknown": unknown}}
        mapping = self.root / "map.json"
        mapping.write_text(json.dumps({
            "version": 1, "revision": REVISION, "complete": True, "tests": ["0", "1", "2"],
            "files": {"m.py": entry},
        }))
        return Selector(self.tests, self.root, mapping, relations=relations)

    def test_exact_states_and_unknown_encodings(self):
        self.assertTrue(relation_outcome("e:9", "Is"))
        self.assertTrue(relation_outcome("e:9", "Eq"))
        self.assertIsNone(relation_outcome("e:9", "Lt"))
        # Identical NaN objects can compare unequal and unordered.
        self.assertTrue(relation_outcome("p:8", "Is"))
        self.assertFalse(relation_outcome("p:8", "Eq"))
        self.assertFalse(relation_outcome("p:8", "LtE"))
        for state in ("", "p:", "numeric:1", "p:3", "p:99", "p:-1", "e:2", "p:1:4", None):
            with self.subTest(state=state):
                self.assertIsNone(relation_outcome(state, "Eq"))
                self.assertIsNone(relation_outcome(state, "Is"))

    def test_custom_equality_and_missing_observations_keep_tests(self):
        class Overloaded:
            def __eq__(self, other):
                raise AssertionError("operand observer invoked custom comparison")

        self.assertIsNone(relation(Overloaded(), None))
        self.assertEqual(relation(None, None), "e:9")
        old = "def f(x):\n    return x is None\n"
        new = old.replace("is None", "== None")
        selector = self.selector(old, {"2": "7"}, site="2:11", states={"e:9": "1"}, unknown="2")
        self.assertEqual(selector.select(diff("m.py", old, new)), 6)

    def test_one_unsupported_state_keeps_a_test_with_multiple_observations(self):
        old = "def f(x):\n    return x is None\n"
        new = old.replace("is None", "== None")
        selector = self.selector(old, {"2": "1"}, site="2:11", states={"e:0": "1", "bad:1": "1"})
        self.assertEqual(selector.select(diff("m.py", old, new)), 1)

    def test_old_ordering_can_raise_without_leaving_an_observation(self):
        class PartialOrder:
            def __lt__(self, other):
                raise ValueError("old operator")

            def __le__(self, other):
                return True

        old = "def f(x):\n    return x < 3\n"
        new = old.replace("<", "<=")
        old_scope, new_scope = {}, {}
        exec(old, old_scope)
        exec(new, new_scope)
        self.assertTrue(old_scope["f"](2))
        self.assertTrue(new_scope["f"](2))
        with self.assertRaises(ValueError):
            old_scope["f"](PartialOrder())
        self.assertTrue(new_scope["f"](PartialOrder()))
        # v1 cannot distinguish a test that ran only the integer invocation
        # from one that also caught the exception from the custom operand.
        selector = self.selector(old, {"2": "1"}, site="2:11", states={"p:2": "1"}, op="Lt")
        self.assertEqual(selector.select(diff("m.py", old, new)), 1)

    def test_comprehensions_and_lambda_use_exact_site_and_unknown_mask(self):
        for expression in ("[x is None for x in items]", "lambda x: x is None"):
            with self.subTest(expression=expression):
                old = f"def f(items):\n    return {expression}\n"
                new = old.replace("is None", "== None")
                site, _, _ = comparison_changes(old, new)[0]
                selector = self.selector(old, {"2": "3"}, site=site, states={"e:9": "1"}, unknown="2")
                self.assertEqual(selector.select(diff("m.py", old, new)), 2)

    def test_comparison_in_a_default_can_affect_collection(self):
        old = "def f(x=(value is None)):\n    pass\n"
        new = old.replace("is None", "== None")
        site, _, _ = comparison_changes(old, new)[0]
        selector = self.selector(old, {"1": "1"}, site=site, states={"e:0": "1"})
        self.assertEqual(selector.select(diff("m.py", old, new)), 7)
        mixed = new.replace("pass", "return 1")
        self.assertEqual(selector.select(diff("m.py", old, mixed)), 7)

    def test_comparison_in_shared_setup_keeps_all_tests(self):
        old = "class Case:\n    def setUpClass(cls):\n        flag = value is None\n"
        new = old.replace("is None", "== None")
        site, _, _ = comparison_changes(old, new)[0]
        selector = self.selector(old, {"3": "1"}, site=site, states={"e:0": "1"})
        self.assertEqual(selector.select(diff("m.py", old, new)), 7)

    def test_multiline_operands_preserve_unattributed_users(self):
        old = "def f(x):\n    return (\n        x\n        is\n        None\n    )\n"
        new = old.replace("        is\n", "        ==\n")
        site, _, _ = comparison_changes(old, new)[0]
        selector = self.selector(old, {"3": "3", "5": "4"}, site=site, states={"e:0": "1"})
        self.assertEqual(selector.select(diff("m.py", old, new)), 6)

    def test_chain_and_mixed_edit_keep_multiline_operand_coverage(self):
        old = "def f(x):\n    return (\n        x\n        is\n        None\n    )\n"
        for before, after in [
            (old.replace("None\n", "None is True\n"), old.replace("        is\n", "        ==\n")
                .replace("None\n", "None is True\n")),
            (old + "\ndef g():\n    return 1\n",
             old.replace("        is\n", "        ==\n") + "\ndef g():\n    return 0\n"),
        ]:
            with self.subTest(before=before):
                self.assertIsNone(comparison_changes(before, after))
                selector = self.selector(before, {"3": "3", "5": "4"})
                self.assertEqual(selector.select(diff("m.py", before, after)), 7)

    def test_comparison_with_an_edit_in_another_file_uses_coverage(self):
        old = "def f(x):\n    return x is None\n"
        new = old.replace("is None", "== None")
        selector = self.selector(old, {"2": "1"}, site="2:11", states={"e:9": "1"})
        other = "def g(x):\n    return x + 1\n"
        (self.root / "other.py").write_text(other)
        selector.files["other.py"] = {"lines": {2: 2}, "anchors": {}, "regions": [],
                                      "comparisons": {}, "readers": 0, "executed": 2}
        self.assertEqual(selector.select(diff("m.py", old, new)), 0)
        combined = diff("m.py", old, new) + diff("other.py", other, other.replace("+ 1", "+ 2"))
        self.assertEqual(selector.select(combined), 3)
