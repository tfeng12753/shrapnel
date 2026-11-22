"""Exact comparison records: attempts marked before operands, exact int/str/None operands only."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from tools.build_map import InlineProcessPool, Recorder, SourceModel, write_map
from tools.instrument import Instrumentation, exact_state

SOURCE = '''def check(left, right):
    return left() == right()

def differ(left, right):
    return left() != right()

def unreached(flag, value):
    return flag and value == 3

def less(left, right):
    return (left()
            < right())
'''


class Custom:
    def __eq__(self, other):
        raise LookupError("custom")


class ExactRecordTest(unittest.TestCase):
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

    def call(self, test, function, *args, phase="call"):
        self.rec.current, self.rec.phase = test, phase
        try:
            return function(*args)
        finally:
            self.rec.current, self.rec.phase = None, "collection"

    def write(self, tests, lines):
        self.rec.tests = tests
        coverage = Mock()
        coverage.measured_files.return_value = [str(self.path)]
        coverage.contexts_by_lineno.return_value = lines
        destination = self.root / "map.json"
        write_map(self.root, self.model, self.rec, coverage, destination)
        return json.loads(destination.read_text())["files"]["sqlglot/subject.py"].get("exact_comparisons", {})

    def test_states(self):
        self.assertEqual(exact_state(2, 2), "p:9")
        self.assertEqual(exact_state(1, 2), "p:2")
        self.assertEqual(exact_state("b", "a"), "p:4")
        self.assertEqual(exact_state(None, None), "e:9")
        self.assertEqual(exact_state(None, 3), "e:0")
        self.assertIsNone(exact_state(True, 1))
        self.assertIsNone(exact_state(1.0, 1))
        self.assertIsNone(exact_state(Custom(), 1))
        self.assertIsNone(exact_state((1,), (1,)))

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
        self.assertTrue(self.call("t6", self.module.check, lambda: True, lambda: 1))
        # A second, incomplete attempt in the same test removes it from complete.
        def twice():
            self.module.check(lambda: 3, lambda: 3)
            try:
                self.module.check(boom, lambda: 3)
            except ValueError:
                pass
        self.call("t7", twice)
        self.assertTrue(self.call("t8", self.module.less, lambda: 1, lambda: 2))
        records = self.write([f"t{i}" for i in range(10)],
                             {2: ["t0", "t1", "t2", "t3", "t6", "t7", "t9"], 5: ["t4"], 8: ["t5"], 11: ["t8"]})
        check = records["2:11"]
        self.assertEqual(check["op"], "Eq")
        self.assertEqual(check["complete"], format(0b11, "x"))
        self.assertEqual(check["states"], {"p:9": format(0b10000001, "x"), "e:0": "2"})
        # t2 (operand raised), t3 (custom operator), t6 (bool), t7 (one failed attempt), t9 (unobserved coverage).
        self.assertEqual(check["unknown"], format(0b1011001100, "x"))
        self.assertEqual(records["5:11"], {"op": "NotEq", "complete": "10", "states": {"p:2": "10"}, "unknown": "0"})
        self.assertNotIn("8:20", records)
        self.assertEqual(records["11:12"]["op"], "Lt")
        self.assertEqual(records["11:12"]["complete"], format(1 << 8, "x"))

    def test_setup_and_pool_attempts_disqualify_the_test(self):
        self.call("t0", self.module.check, lambda: 1, lambda: 1, phase="setup")
        self.call("t0", self.module.check, lambda: 1, lambda: 1)
        def pooled():
            InlineProcessPool.active += 1
            try:
                return self.module.check(lambda: 1, lambda: 1)
            finally:
                InlineProcessPool.active -= 1
        self.call("t1", pooled)
        self.call("t2", self.module.check, lambda: 1, lambda: 1)
        records = self.write(["t0", "t1", "t2"], {2: ["t0", "t1", "t2"]})
        self.assertEqual(records["2:11"]["complete"], "4")

    def test_import_time_attempt_drops_the_site(self):
        self.module.check(lambda: 1, lambda: 1)
        self.call("t0", self.module.check, lambda: 1, lambda: 1)
        self.assertNotIn("2:11", self.write(["t0"], {}))


if __name__ == "__main__":
    unittest.main()
