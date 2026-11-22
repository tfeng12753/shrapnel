import json
import gzip
import ast
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from selector import REVISION, Selector
from tools.mutants import diff
from tools.map_location_readers import build


class DiscardedStringTests(TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.catalog = [{"number": i, "id": str(i), "seconds": .1} for i in range(3)]
        self.name = "example.py"
        self.before = ('class A:\n    """Class description."""\n    field = 1\n'
                       '    """\n    Original note.\n    """\n    value = "used"\n')
        (self.root / self.name).write_text(self.before)
        (self.root / "other.sql").write_text("old\n")
        mapping = {"version": 1, "revision": REVISION, "complete": True, "tests": ["0", "1", "2"],
                   "files": {self.name: {"readers": "1", "lines": {str(i): "7" for i in range(1, 8)}},
                             "other.sql": {"readers": "2"}}}
        self.map = self.root / "map.json"
        self.map.write_text(json.dumps(mapping))

    def selected(self, after, *, enabled=True, extra=""):
        selector = Selector(self.catalog, self.root, self.map, discarded_strings=enabled)
        return selector.select(diff(self.name, self.before, after) + extra)

    def test_preserves_source_readers_and_mixed_file_reasons(self):
        after = self.before.replace("Original note.", "# updated")
        self.assertEqual(self.selected(after), 1)
        self.assertEqual(self.selected(after, enabled=False), 7)
        self.assertEqual(self.selected(after, extra=diff("other.sql", "old\n", "new\n")), 3)

    def test_keeps_observable_strings_positions_and_mixed_code_edits(self):
        for after in (self.before.replace("Class description", "Another description"),
                      self.before.replace('"used"', '"other"'),
                      self.before.replace("Original note.", "Note.\n    Second line."),
                      self.before.replace("Original note.", "edit").replace("field = 1", "field = 2")):
            with self.subTest(after=after):
                self.assertEqual(self.selected(after), 7)

    def test_location_evidence_keeps_position_and_source_readers(self):
        after = self.before.replace("Original note.", "Original note.\n    # extra")
        selector = Selector(self.catalog, self.root, self.map, discarded_strings=True)
        selector.location_readers = 2
        self.assertEqual(selector.select(diff(self.name, self.before, after)), 3)

    def test_line_sensitive_method_is_indexed_even_without_a_call_phase_hit(self):
        observer = self.root / "tests/test_positions.py"
        observer.parent.mkdir()
        observer.write_text("class TestPosition:\n    def test_location(self):\n"
                            "        assert target.__code__.co_firstlineno == 3\n")
        name = "tests/test_positions.py"
        catalog = [name+"::TestPosition::test_location", "unrelated"]
        data = {"tests": catalog, "revision": REVISION, "complete": True, "external": "0",
                "files": {name: {"lines": {}}}}
        mapping = self.root / "map.gz"
        mapping.write_bytes(gzip.compress(json.dumps(data).encode()))
        self.assertEqual(build(self.root, mapping)["mask"], "1")
        original = "class A:\n    field = 1\n    '''note'''\n    def f(self): pass\n"
        shifted = original.replace("'''note'''", "'''note\n    more'''")
        positions = []
        for text in (original, shifted):
            scope = {}
            exec(compile(ast.parse(text), "<module>", "exec"), scope)
            positions.append(scope["A"].f.__code__.co_firstlineno)
        self.assertEqual(positions, [4, 5])
