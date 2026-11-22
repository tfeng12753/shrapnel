import ast
import unittest

from key_evidence import evidence, value_keys


class TokenizerKeysTests(unittest.TestCase):
    def test_record_matches_the_pinned_suite(self):
        self.assertTrue(evidence()["complete"])
        self.assertGreater(len(evidence()["catalog"]), 0)

    def test_only_changed_values_are_eligible(self):
        def expr(text):
            return ast.parse(text, mode="eval").body

        original = expr('{"a": (1, 2, 3, 4), "b": (5, 6, 7, 8)}')
        changed = expr('{"a": (1, 2, 3, 4), "b": (9, 6, 7, 8)}')
        self.assertEqual(value_keys(original, changed)[1], ["b"])
        for other in ['{"a": (1, 2, 3, 4)}', '{"a": (True, 2, 3, 4), "b": (5, 6, 7, 8)}',
                      '{"b": (5, 6, 7, 8), "a": (1, 2, 3, 4)}']:
            self.assertIsNone(value_keys(original, expr(other)))
