import ast
import unittest

from tools.map_tuple_reads import Trace, declarations


class TupleReadsTest(unittest.TestCase):
    def test_alias_preserves_receiver_key_default_and_result_identity(self):
        source = """
class Config:
    TABLE = {"u": (1, 2)}
    MIXED = {"u": (3,), "x": 4}

def read(m, key, default):
    nested = m
    return nested.get(key(), default())
"""
        tree = ast.parse(source)
        bindings = declarations(tree)
        self.assertEqual(list(bindings.values()), ["Config.TABLE"])
        seen = []

        def bind(mapping, name):
            seen.append((name, mapping))
            return mapping

        def get(method, key, default):
            seen.append((method.__self__, key))
            return method(key, default)

        ns = {"_shrapnel_tuple_bind": bind, "_shrapnel_tuple_get": get}
        exec(compile(ast.fix_missing_locations(Trace(bindings).visit(tree)), "<test>", "exec"), ns)
        mapping = ns["Config"].TABLE
        order = []
        result = ns["read"](mapping, lambda: order.append("key") or "u", lambda: order.append("default"))
        self.assertEqual(order, ["key", "default"])
        self.assertIs(seen[0][1], mapping)
        self.assertIs(seen[1][0], mapping)
        self.assertIs(result, mapping["u"])
