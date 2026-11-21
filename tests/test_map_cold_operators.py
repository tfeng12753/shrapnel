import hashlib
import unittest

from tools.import_closure import cold_operator_sites
from tools.map_import_replay import indexed_change


class ColdOperatorTests(unittest.TestCase):
    def test_exact_body_operator_and_source_digest_are_required(self):
        raw = ("class T:\n"
               "    def f(self, x=1 == 2):\n"
               "        later = lambda: x == 2\n"
               "        return 'é' == x\n"
               "    @staticmethod\n"
               "    def g(x): return x == 1\n").encode()
        sites = cold_operator_sites(raw, [["T.f", 2], ["T.g", 6]])
        self.assertEqual(set(sites), {"4"})
        record = {"digest": hashlib.sha256(raw).hexdigest(), "sites": sites}
        before = raw.decode()
        after = before.replace("'é' == x", "'é' is not x")
        self.assertTrue(indexed_change(raw, after, {4}, set(), record))
        for changed in (after.replace("lambda:", "lambda y:"), after.replace("not x", "not y"), before):
            self.assertFalse(indexed_change(raw, changed, {4}, set(), record))
        self.assertFalse(indexed_change(raw, after, {4}, {4}, record))
        self.assertFalse(indexed_change(raw, after, {4}, set(), dict(record, digest="stale")))
