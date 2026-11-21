"""Comment-only edits above a "# type: ignore" comment must stay comment-only."""
import ast
import difflib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from selector import REVISION, Selector, classify_python_edit  # noqa: E402

SOURCE = "def f(a):\n    x = a + 1\n    return x\n\n\nY = g()  # type: ignore[attr-defined]\n"


def diff(name, old, new):
    return "diff --git a/{0} b/{0}\n".format(name) + "".join(
        difflib.unified_diff(old.splitlines(True), new.splitlines(True), "a/" + name, "b/" + name))


class TypeIgnoreShiftTests(unittest.TestCase):
    def test_classifier_ignores_shifted_type_ignore(self):
        new = SOURCE.replace("    x = a + 1\n", "    # Reviewed for clarity\n    x = a + 1\n")
        before, after = ast.parse(SOURCE, type_comments=True), ast.parse(new, type_comments=True)
        self.assertNotEqual(ast.dump(before), ast.dump(after))  # the TypeIgnore line number moved
        self.assertEqual(classify_python_edit(before, after), [])

    def test_type_ignore_tag_change_is_still_a_difference(self):
        new = SOURCE.replace("[attr-defined]", "[misc]")
        self.assertIsNone(classify_python_edit(ast.parse(SOURCE, type_comments=True), ast.parse(new, type_comments=True)))

    def test_comment_insertion_selects_only_file_readers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "m.py").write_text(SOURCE)
            tests = [{"number": i, "id": name, "file": "t.py", "seconds": .1} for i, name in enumerate("ABC")]
            # A and B execute f (lines 1-3); C reads the file's text.
            mapping = {"version": 1, "revision": REVISION, "complete": True, "tests": ["A", "B", "C"], "files": {
                "m.py": {"lines": {"2": "3", "3": "3", "6": "7"}, "regions": [[1, 3, "function", "3"]],
                         "executed": "7", "readers": "4"}}}
            (root / "map.json").write_text(json.dumps(mapping))
            patch = diff("m.py", SOURCE, SOURCE.replace("    x = a + 1\n", "    # Reviewed for clarity\n    x = a + 1\n"))
            self.assertEqual(Selector(tests, root, root / "map.json").select(patch), 4)


if __name__ == "__main__":
    unittest.main()
