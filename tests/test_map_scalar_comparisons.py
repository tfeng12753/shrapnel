"""Check comparison maps against Python itself, independently of sample patches."""
import ast
import importlib.util
import operator
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from selector import Selector, comparison_contexts
from tools.build_map import Recorder, SourceModel, write_map
from tools.instrument import Instrumentation
from tools.map_equality_replay import unchanged_tests


class ScalarComparisonTests(unittest.TestCase):
    def test_map_preserves_every_changed_outcome(self):
        replacements = {"Eq": operator.eq, "NotEq": operator.ne,
                        "Lt": operator.lt, "LtE": operator.le,
                        "Gt": operator.gt, "GtE": operator.ge,
                        "Is": operator.is_, "IsNot": operator.is_not}
        cases = [(bound, None, [bound - 1, bound, bound + 1, "unusual"]) for bound in (0, 1, 2, 257)]
        cases.extend(("ss", method, ["aa", "ss", "zz", 3]) for method in ("upper", "lower", "casefold"))
        for literal, method, values in cases:
            for old, source_op in (("Eq", "=="), ("NotEq", "!=")):
                with self.subTest(literal=literal, old=old), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    path = root / "sqlglot" / "subject.py"
                    path.parent.mkdir()
                    operand = f"value.{method}()" if method else "len(value)"
                    source = f"def check(value, len):\n    return {operand} {source_op} {literal!r}\n"
                    path.write_text(source)
                    model = SourceModel(root)
                    rec = Recorder(root, model, None)
                    tests = [{"id": f"case_{i}", "number": i, "seconds": 0.1}
                             for i in range(len(values))]
                    rec.tests = [test["id"] for test in tests]
                    obs = Instrumentation(root, rec)
                    obs.install()
                    try:
                        spec = importlib.util.spec_from_file_location("sqlglot.subject", path)
                        module = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(module)
                        for test, value in zip(tests, values):
                            rec.current, rec.phase = test["id"], "call"
                            arg = SimpleNamespace(**{method: lambda: value}) if method else value
                            self.assertEqual(module.check(arg, lambda x: x), replacements[old](value, literal))
                        if method == "casefold":
                            self.assertEqual(module.check("ß", None), replacements[old]("ss", literal))
                    finally:
                        obs.restore()
                    coverage = Mock()
                    coverage.measured_files.return_value = [str(path)]
                    coverage.contexts_by_lineno.return_value = {2: rec.tests}
                    dest = root / "map.json"
                    write_map(root, model, rec, coverage, dest)
                    selector = Selector(tests, root, dest)
                    strict = Selector(tests, root, dest, relations=True)
                    tree = ast.parse(source)
                    contexts = comparison_contexts(tree)
                    site = next(n for n in ast.walk(tree) if isinstance(n, ast.Compare))
                    entry = selector.files["sqlglot/subject.py"]
                    for new, op in replacements.items():
                        changes = [(f"{site.lineno}:{site.col_offset}", old, new)]
                        self.assertIsNone(strict.refine_comparisons(changes, entry, contexts))
                        mask = selector.refine_comparisons(changes, entry, contexts)
                        self.assertIsNotNone(mask)
                        for i, value in enumerate(values):
                            try:
                                changed = op(value, literal) != replacements[old](value, literal)
                            except TypeError:
                                changed = True
                            if changed or type(value) is not type(literal) or (value == literal and new in ("Is", "IsNot")):
                                self.assertTrue(mask & (1 << i), (value, old, new))
                        if old == "Eq" and new == "LtE":
                            self.assertFalse(mask & (1 << 1))  # Equal builtin values are also <=.
                        if literal == 1 and old == "Eq" and new == "Lt":
                            self.assertFalse(mask & (1 << 2))  # A larger value is neither equal nor less.

    def test_definition_time_and_cached_code_stay_on_ordinary_coverage(self):
        from tools.instrument import Transformer
        source = """
MODULE = len(label) == 1
class C:
    flag = len(label) != 2
    @cached
    def cached(self):
        return len(label) == 3
def plain(value=(len(label) == 4)):
    return len(value) == 5
def unrelated(value):
    return value == 5
def custom_call(value):
    return value.upper(1) == "x"
def untouched(value):
    return value == "x"
def normalized(value):
    return "x" != value.lower()
"""
        tree = Transformer("subject.py", set()).visit(ast.parse(source))
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name) and n.func.id == "_shrapnel_comparison"]
        self.assertEqual([source.splitlines()[n.lineno - 1].strip() for n in calls],
                         ["return len(value) == 5", "return value == 5", 'return value.upper(1) == "x"',
                          'return value == "x"', 'return "x" != value.lower()'])
        # Only the established sites enter the comparison record.
        legacy = [source.splitlines()[n.lineno - 1].strip() for n in calls
                  if not (len(n.args) == 6 and n.args[5].value is False)]
        self.assertEqual(legacy, ["return len(value) == 5", 'return "x" != value.lower()'])

    def test_equality_replay_requires_consistent_positive_observations(self):
        record = {"op": "Eq", "unknown": 4, "states": {"p:1": 7, "p:9": 7, "p:2": 2}}
        # For the equal call == and <= agree. A second, less-than call
        # or an unrecordable dispatch keeps the corresponding test.
        self.assertEqual(unchanged_tests(record, "Eq", "LtE"), 1)
        self.assertEqual(unchanged_tests(record, "Eq", "Is"), 0)
        self.assertEqual(unchanged_tests(record, "NotEq", "LtE"), 0)

    def test_caught_exception_keeps_a_test_with_an_earlier_known_outcome(self):
        import builtins
        class Raising:
            def __eq__(self, other):
                raise LookupError("intentional")
            def __ne__(self, other):
                raise LookupError("intentional")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rec = Recorder(root, SourceModel(root), None)
            rec.current, rec.phase = "mixed", "call"
            observer = Instrumentation(root, rec)
            observer.install()
            try:
                for op in ("Eq", "NotEq"):
                    compare = builtins._shrapnel_comparison
                    compare(1, 1, op, "subject.py", op)
                    with self.assertRaisesRegex(LookupError, "intentional"):
                        compare(Raising(), 1, op, "subject.py", op)
                    record = rec.comparisons["subject.py", op]
                    self.assertEqual(record["states"]["p:1"], {"mixed"})
                    self.assertEqual(record["unknown"], {"mixed"})
            finally:
                observer.restore()

    def test_overloaded_equality_is_called_once_and_remains_unknown(self):
        class Custom:
            calls = 0
            def __eq__(self, other):
                self.calls += 1
                return self
            def __bool__(self):
                raise AssertionError("observer coerced the comparison result")
        import builtins
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = SourceModel(root)
            rec = Recorder(root, model, None)
            rec.current, rec.phase = "test", "call"
            observer = Instrumentation(root, rec)
            observer.install()
            try:
                value = Custom()
                self.assertIs(builtins._shrapnel_comparison(value, 2, "Eq", "m.py", "1:0"), value)
                self.assertEqual(value.calls, 1)
                self.assertEqual(rec.comparisons["m.py", "1:0"]["unknown"], {"test"})
                rec.phase = "setup"
                builtins._shrapnel_comparison(3, 2, "Eq", "m.py", "1:0")
                self.assertTrue(rec.comparisons["m.py", "1:0"]["collection"])
            finally:
                observer.restore()
