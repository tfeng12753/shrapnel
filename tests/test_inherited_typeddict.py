"""Inherited declarations, uncertainty, metadata consumers and mixed edits."""
import ast
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from typeddict_selection import inherited_total_only
from selector import Selector, import_hazard_kind, resolve_settings
import test_typeddict_total as control

TYPING, diff = control.TYPING, control.diff


def flip(source):
    return source.replace('class Child(Args, total=False)', 'class Child(Args, total=True)')


class AncestryTests(unittest.TestCase):
    def check(self, old, new, expected=True):
        before, after = ast.parse(old), ast.parse(new)
        snapshots = ast.dump(before), ast.dump(after)
        self.assertEqual(inherited_total_only(before, after), expected)
        self.assertEqual((ast.dump(before), ast.dump(after)), snapshots)

    def test_existing_fallback_and_inherited_proof(self):
        self.assertEqual(import_hazard_kind(TYPING, flip(TYPING)), 'structural')
        self.check(TYPING, flip(TYPING))

    def test_aliases_indirect_and_multiple_bases(self):
        for old in (
            TYPING.replace('import typing as t', 'import typing as t\nTD = t.TypedDict').replace('Args(t.TypedDict', 'Args(TD'),
            TYPING.replace('import typing as t', 'from typing import TypedDict as TD').replace('t.TypedDict', 'TD'),
            TYPING.replace('class Child', 'Alias = Args\n\nclass Child').replace('Child(Args', 'Child(Alias'),
            TYPING + '\nclass Grandchild(Child, total=False):\n    extra: int\n',
            TYPING.replace('class Child(Args', 'class Other(t.TypedDict):\n    other: int\n\nclass Child(Args, Other'),
        ):
            with self.subTest(old=old):
                new = old.replace('total=False):\n    comments', 'total=True):\n    comments')
                self.check(old, new)

    def test_required_optional_overrides(self):
        old = TYPING.replace('comments: bool', 'pretty: t.NotRequired[bool]\n    comments: t.Required[bool]')
        self.check(old, flip(old))

    def test_unknown_and_rebound_bases(self):
        for old in (
            TYPING.replace('t.TypedDict', 't.NotTypedDict'),
            TYPING.replace('import typing as t', 'import other as t'),
            TYPING.replace('class Child', 'Args = Other\n\nclass Child'),
            TYPING.replace('class Child', '@decorate\nclass Child'),
            TYPING.replace('class Args', '@decorate\nclass Args'),
            TYPING.replace('class Child(Args', 'class Child(factory()'),
            TYPING + '\nfrom other import *\n',
            TYPING + '\nt.TypedDict = Other\n',
        ):
            with self.subTest(old=old):
                self.check(old, old.replace('total=False):\n    comments', 'total=True):\n    comments'), False)

    def test_mixed_edits_and_changed_inheritance(self):
        for new in (
            flip(TYPING).replace('comments: bool', 'comments: int'),
            flip(TYPING).replace('Child(Args', 'Child(t.TypedDict'),
            flip(TYPING) + '\nvalue = 3\n',
            flip(TYPING).replace('total=True', 'total=1'),
        ):
            self.check(TYPING, new, False)
        old = TYPING + '\ndef value():\n    return 1\n'
        self.check(old, flip(old).replace('return 1', 'return 2'), False)

    def test_config_default_and_override(self):
        self.assertFalse(resolve_settings([])['inherited_typeddict'])
        self.assertTrue(resolve_settings(['--inherited-typeddict'])['inherited_typeddict'])


class InheritedSelectionTests(control.SelectionTests):
    def setUp(self):
        super().setUp()
        self.patch = diff('lib/_typing.py', TYPING, flip(TYPING))

    def select(self, enabled):
        selector = Selector(self.tests, self.root, self.root / 'map.json', relations=True,
                            typeddict_state_check=True, inherited_typeddict=enabled)
        with patch.object(Selector, 'import_succeeds', return_value=True):
            return selector.select(self.patch)

    def test_failed_import_keeps_everything(self):
        selector = Selector(self.tests, self.root, self.root / 'map.json', relations=True,
                            typeddict_state_check=True, inherited_typeddict=True)
        with patch.object(Selector, 'import_succeeds', return_value=False):
            self.assertEqual(selector.select(self.patch), 7)

    def test_source_readers_retained(self):
        path = self.root / 'map.json'
        mapping = json.loads(path.read_text())
        mapping['files']['lib/_typing.py']['readers'] = '2'
        path.write_text(json.dumps(mapping))
        self.assertEqual(self.select(True), 2)

    def test_independent_file_reason_retained(self):
        old = 'def test_x():\n    pass\n'
        self.patch += diff('tests/test_x.py', old, old.replace('pass', 'assert False'))
        self.assertEqual(self.select(True), 7)

    def test_all_metadata_consumers_retained(self):
        for expression in ('Child.__required_keys__', 'Child.__optional_keys__',
                           'Child.__total__', 'Child.__annotations__', 't.get_type_hints(Child)',
                           't.is_typeddict(Child)'):
            with self.subTest(expression=expression):
                (self.root / 'tests/test_x.py').write_text('value = ' + expression + '\n')
                self.assertEqual(self.select(True), 7)

    def test_dynamic_metadata_consumers_retained(self):
        for expression in ('vars(Child)', 'Child.__dict__',
                           '(getattr)(Child, "__" + "total__")',
                           'getattr(Child, "__" + "total__")',
                           'eval("Child." + "__" + "total__")'):
            with self.subTest(expression=expression):
                (self.root / 'tests/test_x.py').write_text('value = ' + expression + '\n')
                self.assertEqual(self.select(True), 7)

    def test_fresh_failure_bearing_metadata_controls(self):
        old = TYPING + '\nclass Grandchild(Child):\n    extra: int\n'
        before, after = {}, {}
        exec(old, before)
        exec(flip(old), after)
        consumers = (
            ('Child', '__total__'), ('Child', '__required_keys__'),
            ('Child', '__optional_keys__'), ('Grandchild', '__required_keys__'),
            ('Grandchild', '__optional_keys__'),
        )
        (self.root / 'lib/_typing.py').write_text(old)
        self.patch = diff('lib/_typing.py', old, flip(old))
        for name, attribute in consumers:
            with self.subTest(name=name, attribute=attribute):
                expected = getattr(before[name], attribute)
                self.assertNotEqual(getattr(after[name], attribute), expected)
                (self.root / 'tests/test_x.py').write_text(
                    f'assert {name}.{attribute} == {expected!r}\n')
                self.assertEqual(self.select(True), 7)


if __name__ == '__main__':
    unittest.main()
