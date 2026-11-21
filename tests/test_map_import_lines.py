from pathlib import Path
import sys
import tempfile
import unittest

from tools.import_closure import observation


class ImportTraceTests(unittest.TestCase):
    def test_executed_and_raw_reads(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            package = root / "sqlglot"
            package.mkdir()
            (package / "__init__.py").write_text("def later():\n    return 42\nopen(__file__).close()\nclass Dialect:\n    _classes = {0}\n")
            data = observation(root)
            name = "sqlglot/__init__.py"
            self.assertIn(name, data["files"])
            self.assertEqual(data["lines"][name], [1, 3, 4, 5])
            self.assertIn(name, data["explicit_reads"])
            self.assertNotIn(name, data["unentered_functions"])

    @unittest.skipUnless(hasattr(sys, "monitoring"), "requires CPython monitoring")
    def test_unentered_excludes_called_inspected_and_suspended_functions(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            package = root / "sqlglot"
            package.mkdir()
            (package / "__init__.py").write_text(
                "def unused(): return 1\n"
                "def called(): return 2\n"
                "def inspected(): return 3\n"
                "def suspended(): yield 4\n"
                "import threading\n"
                "thread = threading.Thread(target=called)\n"
                "thread.start(); thread.join()\n"
                "observed = inspected.__code__.co_code\n"
                "class Dialect: _classes = {0}\n"
            )
            data = observation(root)
            self.assertEqual(data["unentered_functions"]["sqlglot/__init__.py"], [["unused", 1]])

    def test_subprocess_disables_function_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            package = root / "sqlglot"
            package.mkdir()
            (package / "__init__.py").write_text(
                "import subprocess, sys\n"
                "def unused(): return 1\n"
                "subprocess.run([sys.executable, '-c', 'pass'], check=True)\n"
                "class Dialect: _classes = {0}\n"
            )
            self.assertEqual(observation(root)["unentered_functions"], {})
