import json
import tempfile
import unittest
from pathlib import Path

from tools.map_executor_plan import checkout_digest, digest
from tools.map_executor_replay import check, operator_edit
from tools.mutants import diff


class ExecutorAuditTests(unittest.TestCase):
    def test_only_edits_inside_ordinary_function_bodies(self):
        for before, after, line, expected in [
            ("def f(x):\n    return not x\n", "def f(x):\n    return x\n", 2, True),
            ("def f(x):\n    return x < 1\n", "def f(x):\n    return x <= 1\n", 2, True),
            ("def f(x):\n    return x < 1\n", "def f(x):\n    return x <= 2\n", 2, False),
            ("x = a or b\n", "x = a and b\n", 1, False),
            ("def f(\n x=a or b):\n    pass\n", "def f(\n x=a and b):\n    pass\n", 2, False),
            ("def f():\n    def g(): return a or b\n", "def f():\n    def g(): return a and b\n", 2, False),
        ]:
            with self.subTest(before=before):
                self.assertEqual(operator_edit(before, after, line), expected)

    def test_downstream_or_stale_baseline_retains_test(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            name = "sqlglot/optimizer/example.py"
            target = root / name
            target.parent.mkdir(parents=True)
            source = "def f(x):\n    return x < 1\n"
            target.write_text(source)
            patch = diff(name, source, source.replace("<", "<="))
            reference = {"queries": [], "context": []}
            (root / "plan-base-1.json").write_text(json.dumps(reference))
            proof = root / "plan-deps-1.json"
            evidence = {"checkout": checkout_digest(root), "snapshot": digest(reference),
                        "modules": ["sqlglot/executor/python.py", name]}
            proof.write_text(json.dumps(evidence))
            self.assertIn("downstream dependency", check(root, patch, root, "suite", "schema"))
            evidence["snapshot"] = "stale"
            proof.write_text(json.dumps(evidence))
            self.assertIn("mismatched baseline", check(root, patch, root, "suite", "schema"))

    def test_checkout_signature_tracks_changed_table_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "tests" / "fixtures" / "table.csv.gz"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"old")
            original = checkout_digest(root)
            path.write_bytes(b"new")
            self.assertNotEqual(checkout_digest(root), original)
