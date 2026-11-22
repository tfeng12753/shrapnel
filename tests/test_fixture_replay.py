import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fixture_replay  # noqa: E402
from selector import parse_patch  # noqa: E402

OLD = '''import os

LIMIT = 3


def helper(x, y=1):
    total = x + y
    if total > LIMIT:
        return total
    return 0


class Thing:
    FLAG = True

    @staticmethod
    def method(a):
        def inner(b):
            return b * 2
        return inner(a) + 1
'''


def patch_for(old, new, path="sqlglot/mod.py"):
    import difflib
    rows = difflib.unified_diff(old.splitlines(True), new.splitlines(True), f"a/{path}", f"b/{path}", n=3)
    return f"diff --git a/{path} b/{path}\n" + "".join(rows)


def change_for(old, new):
    return parse_patch(patch_for(old, new))[0]


class ChangedFunctionsTests(unittest.TestCase):
    def spans(self, new):
        return fixture_replay.changed_functions(change_for(OLD, new), OLD, new)

    def test_body_edit_is_confined_to_the_function(self):
        spans = self.spans(OLD.replace("if total > LIMIT:", "if total >= LIMIT:"))
        self.assertEqual(spans, [(7, 10)])

    def test_insertion_inside_a_body(self):
        spans = self.spans(OLD.replace("    return 0\n", "    total += 1\n    return 0\n"))
        self.assertEqual(spans, [(7, 10)])

    def test_nested_function_uses_the_innermost_body(self):
        spans = self.spans(OLD.replace("return b * 2", "return b * 3"))
        self.assertEqual(spans, [(19, 19)])

    def test_default_argument_change_is_refused(self):
        self.assertIsNone(self.spans(OLD.replace("def helper(x, y=1):", "def helper(x, y=2):")))

    def test_module_level_change_is_refused(self):
        self.assertIsNone(self.spans(OLD.replace("LIMIT = 3", "LIMIT = 4")))

    def test_class_setting_change_is_refused(self):
        self.assertIsNone(self.spans(OLD.replace("FLAG = True", "FLAG = False")))

    def test_decorator_change_is_refused(self):
        self.assertIsNone(self.spans(OLD.replace("@staticmethod", "@classmethod")))

    def test_edit_spanning_body_and_module_level_is_refused(self):
        new = OLD.replace("    return 0\n\n\nclass Thing:", "    return 1\n\n\nclass Thing2:")
        self.assertIsNone(self.spans(new))


class EntryAttributionTests(unittest.TestCase):
    record = {"base": {"sqlglot/mod.py": [1, 3]},
              "lines": {"sqlglot/mod.py": {"7": [0, 2], "9": [2], "19": [5]}}}

    def test_entries_touching_the_body(self):
        self.assertEqual(fixture_replay.entries_for_lines(self.record, "sqlglot/mod.py", [(7, 10)]), {0, 2})

    def test_no_entry_touches_the_body(self):
        self.assertEqual(fixture_replay.entries_for_lines(self.record, "sqlglot/mod.py", [(12, 14)]), set())

    def test_setup_execution_refuses(self):
        self.assertIsNone(fixture_replay.entries_for_lines(self.record, "sqlglot/mod.py", [(2, 4)]))


def _double(x):
    return 2 * x


def _boom():
    raise KeyError("x")


_INIT = []


def _initializer(value):
    _INIT.append(value)


class InlineExecutorTests(unittest.TestCase):
    def test_results_and_exceptions(self):
        pool = fixture_replay.InlineExecutor(max_workers=4, mp_context=None)
        self.assertEqual(pool.submit(_double, 21).result(), 42)
        with self.assertRaises(KeyError):
            pool.submit(_boom).result()

    def test_unpicklable_argument_fails_like_a_process_pool(self):
        pool = fixture_replay.InlineExecutor()
        with self.assertRaises(Exception):
            pool.submit(_double, lambda: 1).result()

    def test_initializer_runs_with_copied_arguments(self):
        value = {"a": [1]}
        fixture_replay.InlineExecutor(initializer=_initializer, initargs=(value,))
        self.assertEqual(_INIT[-1], value)
        self.assertIsNot(_INIT[-1], value)


