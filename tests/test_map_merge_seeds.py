import unittest

from tools.map_merge_seeds import merge


def build(complete, states, unknown, lines):
    return {"revision": "r", "complete": True, "tests": ["a", "b", "c"], "external": "0", "native": ["a"],
            "files": {"f.py": {"lines": lines, "anchors": {}, "regions": [[1, 2, "function", lines.get("1", "0")]],
                               "readers": "0", "executed": "0",
                               "exact_comparisons": {"1:4": {"op": "Eq", "complete": complete, "states": states,
                                                             "unknown": unknown}}}}}


class MergeSeedsTest(unittest.TestCase):
    def test_union_coverage_and_intersect_complete(self):
        # Test a (bit 1) is complete in both with the same state; test b (bit 2) is
        # complete in both but with different states; test c (bit 4) is complete in one.
        first = build("7", {"p:1": "3", "p:2": "4"}, "0", {"1": "3"})
        second = build("3", {"p:1": "1", "p:2": "2"}, "4", {"1": "5"})
        merged = merge([first, second])["files"]["f.py"]
        self.assertEqual(merged["lines"], {"1": "7"})
        self.assertEqual(merged["regions"], [[1, 2, "function", "7"]])
        record = merged["exact_comparisons"]["1:4"]
        self.assertEqual(record["complete"], "1")
        self.assertEqual(record["states"], {"p:1": "3", "p:2": "6"})
        self.assertEqual(record["unknown"], "6")

    def test_site_missing_under_one_seed_is_dropped(self):
        first = build("1", {"p:1": "1"}, "0", {})
        second = build("1", {"p:1": "1"}, "0", {})
        del second["files"]["f.py"]["exact_comparisons"]
        self.assertNotIn("exact_comparisons", merge([first, second])["files"]["f.py"])


if __name__ == "__main__":
    unittest.main()
