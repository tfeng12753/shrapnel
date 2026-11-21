import unittest

from tools.map_fixture_fields import unpack_sites


class FixtureFieldsTests(unittest.TestCase):
    def test_discard_in_comprehension(self):
        def inputs(rows):
            return [(meta, sql) for meta, sql, _ in rows]

        # Python versions before comprehension inlining use a nested code object.
        bodies = [inputs.__code__, *(c for c in inputs.__code__.co_consts if hasattr(c, "co_code"))]
        self.assertEqual([cols for code in bodies for cols in unpack_sites(code).values()], [[0, 1]])

    def test_underscore_used_by_consumer_is_a_live_field(self):
        def consume(row):
            _, value = row
            return _, value

        self.assertEqual(list(unpack_sites(consume.__code__).values()), [[0, 1]])

    def test_locals_inspection_cannot_establish_a_discard(self):
        def inspect(row):
            _, value = row
            return locals(), value

        self.assertFalse(unpack_sites(inspect.__code__))

    def test_nested_unpack_does_not_establish_a_discard_for_outer_row(self):
        def nested(row):
            (a, b), _ = row
            return a, b

        import dis
        first = next(i for i in dis.get_instructions(nested) if i.opname == "UNPACK_SEQUENCE")
        self.assertNotIn(first.offset, unpack_sites(nested.__code__))
