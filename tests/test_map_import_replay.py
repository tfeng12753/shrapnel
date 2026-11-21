import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.map_import_replay import check_item, cold_comparisons


class ImportReplayTests(unittest.TestCase):
    def test_only_body_comparisons_in_observed_functions_qualify(self):
        cases = [
            ("class T:\n def f(self, x): return x == 1\n", [("T.f", 2)], True),
            ("def f(x): return x == 1\n", [], False),
            ("def f(x=1 == 1): return x\n", [("f", 1)], False),
            ("@wrapper\ndef f(x): return x == 1\n", [("f", 2)], False),
            ("def f(x): return lambda: x == 1\n", [("f", 1)], False),
            ("def f(x): return x == 1\nflag = 1 == 2\n", [("f", 1)], False),
        ]
        for before, records, expected in cases:
            with self.subTest(before=before):
                self.assertEqual(cold_comparisons(before, before.replace("==", "!="), records), expected)

    def test_ambiguous_test_outcomes_cannot_verify_a_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            for xml, status, expected in [
                ("<testcase/>", 0, True), ("<testcase><skipped/></testcase>", 0, False),
                ("<testcase/><testcase/>", 0, False), ("<testcase/>", 1, False), ("", 0, False),
            ]:
                def fake_launch(argv, **options):
                    report, = [arg.split("=", 1)[1] for arg in argv if arg.startswith("--junitxml=")]
                    Path(report).write_text(f"<testsuite>{xml}</testsuite>")
                    return process

                with self.subTest(xml=xml, status=status), patch("subprocess.Popen") as launch:
                    process = launch.return_value
                    process.__enter__.return_value.wait.return_value = status
                    launch.side_effect = fake_launch
                    self.assertEqual(check_item(source, {}, "tests/test_import.py::test_registry"), expected)