class FingerprintTests(unittest.TestCase):
    def setUp(self):
        self.module = types.ModuleType("fakepkg.state")
        self.module.CACHE = {"a": 1}
        self.module.os = types
        class Settings:
            FLAG = True
        Settings.__module__ = "fakepkg.state"
        self.module.Settings = Settings
        patcher = mock.patch.dict(sys.modules, {"fakepkg.state": self.module})
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(fixture_replay, "STATE_PACKAGES", ("fakepkg",))
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(fixture_replay, "STATE_MODULES", ())
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_stable_and_sensitive(self):
        start = fixture_replay.state_summary()
        self.assertEqual(start, fixture_replay.state_summary())
        unchanged = fixture_replay.state_fingerprint(start, fixture_replay.state_summary())
        self.module.CACHE["b"] = 2
        added = fixture_replay.state_fingerprint(start, fixture_replay.state_summary())
        self.assertNotEqual(unchanged, added)
        self.module.CACHE["b"] = 3
        self.assertNotEqual(added, fixture_replay.state_fingerprint(start, fixture_replay.state_summary()))
        del self.module.CACHE["b"]
        self.module.Settings.FLAG = False
        self.assertNotEqual(unchanged, fixture_replay.state_fingerprint(start, fixture_replay.state_summary()))

    def test_delta_ignores_state_present_before(self):
        start = fixture_replay.state_summary()
        self.module.CACHE["b"] = 2
        first = fixture_replay.state_fingerprint(start, fixture_replay.state_summary())
        self.module.OTHER = [1]
        later = fixture_replay.state_summary()
        self.module.CACHE["c"] = 2
        del self.module.CACHE["c"]
        self.assertNotEqual(first, fixture_replay.state_fingerprint(later, fixture_replay.state_summary()))
        self.assertEqual(fixture_replay.state_fingerprint(later, fixture_replay.state_summary()),
                         fixture_replay.state_fingerprint(start, start))

    def test_too_large_state_is_unknown(self):
        self.module.CACHE = {str(i): i for i in range(10)}
        with mock.patch.object(fixture_replay, "STATE_LIMIT", 5):
            self.assertIsNone(fixture_replay.state_summary())
        self.assertIsNone(fixture_replay.state_fingerprint(None, {}))


class IsolationTests(unittest.TestCase):
    def test_result_error_and_timeout(self):
        self.assertEqual(fixture_replay.run_isolated(lambda: [1, "x"], time.monotonic() + 10), [1, "x"])
        self.assertIsNone(fixture_replay.run_isolated(_boom, time.monotonic() + 10))
        self.assertIsNone(fixture_replay.run_isolated(lambda: time.sleep(5), time.monotonic() + .3))


class FixtureEntryDiffTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.source = Path(folder.name)
        (self.source / "tests/fixtures/opt").mkdir(parents=True)
        (self.source / "tests/helpers.py").write_text(
            "import os\nFIXTURES_DIR = os.path.join(os.path.dirname(__file__), 'fixtures')\n"
            "def load_pairs(filename):\n"
            "    with open(os.path.join(FIXTURES_DIR, filename)) as f:\n"
            "        statements = f.read().split(';')\n"
            "    for i in range(0, len(statements) - 1, 2):\n"
            "        yield statements[i].strip(), statements[i + 1].strip()\n")
        self.text = "SELECT 1;\nSELECT 1;\nSELECT 2;\nSELECT 2;\nSELECT 3;\nSELECT 3;\n"
        (self.source / "tests/fixtures/opt/f.sql").write_text(self.text)

    def entries(self, new):
        return fixture_replay.fixture_entries(self.source, "tests/fixtures/opt/f.sql", self.text, new, "load_pairs")

    def test_one_changed_entry(self):
        self.assertEqual(self.entries(self.text.replace("SELECT 2;\nSELECT 2;", "SELECT 2;\nSELECT 9;")), {1})

    def test_repaired_entries_are_all_affected(self):
        self.assertEqual(self.entries(self.text.replace("SELECT 2;\n", "", 1)), {1, 2})

    def test_changed_entry_count_is_refused(self):
        self.assertIsNone(self.entries(self.text.replace("SELECT 2;\nSELECT 2;\n", "")))


