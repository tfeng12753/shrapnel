import importlib.util
import os
from pathlib import Path
import py_compile
import shutil
import subprocess
import sys
import tempfile
import unittest

from tools.import_closure import prepare_bytecode
from tools.map_import_replay import staging_exclusions


class PreparedBytecodeTests(unittest.TestCase):
    def test_same_size_same_timestamp_change_reaches_real_child(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, stage = root / "pinned", root / "patched"
            pkg = source / "sqlglot"
            pkg.mkdir(parents=True)
            (pkg / "__init__.py").write_text("value = 1\n")
            prepare_bytecode(source)
            cached = Path(importlib.util.cache_from_source(str(pkg / "__init__.py")))
            self.assertEqual(int.from_bytes(cached.read_bytes()[4:8], "little"), 3)
            before = pkg / "old.py"
            before.write_text("value = 0\n")
            timestamp_cache = Path(py_compile.compile(str(before), doraise=True,
                                  invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP))
            shutil.copytree(source, stage, ignore=staging_exclusions(source))
            self.assertFalse((stage / timestamp_cache.relative_to(source)).exists())
            self.assertTrue((stage / cached.relative_to(source)).exists())
            ignore_changed = staging_exclusions(source, ["sqlglot/__init__.py"])
            self.assertIn(cached.name, ignore_changed(str(cached.parent), [cached.name]))
            path = stage / "sqlglot" / "__init__.py"
            stamp = path.stat()
            path.write_text("value = 2\n")
            os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            output = subprocess.check_output([sys.executable, "-B", "-c", "import sqlglot; print(sqlglot.value)"],
                                             cwd=stage, text=True)
            self.assertEqual(output.strip(), "2")
