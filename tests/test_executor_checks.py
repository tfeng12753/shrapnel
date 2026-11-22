import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import executor_fixtures as fixtures
import executor_plans as plans
from selector import REVISION, Selector
from tools.mutants import diff


class FixtureSelectionTests(TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.name = "tests/fixtures/work.sql"
        self.contents = "run|select 1|A\noff|select 2|B\n"
        (self.root / self.name).parent.mkdir(parents=True)
        (self.root / self.name).write_text(self.contents)
        self.suite = self.root / "tests/test_executor.py"
        self.suite.write_text("""
class TestExecutor:
    def setUpClass(cls):
        cls.tpcds_sqls = list(load_sql_fixture_pairs("work.sql"))
        cls.tpch_sqls = list(load_sql_fixture_pairs("other.sql"))
""")
        self.helpers = self.root / "tests/helpers.py"
        self.helpers.write_text("""
from pathlib import Path
FIXTURES_DIR = Path(__file__).parent / 'fixtures'
def load_sql_fixture_pairs(path):
    for line in Path(path).read_text().splitlines():
        flag, sql, expected = line.split('|')
        yield {'execute': flag}, sql, expected
""")
        for key, path in (("SUITE_HASH", self.suite), ("LOADER_HASH", self.helpers)):
            self.enterContext(patch.object(fixtures, key, hashlib.sha256(path.read_bytes()).hexdigest()))
        self.catalog = [{"id": name, "number": i, "seconds": 5.0}
                        for i, name in enumerate([plans.ITEM, fixtures.CLASS + "::other", "other::test"])]
        self.map = self.root / "map.json"
        self.map.write_text(json.dumps({"version": 1, "revision": REVISION, "complete": True,
            "tests": [t["id"] for t in self.catalog],
            "files": {self.name: {"readers": "5", "setup_classes": {fixtures.CLASS: "3"}}}}))

    def select(self, before, after, *, enabled=True):
        selector = Selector(self.catalog, self.root, self.map, executor_fixtures=enabled)
        return selector.select(diff(self.name, before, after))

    def test_expected_only_edit_preserves_other_consumers(self):
        self.assertEqual(self.select(self.contents, self.contents.replace("|A", "|X")), 4)
        self.assertEqual(self.select(self.contents, self.contents.replace("|A", "|X"), enabled=False), 7)

    def test_changed_sql_metadata_and_order_keep_executor(self):
        for edited in [self.contents.replace("select 2", "select 3"), self.contents.replace("off", "run"),
                       "".join(reversed(self.contents.splitlines(keepends=True)))]:
            with self.subTest(edited=edited):
                self.assertEqual(self.select(self.contents, edited), 5)

    def test_broken_loader_or_changed_contract_falls_back_to_regular_setup_selection(self):
        self.assertEqual(self.select(self.contents, self.contents.replace("|A", "|A|B")), 7)
        self.suite.write_text(self.suite.read_text() + "# changed contract\n")
        self.assertEqual(self.select(self.contents, self.contents.replace("|A", "|X")), 7)

    def test_other_patch_cannot_lose_its_test_through_a_fixture_edit(self):
        (self.root / "unknown").write_text("old\n")
        other = diff("unknown", "old\n", "new\n")
        first = diff(self.name, self.contents, self.contents.replace("|A", "|X"))
        self.assertEqual(Selector(self.catalog, self.root, self.map, executor_fixtures=True).select(first + other), 7)


class PlanSelectionTests(TestCase):
    def test_only_a_complete_economical_match_removes_the_executor(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            name = "sqlglot/optimizer/example.py"
            (root / name).parent.mkdir(parents=True)
            before = "def opt(x):\n    return x < 1\n"
            (root / name).write_text(before)
            catalog = [{"id": plans.ITEM, "number": 0, "seconds": 21.0},
                       {"id": "other::test", "number": 1, "seconds": 1.0}]
            depmap = root / "map.json"
            depmap.write_text(json.dumps({"version": 1, "revision": REVISION, "complete": True,
                "tests": [t["id"] for t in catalog], "files": {name: {"lines": {"2": "3"}}}}))
            mutation = diff(name, before, before.replace("<", "<="))
            for match, expected in ((True, 2), (False, 3)):
                selector = Selector(catalog, root, depmap, executor_plans=root, executor_plan_budget=5)
                with patch.object(plans, "matched", return_value=match) as replay:
                    self.assertEqual(selector.select(mutation), expected)
                    self.assertLessEqual(replay.call_args.args[-1], 5)
            with patch.object(plans, "matched", side_effect=AssertionError("unbudgeted audit")):
                selector = Selector(catalog, root, depmap, executor_plans=root)
                self.assertEqual(selector.select(mutation), 3)

    def test_missing_snapshots_and_definition_time_edits_cannot_qualify(self):
        before = "def opt(x):\n    return x < 1\n"
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertFalse(plans.matched(root, "example.py", before, before.replace("<", "<="), 2, root, 5))
        self.assertFalse(plans.operator_edit("def opt(x=a or b):\n    pass\n", "def opt(x=a and b):\n    pass\n", 1))


class IsolatedImportTests(TestCase):
    def test_requires_only_one_external_item_on_one_package_edit_and_charges_failures(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            name = "sqlglot/sample.py"
            before = "def f(x):\n    return x < 1\n"
            (root / name).parent.mkdir()
            (root / name).write_text(before)
            lazy = "tests/dialects/test_dialect.py::TestDialect::test_lazy_load"
            tests = [{"id": lazy, "number": 0, "seconds": 0.368},
                     {"id": "other::test", "number": 1, "seconds": 1.0}]
            depmap = root / "map.json"
            mutation = diff(name, before, before.replace("<", "<="))
            for readers, passed, expected in (("0", True, 0), ("0", False, 1), ("2", True, 3)):
                depmap.write_text(json.dumps({"version": 1, "revision": REVISION, "complete": True,
                    "tests": [t["id"] for t in tests], "external": "1", "files": {name: {"readers": readers}}}))
                selector = Selector(tests, root, depmap, lazy_test_budget=3,
                                    import_closure={"tests": [lazy], "files": [name]})
                with patch("isolated_pytest.passed", return_value=passed) as verify:
                    self.assertEqual(selector.select(mutation), expected)
                    if readers == "2":
                        verify.assert_not_called()
                    else:
                        self.assertLess(selector.lazy_test_budget, 3)
                        self.assertEqual(verify.call_args.args[-1], 6.0)  # fixed, load-independent wall limit

    def test_junit_case_identity_skip_and_result_and_staged_source(self):
        import isolated_pytest
        from unittest.mock import MagicMock
        with TemporaryDirectory() as directory:
            source = Path(directory)
            name = "sqlglot/a.py"
            (source / "sqlglot").mkdir()
            (source / name).write_text("x = 1\n")
            for case, skip, expected in (("test_a", 0, True), ("test_b", 0, False), ("test_a", 1, False)):
                def launch(cmd, *, cwd, **kwargs):
                    self.assertEqual((cwd / name).read_text(), "x = 2\n")
                    out = next(arg[11:] for arg in cmd if arg.startswith("--junitxml="))
                    Path(out).write_text(f'<testsuite skipped="{skip}"><testcase classname="tests.test_a.A" '
                                         f'name="{case}"/></testsuite>')
                    child = MagicMock()
                    child.wait.return_value = 0
                    child.__enter__.return_value = child
                    return child
                with patch.object(isolated_pytest.subprocess, "Popen", side_effect=launch), patch("os.killpg"):
                    self.assertIs(isolated_pytest.passed(source, name, "x = 2\n",
                                  "tests/test_a.py::A::test_a", 5), expected)
            self.assertEqual((source / name).read_text(), "x = 1\n")
