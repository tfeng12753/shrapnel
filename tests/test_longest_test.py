import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from selector import REVISION, Selector
from tools.mutants import diff


class LongestTestChecks(TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.name = "sqlglot/example.py"
        self.before = "def check(x):\n    return x < 2\n"
        path = self.root / self.name
        path.parent.mkdir()
        path.write_text(self.before)
        self.patch = diff(self.name, self.before, self.before.replace("<", "<="))
        self.tests = [{"number": i, "seconds": seconds, "id": f"tests/test_a.py::A::test_{i}"}
                      for i, seconds in enumerate([12, .1])]
        self.map = self.root / "map.json"
        self.map.write_text(json.dumps({"version": 1, "revision": REVISION, "complete": True,
            "tests": [t["id"] for t in self.tests], "files": {self.name: {"lines": {"2": "3"}}}}))

    def test_full_pass_drops_only_one_item_and_every_attempt_costs_cpu(self):
        for passed, expected in ((True, 2), (False, 3)):
            selector = Selector(self.tests, self.root, self.map, longest_test_budget=5)
            with patch("isolated_pytest.passed", return_value=passed) as verify:
                self.assertEqual(selector.select(self.patch), expected)
                self.assertEqual(verify.call_args.args[3], self.tests[0]["id"])
                self.assertLessEqual(verify.call_args.args[-1], 2.5)
                self.assertLess(selector.longest_test_budget, 5)

    def test_disabled_and_mixed_edits_preserve_all_reasons(self):
        fixture = "tests/fixtures/sample.sql"
        path = self.root / fixture
        path.parent.mkdir(parents=True)
        path.write_text("SELECT 1\n")
        mapping = json.loads(self.map.read_text())
        mapping["files"][fixture] = {"readers": "1"}
        self.map.write_text(json.dumps(mapping))
        second = diff(fixture, "SELECT 1\n", "SELECT 2\n")
        with patch("isolated_pytest.passed") as verify, patch("fixture_replay.refine") as replay:
            self.assertEqual(Selector(self.tests, self.root, self.map).select(self.patch), 3)
            selector = Selector(self.tests, self.root, self.map, longest_test_budget=10,
                                fixture_entries={"tests": {}}, fixture_replay_budget=8)
            for mixed in (self.patch + second, second + self.patch):
                self.assertEqual(selector.select(mixed), 3)
            verify.assert_not_called()
            replay.assert_not_called()

    def test_real_pytest_item_sees_patched_source_and_checks_the_whole_test(self):
        test_dir = self.root / "tests"
        test_dir.mkdir()
        (self.root / self.name).with_name("__init__.py").touch()
        (test_dir / "__init__.py").touch()
        contents = ("from unittest import TestCase\nfrom sqlglot.example import check\n"
                    "class A(TestCase):\n    def test_0(self):\n"
                    "        self.assertTrue(check(0))\n        self.assertEqual(check(2), VALUE)\n")
        for expected_value, expected_mask in (("True", 2), ("False", 3)):
            (test_dir / "test_a.py").write_text(contents.replace("VALUE", expected_value))
            selector = Selector(self.tests, self.root, self.map, longest_test_budget=18)
            self.assertEqual(selector.select(self.patch), expected_mask)
