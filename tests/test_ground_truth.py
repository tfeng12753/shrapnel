"""Parsing pytest's short summary into failing catalog tests (evaluation tooling)."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from ground_truth import failing_tests  # noqa: E402

NUMBER = {
    "tests/test_sub.py::TestX::test_plain": 0,
    "tests/test_sub.py::TestX::test_sub": 1,
    "tests/dialects/test_duckdb.py::TestDuckDB::test_duckdb": 2,
    "tests/dialects/test_duckdb.py::TestDuckDB::test_time": 3,
}

# Captured from pytest 9.2.0 in the submission image.
STDOUT = """\
tests/test_sub.py:6: AssertionError
=========================== short test summary info ============================
FAILED tests/test_sub.py::TestX::test_plain - AssertionError: 1 != 2
SUBFAILED[case 1] tests/test_sub.py::TestX::test_sub - AssertionError: 1 == 1
2 failed, 1 passed, 2 subtests passed in 0.02s
"""


class FailingTestsTests(unittest.TestCase):
    def test_plain_and_subtest_failures(self):
        failing, unknown, lines = failing_tests(STDOUT, NUMBER)
        self.assertEqual(failing, {0, 1})
        self.assertEqual(unknown, set())
        self.assertEqual(len(lines), 2)

    def test_subtest_title_with_spaces_and_brackets(self):
        out = "SUBFAILED[(execute) SELECT a [x] FROM t] tests/test_sub.py::TestX::test_sub - AssertionError\n"
        self.assertEqual(failing_tests(out, NUMBER)[0], {1})

    def test_collection_error_selects_every_test_in_the_file(self):
        out = "ERROR tests/dialects/test_duckdb.py - ImportError: cannot import name\n"
        self.assertEqual(failing_tests(out, NUMBER)[0], {2, 3})

    def test_unmapped_ids_are_reported(self):
        failing, unknown, _ = failing_tests("FAILED tests/test_other.py::T::t - boom\n", NUMBER)
        self.assertEqual((failing, unknown), (set(), {"tests/test_other.py::T::t"}))

    def test_progress_and_passing_lines_are_ignored(self):
        out = "........F... [ 10%]\nPASSED tests/test_sub.py::TestX::test_plain\n1 passed in 0.1s\n"
        self.assertEqual(failing_tests(out, NUMBER)[0], set())


if __name__ == "__main__":
    unittest.main()
