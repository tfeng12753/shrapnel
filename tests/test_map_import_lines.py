from pathlib import Path
import tempfile
import unittest

from tools.import_closure import observation


class ImportTraceTests(unittest.TestCase):
    def test_executed_and_raw_reads(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            package = root / "sqlglot"
            package.mkdir()
            (package / "__init__.py").write_text("def later():\n    return 42\nopen(__file__).close()\n")
            data = observation(root)
            name = "sqlglot/__init__.py"
            self.assertIn(name, data["files"])
            self.assertEqual(data["lines"][name], [1, 3])
            self.assertIn(name, data["explicit_reads"])
