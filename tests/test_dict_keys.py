import ast
import unittest

from dict_keys import refine

SOURCE = '''class Tokenizer:
    ESCAPES = {
        "x": (16, 2, 2, 255),
        "u": (16, 4, 4, 65535),
    }
    FLAG = True

    def run(self):
        return 1

BASE = {"a": 1, **{"b": 2}}
'''


def entry(**overrides):
    info = {"start": 2, "end": 5, "keys": {"x": "1", "u": "2"}, "whole": "10",
            "presence": {"x": "ff"}, "lines": {"3": "20"}, "transferred": "40", "unobserved": "80",
            "shared": []}
    info.update(overrides)
    return {"Tokenizer.ESCAPES": info}


def decode(value):
    return int(value, 16)


def lines(text):
    return text.splitlines()


class RefineTest(unittest.TestCase):
    def run_refine(self, new, info=None, removed=None, inserted=()):
        before, after = ast.parse(SOURCE), ast.parse(new)
        if removed is None:
            old_lines, new_lines = lines(SOURCE), lines(new)
            removed = {i + 1 for i, (a, b) in enumerate(zip(old_lines, new_lines)) if a != b}
        return refine(info if info is not None else entry(), decode, before, after, removed, set(inserted))

    def test_value_edit_selects_key_whole_unobserved_and_line(self):
        new = SOURCE.replace("(16, 2, 2, 255)", "(16, 2, 3, 255)")
        # key x (1), whole (0x10), unobserved (0x80), coverage of line 3 (0x20)
        self.assertEqual(self.run_refine(new), 0x1 | 0x10 | 0x20 | 0x80)

    def test_unread_key_still_selects_fallbacks(self):
        new = SOURCE.replace("(16, 4, 4, 65535)", "(16, 4, 4, 1)")
        self.assertEqual(self.run_refine(new), 0x2 | 0x10 | 0x80)

    def test_value_shape_change_is_allowed(self):
        new = SOURCE.replace("(16, 2, 2, 255)", '"hex"')
        self.assertEqual(self.run_refine(new), 0x1 | 0x10 | 0x20 | 0x80)

    def test_key_edit_is_not_refined(self):
        self.assertIsNone(self.run_refine(SOURCE.replace('"x": (16', '"y": (16')))

    def test_added_key_is_not_refined(self):
        new = SOURCE.replace('        "u": (16, 4, 4, 65535),\n',
                             '        "u": (16, 4, 4, 65535),\n        "z": 1,\n')
        self.assertIsNone(self.run_refine(new, removed={5}, inserted={5}))

    def test_non_literal_value_is_not_refined(self):
        self.assertIsNone(self.run_refine(SOURCE.replace("(16, 2, 2, 255)", "compute()")))

    def test_edit_outside_literal_is_not_refined(self):
        new = SOURCE.replace("(16, 2, 2, 255)", "(16, 2, 3, 255)").replace("FLAG = True", "FLAG = False")
        self.assertIsNone(self.run_refine(new))

    def test_unmapped_literal_is_not_refined(self):
        self.assertIsNone(self.run_refine(SOURCE.replace('"a": 1', '"a": 2')))

    def test_old_map_format_is_not_refined(self):
        info = entry()
        del info["Tokenizer.ESCAPES"]["transferred"]
        self.assertIsNone(self.run_refine(SOURCE.replace("(16, 2, 2, 255)", "(16, 2, 3, 255)"), info))

    def test_spread_value_edit_is_not_refined(self):
        info = entry()
        info["BASE"] = {"start": 11, "end": 11, "keys": {}, "whole": "0", "presence": {}, "lines": {},
                        "transferred": "0", "unobserved": "0", "shared": []}
        self.assertIsNone(self.run_refine(SOURCE.replace('**{"b": 2}', '**{"b": 3}'), info))

    def test_shared_reads_fall_back(self):
        new = SOURCE.replace("(16, 2, 2, 255)", "(16, 2, 3, 255)")
        self.assertIsNone(self.run_refine(new, entry(shared=["x"])))
        self.assertIsNone(self.run_refine(new, entry(shared=[""])))
        self.assertEqual(self.run_refine(new, entry(shared=["u"])), 0x1 | 0x10 | 0x20 | 0x80)

    def test_comment_inside_literal_with_value_edit(self):
        new = SOURCE.replace("(16, 2, 2, 255),", "(16, 2, 3, 255),  # changed")
        self.assertEqual(self.run_refine(new), 0x1 | 0x10 | 0x20 | 0x80)


if __name__ == "__main__":
    unittest.main()
