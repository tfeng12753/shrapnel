"""Safety boundaries for the optional parser dependency refinement."""

import difflib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import selector
import token_vocabulary as vocabulary


class TokenVocabularyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / vocabulary.DIALECT).parent.mkdir(parents=True)
        (self.root / "tests").mkdir()
        (self.root / "tests/test_parser.py").write_text("# fixture\n")
        (self.root / ".pinned-revision").write_text(selector.REVISION)
        self.old = f"class Dialect:\n    {vocabulary.FLAG} = True\n    OTHER = False\n"
        (self.root / vocabulary.DIALECT).write_text(self.old)
        (self.root / "sqlglot/parser.py").write_text(
            f"def parse(self):\n    return self.dialect.{vocabulary.FLAG} and self._match(TokenType.DPIPE)\n")
        self.evidence = {"format": vocabulary.FORMAT, "revision": selector.REVISION,
                         "test": vocabulary.TEST, "flag": vocabulary.FLAG,
                         "required_token": vocabulary.TOKEN, "tokens": ["SELECT"],
                         "capture": "raw-and-current-v1", "passed": True, "advances": 1,
                         "fingerprint": vocabulary.fingerprint(self.root),
                         "gate": vocabulary.verify_gate(self.root)}
        self.evidence_path = self.root / "evidence.json"
        self.evidence_path.write_text(json.dumps(self.evidence))
        self.map = self.root / "map.json"
        self.map.write_text(json.dumps({"version": 1, "revision": selector.REVISION,
            "complete": True, "tests": [vocabulary.TEST, "tests/test_other.py::test_other"],
            "files": {vocabulary.DIALECT: {"lines": {"2": "1"}, "readers": "0"}}}))

    def make_selector(self, enabled=True):
        with patch.object(vocabulary, "load_evidence", side_effect=lambda root, rev: self.load(
                root, rev, self.evidence_path)):
            return selector.Selector([
                {"id": vocabulary.TEST, "number": 0, "seconds": 1},
                {"id": "tests/test_other.py::test_other", "number": 1, "seconds": 1}],
                self.root, self.map, token_vocabulary=enabled)

    load = staticmethod(vocabulary.load_evidence)

    def change(self, new=None):
        new = new if new is not None else self.old.replace("= True", "= False")
        return f"diff --git a/{vocabulary.DIALECT} b/{vocabulary.DIALECT}\n" + "".join(
            difflib.unified_diff(self.old.splitlines(True), new.splitlines(True),
                                 "a/" + vocabulary.DIALECT, "b/" + vocabulary.DIALECT))

    def test_default_and_eligible_case(self):
        self.assertTrue(self.make_selector(False).select(self.change()) & 1)
        self.assertFalse(self.make_selector().select(self.change()) & 1)

    def test_independent_reasons_survive(self):
        for reason in ("readers", "external", "child_work"):
            selected = self.make_selector()
            if reason == "readers":
                selected.files[vocabulary.DIALECT][reason] = 1
            else:
                setattr(selected, reason, 1)
            with self.subTest(reason=reason):
                self.assertTrue(selected.select(self.change()) & 1)

    def test_downstream_addition_survives(self):
        selected = self.make_selector()
        selected.import_effects = object()
        with patch.object(selected, "downstream_imports", return_value=1) as check:
            self.assertTrue(selected.select(self.change()) & 1)
            self.assertFalse(check.call_args.args[1] & 1)

    def test_fallbacks(self):
        for mode in ("hit", "stale", "missing", "incomplete", "conservative"):
            selected = self.make_selector()
            if mode == "hit":
                selected.token_evidence["tokens"].append("DPIPE")
            elif mode in ("stale", "missing"):
                selected.token_evidence = None
            elif mode == "incomplete":
                selected.complete = False
            else:
                selected.conservative_python = True
            with self.subTest(mode=mode):
                self.assertTrue(selected.select(self.change()) & 1)

    def test_unrelated_mixed_and_invalid_patches(self):
        selected = self.make_selector()
        baseline = self.make_selector(False)
        for edit in (self.change(self.old.replace("OTHER = False", "OTHER = True")),
                     self.change(self.old.replace("= True", "= False").replace("OTHER = False", "OTHER = True")),
                     self.change().replace("-    ", "-  "),
                     self.change() + "diff --git a/x b/x\nold mode 100644\nnew mode 100755\n",
                     "old mode 100644\nnew mode 100755\n" + self.change()):
            with self.subTest(edit=edit):
                self.assertEqual(selected.select(edit), baseline.select(edit))

    def test_evidence_binding(self):
        self.assertIsNotNone(self.load(self.root, selector.REVISION, self.evidence_path))
        for field, value in (("format", 0), ("revision", "stale"), ("passed", False),
                             ("test", "other"), ("tokens", []), ("advances", 0),
                             ("fingerprint", "stale"), ("gate", {})):
            self.evidence_path.write_text(json.dumps({**self.evidence, field: value}))
            with self.subTest(field=field):
                self.assertIsNone(self.load(self.root, selector.REVISION, self.evidence_path))
        self.evidence_path.write_text(json.dumps(self.evidence))
        (self.root / "tests/test_parser.py").write_text("# changed\n")
        self.assertIsNone(self.load(self.root, selector.REVISION, self.evidence_path))

    def test_second_or_indirect_flag_use_rejects_proof(self):
        parser = self.root / "sqlglot/parser.py"
        original = parser.read_text()
        for extra in (f"\nx = getattr(object(), '{vocabulary.FLAG}')\n",
                      f"\nx = object().{vocabulary.FLAG}\n"):
            parser.write_text(original + extra)
            with self.assertRaises(ValueError):
                vocabulary.verify_gate(self.root)


if __name__ == "__main__":
    unittest.main()