class FakeSelector:
    def __init__(self, tests):
        self.tests = tests
        self.test_bits = {t["id"]: 1 << t["number"] for t in tests}
        self.files, self.external, self.child_work = {}, 0, 0
        self.source = Path("/nonexistent")

    def chosen(self, mask):
        return [t for t in self.tests if mask & (1 << t["number"])]


class RefineTests(unittest.TestCase):
    def setUp(self):
        self.tests = [{"id": "t::A::heavy", "number": 0, "seconds": 20.0},
                      {"id": "t::A::light", "number": 1, "seconds": 0.5}]
        self.selector = FakeSelector(self.tests)
        self.data = {"import_cpu": .5, "setup_cpu": {"t::A": .5}, "tests": {
            "t::A::heavy": {"entry_cpu": [.1, .1, .1], "fingerprints": ["f0", "f1", "f2"], "entries": 3}}}
        patcher = mock.patch.object(fixture_replay, "plan", return_value={"t::A::heavy": [0, 2]})
        patcher.start()
        self.addCleanup(patcher.stop)

    def refine(self, outcome, budget=8.0):
        with mock.patch("native_probes._forked", return_value=(outcome, 1.0)) as forked:
            mask, _ = fixture_replay.refine(self.selector, self.data, 0b11, "sqlglot/x.py", {}, "", "", budget)
        return mask, forked

    def test_drops_only_when_every_affected_entry_verifies(self):
        mask, _ = self.refine([["t::A::heavy", 0, True, "f0"], ["t::A::heavy", 2, True, "f2"]])
        self.assertEqual(mask, 0b10)

    def test_failing_entry_keeps(self):
        mask, _ = self.refine([["t::A::heavy", 0, True, "f0"], ["t::A::heavy", 2, False, "f2"]])
        self.assertEqual(mask, 0b11)

    def test_state_difference_keeps(self):
        mask, _ = self.refine([["t::A::heavy", 0, True, "f0"], ["t::A::heavy", 2, True, "other"]])
        self.assertEqual(mask, 0b11)

    def test_missing_entry_keeps(self):
        mask, _ = self.refine([["t::A::heavy", 0, True, "f0"]])
        self.assertEqual(mask, 0b11)

    def test_unknown_outcome_keeps(self):
        mask, _ = self.refine(None)
        self.assertEqual(mask, 0b11)

    def test_unverifiable_recorded_state_keeps(self):
        self.data["tests"]["t::A::heavy"]["fingerprints"][2] = None
        mask, _ = self.refine([["t::A::heavy", 0, True, "f0"], ["t::A::heavy", 2, True, None]])
        self.assertEqual(mask, 0b11)

    def test_admission_requires_saving_over_cpu(self):
        self.tests[0]["seconds"] = 1.0
        mask, forked = self.refine([["t::A::heavy", 0, True, "f0"], ["t::A::heavy", 2, True, "f2"]])
        self.assertEqual(mask, 0b11)
        forked.assert_not_called()

    def test_budget_limits_admission(self):
        mask, forked = self.refine([["t::A::heavy", 0, True, "f0"], ["t::A::heavy", 2, True, "f2"]], budget=.5)
        self.assertEqual(mask, 0b11)
        forked.assert_not_called()

    def test_internal_errors_keep(self):
        with mock.patch.object(fixture_replay, "plan", side_effect=KeyError("boom")):
            mask, _ = fixture_replay.refine(self.selector, self.data, 0b11, "sqlglot/x.py", {}, "", "", 8.0)
        self.assertEqual(mask, 0b11)


if __name__ == "__main__":
    unittest.main()
