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
    "tests/test_optimizer.py::TestOptimizer::test_tpch": 4,
}

# Captured from pytest 9.2.0 in the selector image.
STDOUT = """\
tests/test_sub.py:6: AssertionError
=========================== short test summary info ============================
FAILED tests/test_sub.py::TestX::test_plain - AssertionError: 1 != 2
SUBFAILED[case 1] tests/test_sub.py::TestX::test_sub - AssertionError: 1 == 1
2 failed, 1 passed, 2 subtests passed in 0.02s
"""

# Captured from pytest 9 on a TPC fixture mutant: the subtest title is a multi-line
# SQL entry, so the test id only appears on the title's last row (trimmed).
MULTILINE = """\
=========================== short test summary info ============================
SUBFAILED[7, select
        supp_nation,
        cust_nation,
from
        (
                select
                        n1.n_name as supp_nation
        ) as shipping
order by
        supp_nation,
        l_year] tests/test_optimizer.py::TestOptimizer::test_tpch - AssertionError: 'SELE[571 chars]OR "n_name"."n_name" = \\'GERMANY\\'\\n  )\\n  ...
1 failed, 1 passed, 166 deselected, 21 subtests passed in 1.99s
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

    def test_multiline_subtest_title(self):
        failing, unknown, lines = failing_tests(MULTILINE, NUMBER)
        self.assertEqual((failing, unknown), ({4}, set()))
        self.assertEqual(len(lines), 1)
        self.assertIn("tests/test_optimizer.py::TestOptimizer::test_tpch", lines[0])

    def test_unterminated_multiline_title_is_unknown(self):
        out = "SUBFAILED[7, select\n  a,\nFAILED tests/test_sub.py::TestX::test_plain - boom\n"
        failing, unknown, _ = failing_tests(out, NUMBER)
        self.assertEqual((failing, unknown), ({0}, {"SUBFAILED[7, select"}))

    def test_unmapped_ids_are_reported(self):
        failing, unknown, _ = failing_tests("FAILED tests/test_other.py::T::t - boom\n", NUMBER)
        self.assertEqual((failing, unknown), (set(), {"tests/test_other.py::T::t"}))

    def test_progress_and_passing_lines_are_ignored(self):
        out = "........F... [ 10%]\nPASSED tests/test_sub.py::TestX::test_plain\n1 passed in 0.1s\n"
        self.assertEqual(failing_tests(out, NUMBER)[0], set())


if __name__ == "__main__":
    unittest.main()
