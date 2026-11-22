import difflib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import small_suite


class SmallSuiteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "sqlglot").mkdir()
        (self.root / "sqlglot/__init__.py").write_text("")
        (self.root / "tests").mkdir()
        (self.root / "tests/__init__.py").write_text("")
        self.name = "sqlglot/value.py"
        self.item = {"id": "tests/test_example.py::Example::test_value", "number": 0, "seconds": .1}
        self.selector = SimpleNamespace(complete=True, source=self.root, native={self.item["id"]},
                                        chosen=lambda mask: [self.item], read_source=lambda path: (self.root / path).read_text())

    def select(self, body):
        target = self.root / self.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("VALUE = 1\n")
        (self.root / "tests/test_example.py").write_text(
            "import unittest\nclass Example(unittest.TestCase):\n    def test_value(self):\n"
            + "".join(f"        {line}\n" for line in body.splitlines()))
        change = "".join(difflib.unified_diff(["VALUE = 1\n"], ["VALUE = 2\n"],
                                             "a/" + self.name, "b/" + self.name))
        return small_suite.refine(self.selector, "diff --git a/" + self.name + " b/" + self.name + "\n" + change, 1)

    def test_assertion_runs_on_patched_module(self):
        self.assertEqual(self.select("from sqlglot.value import VALUE\nself.assertEqual(VALUE, 2)"), 0)
        self.assertEqual(self.select("from sqlglot.value import VALUE\nself.assertEqual(VALUE, 1)"), 1)

    def test_skip_and_caught_subprocess_rejection_keep_selection(self):
        self.assertEqual(self.select("self.skipTest('unavailable')"), 1)
        self.assertEqual(self.select("import subprocess\ntry: subprocess.run(['true'])\nexcept RuntimeError: pass"), 1)

    def test_registry_edit_declines_stateful_verification(self):
        self.name = "sqlglot/dialects/dialect.py"
        with patch("small_suite._forked", side_effect=AssertionError("unexpected trial")):
            self.assertEqual(self.select("self.assertTrue(True)"), 1)
