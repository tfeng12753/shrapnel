"""Schedule-aware admission: a probe runs only if dropping its tests pays for its CPU."""
import hashlib
import json
from pathlib import Path
import py_compile
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import executor_fixtures as fixtures
import executor_plans as plans
import isolated_pytest
import selector as S
from selector import REVISION, Selector, pays_for_itself, schedule_saving, schedule_seconds
from tools.mutants import diff


def catalog(*seconds):
    return [{"id": f"tests/test_t.py::T::test_{i}", "number": i, "seconds": s} for i, s in enumerate(seconds)]


class HelperTests(TestCase):
    def test_saving_uses_the_final_schedule(self):
        tests = catalog(20, 16, 2, 2)
        # Both short tests fit beside the 16 s test: the 20 s test sets the schedule.
        self.assertEqual(schedule_saving(tests, 0b1100), 0)
        self.assertEqual(schedule_seconds(tests), 24)
        # The sum of the dropped durations is not the saving.
        self.assertEqual(schedule_saving(catalog(30, 1, 1, 1), 0b1110), 2)
        self.assertEqual(schedule_saving(tests, 0b1111), 24)
        self.assertEqual(schedule_saving([], 0), 0)

    def test_admission_rule(self):
        self.assertTrue(pays_for_itself(3.5, 2))
        self.assertFalse(pays_for_itself(3.49, 2))
        self.assertFalse(pays_for_itself(0.4, 0))
        self.assertFalse(pays_for_itself(float("nan"), 0))
        self.assertFalse(pays_for_itself(10, float("inf")))


class SetupAdmissionTests(TestCase):
    """A fixture edit whose class-setup members are droppable only after a setUpClass check."""

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.name = "tests/fixtures/data.sql"
        (self.root / self.name).parent.mkdir(parents=True)
        (self.root / self.name).write_text("a\nb\n")
        self.patch = diff(self.name, "a\nb\n", "a\nc\n")

    def select(self, seconds, readers, members, *, admission, verify=None):
        tests = catalog(*seconds)
        depmap = self.root / "map.json"
        depmap.write_text(json.dumps({"version": 1, "revision": REVISION, "complete": True,
            "tests": [t["id"] for t in tests],
            "files": {self.name: {"readers": f"{readers:x}", "setup_classes": {"tests/test_t.py::T": f"{members:x}"}}}}))
        selector = Selector(tests, self.root, depmap, setup_check_budget=8, schedule_admission=admission)
        with patch("native_probes.verify_setup", **(verify or {"side_effect": AssertionError("probe ran")})) as run:
            return selector.select(self.patch), run, selector

    def test_probe_with_no_schedule_saving_never_runs(self):
        # The members sum to 4 s, which the old rule probes, but they save nothing.
        mask, run, _ = self.select((20, 16, 2, 2), 0b0011, 0b1100, admission=True)
        self.assertEqual(mask, 0b1111)
        run.assert_not_called()
        mask, run, _ = self.select((20, 16, 2, 2), 0b0011, 0b1100, admission=False,
                                   verify={"return_value": (True, 1.6)})
        self.assertEqual(mask, 0b0011)
        run.assert_called_once()

    def test_profitable_probe_runs_and_records_its_cpu(self):
        for ok, expected in ((True, 0b0001), (False, 0b1111)):
            with self.subTest(ok=ok):
                mask, run, selector = self.select((2, 0.5, 10, 10, 10), 0b0001, 0b1110, admission=True,
                                                  verify={"return_value": (ok, 1.25)})
                self.assertEqual(mask, expected)
                run.assert_called_once()
                self.assertEqual(selector.expected_cpu("setup", "tests/test_t.py::T"), 1.25)
                self.assertEqual(selector.setup_check_budget, 8 - 1.25)

    def test_measured_cpu_can_stop_later_probes(self):
        mask, run, selector = self.select((2, 0.5, 10, 10, 10), 0b0001, 0b1110, admission=True,
                                          verify={"return_value": (True, 1.25)})
        selector.record("setup", "tests/test_t.py::T", 30)
        with patch("native_probes.verify_setup", side_effect=AssertionError("probe ran")):
            self.assertEqual(selector.select(self.patch), 0b1111)

    def test_members_kept_by_another_file_are_not_counted_as_saving(self):
        other = "tests/fixtures/other.sql"
        (self.root / other).write_text("x\n")
        tests = catalog(2, 10, 10)
        depmap = self.root / "map.json"
        depmap.write_text(json.dumps({"version": 1, "revision": REVISION, "complete": True,
            "tests": [t["id"] for t in tests],
            "files": {self.name: {"readers": "1", "setup_classes": {"tests/test_t.py::T": "6"}},
                      other: {"readers": "6"}}}))
        selector = Selector(tests, self.root, depmap, setup_check_budget=8, schedule_admission=True)
        with patch("native_probes.verify_setup", side_effect=AssertionError("probe ran")):
            self.assertEqual(selector.select(self.patch + diff(other, "x\n", "y\n")), 7)


