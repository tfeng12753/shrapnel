"""Check comparison maps against Python itself, independently of sample patches."""
import ast
import importlib.util
import operator
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from selector import Selector
from tools.build_map import Recorder, SourceModel, write_map
from tools.instrument import Instrumentation


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
                    selector = Selector(tests, root, dest, relations=True)
                    site = next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Compare))
                    entry = selector.files["sqlglot/subject.py"]
                    for new, op in replacements.items():
                        changes = [(f"{site.lineno}:{site.col_offset}", old, new)]
                        mask = selector.refine_comparisons(changes, entry)
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
                 and isinstance(n.func, ast.Name) and n.func.id == "_blast_radius_comparison"]
        self.assertEqual([source.splitlines()[n.lineno - 1].strip() for n in calls],
                         ["return len(value) == 5", 'return "x" != value.lower()'])

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
                self.assertIs(builtins._blast_radius_comparison(value, 2, "Eq", "m.py", "1:0"), value)
                self.assertEqual(value.calls, 1)
                self.assertEqual(rec.comparisons["m.py", "1:0"]["unknown"], {"test"})
                rec.phase = "setup"
                builtins._blast_radius_comparison(3, 2, "Eq", "m.py", "1:0")
                self.assertTrue(rec.comparisons["m.py", "1:0"]["collection"])
            finally:
                observer.restore()
