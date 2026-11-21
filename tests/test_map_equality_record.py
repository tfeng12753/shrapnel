"""Equality observations: reach before operands, unknown before dispatch, builtin relations only."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from tools.build_map import Recorder, SourceModel, write_map
from tools.instrument import Instrumentation, equality_state

SOURCE = '''def check(left, right):
    return left() == right()

def differ(left, right):
    return left() != right()

def unreached(flag, value):
    return flag and value == 3
'''


class Custom:
    def __eq__(self, other):
        raise LookupError("custom")


class EqualityRecordTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.path = self.root / "sqlglot" / "subject.py"
        self.path.parent.mkdir()
        self.path.write_text(SOURCE)
        self.model = SourceModel(self.root)
        self.rec = Recorder(self.root, self.model, None)
        observer = Instrumentation(self.root, self.rec)
        observer.install()
        self.addCleanup(observer.restore)
        spec = importlib.util.spec_from_file_location("sqlglot.subject", self.path)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def call(self, test, function, *args):
        self.rec.current, self.rec.phase = test, "call"
        try:
            return function(*args)
        finally:
            self.rec.current, self.rec.phase = None, "collection"

    def test_states(self):
        self.assertEqual(equality_state(1, 1), "p:1")
        self.assertEqual(equality_state(1, 2), "p:2")
        self.assertEqual(equality_state("b", "a"), "p:4")
        self.assertEqual(equality_state(True, 1), "p:1")
        self.assertEqual(equality_state(None, None), "e:9")
        self.assertEqual(equality_state(None, 3), "e:0")
        self.assertEqual(equality_state("3", 3), "e:0")
        self.assertIsNone(equality_state(1.0, 1))
        self.assertIsNone(equality_state(Custom(), 1))
        self.assertIsNone(equality_state((1,), (1,)))

    def test_records_and_serialization(self):
        def boom():
            raise ValueError("operand")
        self.assertTrue(self.call("t0", self.module.check, lambda: 2, lambda: 2))
        self.assertFalse(self.call("t1", self.module.check, lambda: 1, lambda: None))
        with self.assertRaises(ValueError):
            self.call("t2", self.module.check, lambda: 1, boom)
        with self.assertRaises(LookupError):
            self.call("t3", self.module.check, Custom, lambda: 1)
        self.assertTrue(self.call("t4", self.module.differ, lambda: "a", lambda: "b"))
        self.assertFalse(self.call("t5", self.module.unreached, False, 3))
        site = ("sqlglot/subject.py", "2:11")
        entry = self.rec.equality[site]
        self.assertEqual(entry["reached"], {"t0", "t1", "t2", "t3"})
        self.assertEqual(dict(entry["states"]), {"p:1": {"t0"}, "e:0": {"t1"}})
        self.assertEqual(entry["unknown"], {"t3"})
        # The comparison record keeps its established sites only.
        self.assertNotIn(site, self.rec.comparisons)

        self.rec.tests = [f"t{i}" for i in range(7)]
        coverage = Mock()
        coverage.measured_files.return_value = [str(self.path)]
        # t6 executed line 2 without the observer seeing it (for example, in a child process).
        coverage.contexts_by_lineno.return_value = {2: ["t0", "t1", "t2", "t3", "t6"], 5: ["t4"], 8: ["t5"]}
        destination = self.root / "map.json"
        write_map(self.root, self.model, self.rec, coverage, destination)
        records = json.loads(destination.read_text())["files"]["sqlglot/subject.py"]["equality"]
        check = records["2:11"]
        self.assertEqual(check["op"], "Eq")
        self.assertEqual(check["reached"], format(0b1111, "x"))
        self.assertEqual(check["states"], {"p:1": "1", "e:0": "2"})
        # t2 (operand raised), t3 (custom operator) and t6 (unobserved coverage).
        self.assertEqual(check["unknown"], format(0b1001100, "x"))
        self.assertEqual(records["5:11"], {"op": "NotEq", "reached": "10", "states": {"p:2": "10"}, "unknown": "0"})
        self.assertNotIn("8:20", records)

    def test_shared_evaluation_has_no_record(self):
        self.module.check(lambda: 1, lambda: 1)
        self.call("t0", self.module.check, lambda: 1, lambda: 1)
        self.rec.tests = ["t0"]
        coverage = Mock()
        coverage.measured_files.return_value = []
        destination = self.root / "map.json"
        write_map(self.root, self.model, self.rec, coverage, destination)
        files = json.loads(destination.read_text())["files"]
        self.assertNotIn("2:11", files.get("sqlglot/subject.py", {}).get("equality", {}))


if __name__ == "__main__":
    unittest.main()
