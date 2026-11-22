import ast
import unittest

import attribute_docs
import config_flow
import key_evidence
from selector import classify_python_edit


class TestConsumerCoverage(unittest.TestCase):
    def test_value_evidence_leaves_membership_and_order_intact(self):
        def expr(s):
            return ast.parse(s, mode="eval").body

        a = expr('{"x": (16,2,2,255), "u": (16,4,4,65535)}')
        b = expr('{"x": (2,2,2,255), "u": (16,4,4,65535)}')
        self.assertEqual(key_evidence.value_keys(a, b)[1], ["x"])
        self.assertIsNone(
            key_evidence.value_keys(a, expr('{"u": (16,4,4,65535), "x": (16,2,2,255)}'))
        )
        self.assertIsNone(key_evidence.value_keys(a, expr('{"x": (16,2,2,255)}')))

    def test_boolean_probe_requires_a_single_literal(self):
        self.assertEqual(
            config_flow.literal_edit("OPT = False", "OPT = True", {1}), "OPT"
        )
        self.assertIsNone(config_flow.literal_edit("OPT = False", "OPT = 1", {1}))
        self.assertIsNone(
            config_flow.literal_edit("OPT = False\nuse()", "OPT = True\nother()", {1})
        )

    def test_class_documentation_remains_observable(self):
        base = 'class C:\n    "public"\n    x = 1\n    "attribute prose"'
        for key, changed in [
            ("public", False),
            ("attribute prose", True),
            ("1", False),
        ]:
            old, new = ast.parse(base), ast.parse(base.replace(key, "2"))
            pristine = ast.dump(new)
            self.assertEqual(
                attribute_docs.matches(old, new, classify_python_edit), changed
            )
            self.assertEqual(ast.dump(new), pristine)
