"""Collection errors in ground-truth runs: every catalog test of an erroring file fails."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
from ground_truth import PYTEST_ARGS, collection_errors, failing_tests, interrupted, result_record  # noqa: E402
from tools.mutant_eval import suspicion  # noqa: E402

NUMBER = {
    "tests/dialects/test_dax.py::TestDAX::test_evaluate": 159,
    "tests/dialects/test_dax.py::TestDAX::test_filter": 160,
    "tests/dialects/test_dax.py::TestDAX::test_order_by": 161,
    "tests/dialects/test_mysql.py::TestMySQL::test_alter_drop_column": 437,
    "tests/dialects/test_mysql.py::TestMySQL::test_analyze": 440,
    "tests/test_helper.py::TestHelper::test_merge_ranges": 1231,
    "tests/test_helper.py::TestHelper::test_to_int": 1233,
}
ERRORING = {159, 160, 161, 437, 440}

# Captured from pytest 9 (pytest -q -p no:cacheprovider -rfE) on a mutant that flips the
# base dialect's DPIPE_IS_STRING_CONCAT setting, run on two affected files and one
# unaffected file; tracebacks trimmed.
_ERRORS = """\

==================================== ERRORS ====================================
_________________ ERROR collecting tests/dialects/test_dax.py __________________
tests/dialects/test_dax.py:1: in <module>
    from tests.dialects.test_dialect import Validator
tests/dialects/test_dialect.py:16: in <module>
    from sqlglot.dialects.duckdb import WS_CONTROL_CHARS_TO_DUCK
sqlglot/parser.py:2195: in raise_error
    raise error
E   sqlglot.errors.ParseError: Expecting ). Line 5, Col: 70.
E     BETWEEN 0 AND 32767)) THEN NULL
________________ ERROR collecting tests/dialects/test_mysql.py _________________
tests/dialects/test_mysql.py:7: in <module>
    from tests.dialects.test_dialect import Validator
sqlglot/parser.py:2195: in raise_error
    raise error
E   sqlglot.errors.ParseError: Expecting ). Line 5, Col: 70.
E     BETWEEN 0 AND 32767)) THEN NULL
=========================== short test summary info ============================
ERROR tests/dialects/test_dax.py - sqlglot.errors.ParseError: Expecting ). Li...
ERROR tests/dialects/test_mysql.py - sqlglot.errors.ParseError: Expecting ). ...
"""
# Without --continue-on-collection-errors (exit 2): nothing ran.
ABORTED = _ERRORS + """\
!!!!!!!!!!!!!!!!!!! Interrupted: 2 errors during collection !!!!!!!!!!!!!!!!!!!!
2 errors in 0.95s
"""
# With --continue-on-collection-errors (exit 1): the unaffected file ran and passed.
CONTINUED = _ERRORS + "4 passed, 2 errors, 10 subtests passed in 0.94s\n"


class CollectionErrorTests(unittest.TestCase):
    def test_every_test_of_an_erroring_file_fails(self):
        for out in (ABORTED, CONTINUED):
            failing, unknown, lines = failing_tests(out, NUMBER)
            self.assertEqual(failing, ERRORING)
            self.assertEqual(unknown, set())
            self.assertEqual(len(lines), 2)

    def test_bare_error_line_without_message(self):
        self.assertEqual(failing_tests("ERROR tests/dialects/test_dax.py\n", NUMBER)[0], {159, 160, 161})

    def test_section_header_alone_is_enough(self):
        out = "________________ ERROR collecting tests/dialects/test_mysql.py _________________\nE   boom\n"
        self.assertEqual(failing_tests(out, NUMBER)[0], {437, 440})
        self.assertEqual(collection_errors(out), ["tests/dialects/test_mysql.py"])

    def test_directory_error_covers_its_files(self):
        self.assertEqual(failing_tests("ERROR tests/dialects - ImportError\n", NUMBER)[0], ERRORING)

    def test_file_prefix_does_not_match_other_files(self):
        number = {"tests/test_a.py::T::t": 0, "tests/test_a.pyx::T::t": 1}
        self.assertEqual(failing_tests("ERROR tests/test_a.py - E\n", number)[0], {0})

    def test_unmapped_collection_error_is_unknown(self):
        failing, unknown, _ = failing_tests("____ ERROR collecting tests/test_gone.py ____\n", NUMBER)
        self.assertEqual((failing, unknown), (set(), {"tests/test_gone.py"}))

    def test_interrupted_detection(self):
        self.assertEqual(interrupted(ABORTED), 2)
        self.assertIsNone(interrupted(CONTINUED))
        self.assertEqual(interrupted("!!!!! Interrupted: 1 error during collection !!!!!\n"), 1)

    def test_continued_run_is_trusted(self):
        record = result_record(CONTINUED, "", 1, NUMBER, 1.0)
        self.assertEqual(record["failing"], sorted(ERRORING))
        self.assertEqual(record["unknown"], [])
        self.assertEqual(record["collection_errors"], ["tests/dialects/test_dax.py", "tests/dialects/test_mysql.py"])
        self.assertEqual(suspicion(record), [])

    def test_aborted_run_keeps_failures_but_is_flagged(self):
        record = result_record(ABORTED, "", 2, NUMBER, 1.0)
        self.assertEqual(record["failing"], sorted(ERRORING))
        self.assertEqual(len(record["unknown"]), 1)
        self.assertIn("did not run", record["unknown"][0])
        self.assertTrue(suspicion(record))

    def test_pytest_keeps_running_other_files(self):
        self.assertIn("--continue-on-collection-errors", PYTEST_ARGS)


if __name__ == "__main__":
    unittest.main()