class FixtureAdmissionTests(TestCase):
    """The executor fixture check keeps its selections when it is deferred to the end of the patch."""

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.name = "tests/fixtures/work.sql"
        self.contents = "run|select 1|A\noff|select 2|B\n"
        (self.root / self.name).parent.mkdir(parents=True)
        (self.root / self.name).write_text(self.contents)
        suite = self.root / "tests/test_executor.py"
        suite.write_text("""
class TestExecutor:
    def setUpClass(cls):
        cls.tpcds_sqls = list(load_sql_fixture_pairs("work.sql"))
        cls.tpch_sqls = list(load_sql_fixture_pairs("other.sql"))
""")
        helpers = self.root / "tests/helpers.py"
        helpers.write_text("""
from pathlib import Path
FIXTURES_DIR = Path(__file__).parent / 'fixtures'
def load_sql_fixture_pairs(path):
    for line in Path(path).read_text().splitlines():
        flag, sql, expected = line.split('|')
        yield {'execute': flag}, sql, expected
""")
        for key, path in (("SUITE_HASH", suite), ("LOADER_HASH", helpers)):
            self.enterContext(patch.object(fixtures, key, hashlib.sha256(path.read_bytes()).hexdigest()))
        self.map = self.root / "map.json"

    def select(self, after, seconds=(5.0, 5.0, 5.0)):
        tests = [{"id": name, "number": i, "seconds": s}
                 for i, (name, s) in enumerate(zip([plans.ITEM, fixtures.CLASS + "::other", "other::test"], seconds))]
        self.map.write_text(json.dumps({"version": 1, "revision": REVISION, "complete": True,
            "tests": [t["id"] for t in tests],
            "files": {self.name: {"readers": "5", "setup_classes": {fixtures.CLASS: "3"}}}}))
        selector = Selector(tests, self.root, self.map, executor_fixtures=True, schedule_admission=True)
        return selector.select(diff(self.name, self.contents, after))

    def test_same_selections_as_the_inline_check(self):
        self.assertEqual(self.select(self.contents.replace("|A", "|X")), 4)
        self.assertEqual(self.select(self.contents.replace("select 2", "select 3")), 5)
        # A broken loader proves nothing about class setup: keep the class.
        self.assertEqual(self.select(self.contents.replace("|A", "|A|B")), 7)

    def test_check_without_saving_is_skipped(self):
        with patch.object(fixtures, "fixture_effect", side_effect=AssertionError("probe ran")):
            # A 60 s reader sets the schedule; the class's two short tests fit beside it.
            self.assertEqual(self.select(self.contents.replace("|A", "|X"), (0.2, 0.2, 60.0)), 7)


