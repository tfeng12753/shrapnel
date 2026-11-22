import gzip
import hashlib
import unittest

import exact_calls


class ExactCallTests(unittest.TestCase):
    def test_admit_only_supported_body_edits(self):
        old = "def highlight_sql(value=0):\n    return value < 1\n"
        self.assertTrue(exact_calls.supported(old, old.replace("< 1", "<= 1")))
        for new in (old.replace("=0", "=1"), old + "other = 5\n", old.replace("< 1", "< int(2)")):
            with self.subTest(new=new):
                self.assertFalse(exact_calls.supported(old, new))

    def test_shipped_recording_matches_digest(self):
        payload = gzip.decompress(exact_calls.RECORDING.read_bytes())
        self.assertEqual(hashlib.sha256(payload).hexdigest(), exact_calls.RECORDING_SHA256)
