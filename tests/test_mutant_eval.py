import unittest

from tools import mutant_eval as m


class HelperTests(unittest.TestCase):
    def test_split(self):
        self.assertTrue(m.in_split("m01007", "holdout"))
        self.assertFalse(m.in_split("m01007", "tune"))
        self.assertTrue(m.in_split("m01008", "tune"))
        self.assertTrue(m.in_split("anything", "all"))
        self.assertFalse(m.in_split("baseline", "holdout"))

    def test_summary(self):
        self.assertTrue(m.looks_like_pytest_summary("3 failed, 1200 passed, 2 skipped in 61.20s (0:01:01)"))
        self.assertTrue(m.looks_like_pytest_summary("1 error in 0.50s"))
        self.assertFalse(m.looks_like_pytest_summary("Traceback (most recent call last):"))

    def test_suspicion(self):
        ok = {"failing": [3], "unknown": [], "exit": 1, "summary": "1 failed, 9 passed in 1.00s"}
        self.assertEqual(m.suspicion(ok), [])
        self.assertEqual(m.suspicion({"failing": [], "unknown": [], "exit": 0, "summary": "9 passed in 1.00s"}), [])
        self.assertTrue(m.suspicion({**ok, "unknown": ["tests/x.py"]}))
        self.assertTrue(m.suspicion({"failing": [], "unknown": [], "exit": 4, "summary": "ERROR: not found"}))

    def test_oracle(self):
        tests = [{"number": 0, "seconds": 1.0}, {"number": 1, "seconds": 0.5}]
        duration = {0: 1000, 1: 500}
        by_number = {t["number"]: t for t in tests}
        self.assertEqual(m.oracle_milliseconds([], by_number, duration), 0)
        self.assertEqual(m.oracle_milliseconds([0, 1], by_number, duration), 3500)


if __name__ == "__main__":
    unittest.main()
