"""TypedDict total= flips: classification and narrowing behind typeddict_state_check."""
import difflib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch as mock_patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from selector import REVISION, Selector, import_hazard_kind  # noqa: E402

TYPING = '''import typing as t


class Args(t.TypedDict, total=False):
    pretty: bool
    indent: int


class Child(Args, total=False):
    comments: bool
'''


def diff(name, old, new):
    return "diff --git a/{0} b/{0}\n".format(name) + "".join(
        difflib.unified_diff(old.splitlines(True), new.splitlines(True), "a/" + name, "b/" + name))


class ClassificationTests(unittest.TestCase):
    def test_total_flip(self):
        self.assertEqual(import_hazard_kind(TYPING, TYPING.replace("class Args(t.TypedDict, total=False)",
                                                                    "class Args(t.TypedDict, total=True)")), "typeddict_total")

    def test_plain_class_keyword_is_structural(self):
        old = "class A(Base, flag=False):\n    pass\n"
        self.assertEqual(import_hazard_kind(old, old.replace("flag=False", "flag=True")), "structural")

    def test_total_flip_with_another_change_is_structural(self):
        new = TYPING.replace("total=False):\n    pretty", "total=True):\n    pretty").replace("import typing as t", "import typing as tt\nt = tt")
        self.assertEqual(import_hazard_kind(TYPING, new), "structural")

    def test_non_boolean_total_is_structural(self):
        self.assertEqual(import_hazard_kind(TYPING, TYPING.replace("class Args(t.TypedDict, total=False)",
                                                                    "class Args(t.TypedDict, total=flag)")), "structural")


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "lib").mkdir()
        (self.root / "lib/_typing.py").write_text(TYPING)
        (self.root / "tests").mkdir()
        (self.root / "tests/test_x.py").write_text("def test_x():\n    pass\n")
        self.tests = [{"number": i, "id": f"tests/test_x.py::T::t{i}", "file": "tests/test_x.py", "seconds": .1} for i in range(3)]
        mapping = {"version": 1, "revision": REVISION, "complete": True, "tests": [t["id"] for t in self.tests],
                   "files": {"lib/_typing.py": {"lines": {"4": "1"}, "regions": [], "executed": "7", "readers": "0"}}}
        (self.root / "map.json").write_text(json.dumps(mapping))
        self.patch = diff("lib/_typing.py", TYPING, TYPING.replace("class Args(t.TypedDict, total=False)", "class Args(t.TypedDict, total=True)"))

    def tearDown(self):
        self.tmp.cleanup()

    def select(self, enabled):
        selector = Selector(self.tests, self.root, self.root / "map.json", relations=True, import_check_budget=6.0,
                            typeddict_state_check=enabled)
        with mock_patch.object(Selector, "import_succeeds", return_value=True):
            return selector.select(self.patch)

    def test_disabled_selects_everything(self):
        self.assertEqual(self.select(False), 0b111)

    def test_enabled_keeps_only_readers_and_external(self):
        # Line 4 (the class header) has a user, but a verified metadata-only change can't affect it.
        self.assertEqual(self.select(True), 0)

    def test_metadata_reader_in_checkout_keeps_everything(self):
        (self.root / "tests/test_x.py").write_text("def test_x():\n    assert Args.__required_keys__\n")
        self.assertEqual(self.select(True), 0b111)

    def test_failed_import_keeps_everything(self):
        selector = Selector(self.tests, self.root, self.root / "map.json", relations=True, typeddict_state_check=True)
        with mock_patch.object(Selector, "import_succeeds", return_value=False):
            self.assertEqual(selector.select(self.patch), 0b111)


if __name__ == "__main__":
    unittest.main()