class FinalSelectionProbeTests(TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.name = "sqlglot/optimizer/example.py"
        (self.root / self.name).parent.mkdir(parents=True)
        self.before = "def opt(x):\n    return x < 1\n"
        (self.root / self.name).write_text(self.before)
        self.mutation = diff(self.name, self.before, self.before.replace("<", "<="))

    def plan_selector(self, item_seconds, **options):
        tests = [{"id": plans.ITEM, "number": 0, "seconds": item_seconds},
                 {"id": "other::test", "number": 1, "seconds": 1.0}]
        depmap = self.root / "map.json"
        depmap.write_text(json.dumps({"version": 1, "revision": REVISION, "complete": True,
            "tests": [t["id"] for t in tests], "files": {self.name: {"lines": {"2": "3"}}}}))
        return Selector(tests, self.root, depmap, executor_plans=self.root, executor_plan_budget=20,
                        schedule_admission=True, **options)

    def test_plan_replay_needs_a_saving_that_pays_and_is_capped_at_break_even(self):
        with patch.object(plans, "matched", side_effect=AssertionError("probe ran")):
            # Dropping a 1 s item saves 1 s: less than the expected replay CPU.
            self.assertEqual(self.plan_selector(1.0).select(self.mutation), 3)
        with patch.object(plans, "matched", return_value=True) as replay:
            self.assertEqual(self.plan_selector(21.0).select(self.mutation), 2)
            # Saving 21 s: spend at most (21 - 0.5) / 1.5 s.
            self.assertLessEqual(replay.call_args.args[-1], (21 - .5) / 1.5)

    def test_lazy_check_runs_only_when_its_expected_cpu_pays(self):
        name = "sqlglot/sample.py"
        (self.root / name).write_text(self.before)
        lazy = "tests/dialects/test_dialect.py::TestDialect::test_lazy_load"
        tests = [{"id": lazy, "number": 0, "seconds": 0.368}, {"id": "other::test", "number": 1, "seconds": 1.0}]
        depmap = self.root / "map.json"
        depmap.write_text(json.dumps({"version": 1, "revision": REVISION, "complete": True,
            "tests": [t["id"] for t in tests], "external": "1", "files": {name: {"readers": "0"}}}))
        mutation = diff(name, self.before, self.before.replace("<", "<="))
        for cpu, runs, expected in ((1.35, False, 1), (0.9, True, 0)):
            with self.subTest(cpu=cpu), patch.dict(S.PRIOR_CPU, {"lazy": cpu}):
                selector = Selector(tests, self.root, depmap, lazy_test_budget=3, schedule_admission=True,
                                    import_closure={"tests": [lazy], "files": [name]})
                with patch("isolated_pytest.passed", return_value=True) as verify:
                    self.assertEqual(selector.select(mutation), expected)
                    self.assertEqual(verify.called, runs)

    def test_cost_aware_probe_uses_the_admission_rule(self):
        name = "sqlglot/subject.py"
        (self.root / name).write_text("VALUE = 1\n")
        patch_text = diff(name, "VALUE = 1\n", "VALUE = 2\n")
        tests = [{"id": "a", "number": 0, "seconds": .395}, {"id": "b", "number": 1, "seconds": .025}]
        for admission, runs in ((False, True), (True, False)):
            with self.subTest(admission=admission):
                selector = Selector(tests, self.root, self.root / "missing.json", schedule_admission=admission)
                selector.native = {"a", "b"}
                with patch("native_probes.verify", return_value=(["a", "b"], .1)) as run:
                    # Saving 2.42 s discounted to 1.55 s; expected CPU 1.075 s.
                    selector.probe(patch_text, 3, 2, cost_aware=True)
                    self.assertEqual(run.called, runs)


class StagingTests(TestCase):
    def test_patched_module_never_runs_from_cached_bytecode(self):
        with TemporaryDirectory() as directory:
            source, staged = Path(directory) / "src", Path(directory) / "staged"
            module = source / "sqlglot" / "value.py"
            module.parent.mkdir(parents=True)
            module.write_text("VALUE = 1\n")
            other = source / "sqlglot" / "other.py"
            other.write_text("OTHER = 1\n")
            for path in (module, other):
                py_compile.compile(str(path), invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
            isolated_pytest.stage(source, staged, "sqlglot/value.py", "VALUE = 2\n")
            cache = staged / "sqlglot" / "__pycache__"
            self.assertEqual([p.name.split(".")[0] for p in cache.iterdir()], ["other"])
            # Same size as the original: only the source can supply the new value.
            output = subprocess.run([sys.executable, "-B", "-c", "import sqlglot.value as v; print(v.VALUE)"],
                                    cwd=staged, capture_output=True, text=True, check=True).stdout
            self.assertEqual(output.strip(), "2")
            isolated_pytest.stage(source, Path(directory) / "trees", "sqlglot/value.py", "VALUE = 3\n", trees=("sqlglot",))
            self.assertEqual((Path(directory) / "trees/sqlglot/value.py").read_text(), "VALUE = 3\n")
