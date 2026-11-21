import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

from tools import ab_eval as ab

ROOT = Path(__file__).resolve().parents[1]

# Fake selector: prints one line per patch. mode.json next to it picks the behavior:
# "all" selects tests 0 and 1 everywhere; "marker" selects test 0 only for patches containing "sel0".
FAKE_SELECTOR = r'''
import argparse, json, sys
from pathlib import Path
here = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument("--data", type=Path)
args, _ = parser.parse_known_args()
if sorted(p.name for p in args.data.iterdir()) != ["commits", "tests.json"]:
    sys.exit(3)
with open(here / "calls.log", "a") as log:
    log.write("x\n")
mode = json.loads((here / "mode.json").read_text())
if mode == "crash":
    sys.exit(5)
for patch in sorted((args.data / "commits").glob("*.patch")):
    text = patch.read_text()
    if mode == "all":
        workers = [[0, 1]]
    else:
        workers = [[0]] if "sel0" in text else []
    print(json.dumps({"commit": patch.stem, "workers": workers}))
'''

CATALOG = {"tests": [{"number": 0, "id": "tests/t.py::T::a", "file": "tests/t.py", "seconds": 1.0},
                     {"number": 1, "id": "tests/t.py::T::b", "file": "tests/t.py", "seconds": 0.5},
                     {"number": 2, "id": "tests/t.py::T::c", "file": "tests/t.py", "seconds": 3.0}],
           "skipped": []}


def make_revision(path, mode):
    path.mkdir(parents=True)
    (path / "selector.py").write_text(FAKE_SELECTOR)
    (path / "mode.json").write_text(json.dumps(mode))
    return path


def calls(path):
    log = path / "calls.log"
    return len(log.read_text().splitlines()) if log.exists() else 0


def make_mutants(path, count=65, string_ids=False):
    (path / "commits").mkdir(parents=True)
    (path / "results").mkdir()
    manifest = {}
    for i in range(count):
        commit = f"m{i:05d}"
        failing = [0] if i % 5 == 0 else [0, 1] if i % 5 == 1 else []
        if string_ids:
            failing = [CATALOG["tests"][n]["id"] for n in failing]
        manifest[commit] = {"kind": "code" if i % 2 == 0 else "fixture"}
        if i % 2 == 0:
            manifest[commit]["family"] = "arith"
        marker = "sel0" if i % 5 in (0, 1) else "other"
        (path / "commits" / f"{commit}.patch").write_text(f"--- a/x.py\n+++ b/x.py\n# {marker}\n")
        summary = f"{len(failing)} failed, 9 passed in 1.00s" if failing else "9 passed in 1.00s"
        (path / "results" / f"{commit}.json").write_text(json.dumps(
            {"failing": failing, "unknown": [], "exit": 1 if failing else 0, "summary": summary}))
    (path / "manifest.json").write_text(json.dumps(manifest))
    (path / "results" / "baseline.json").write_text(json.dumps(
        {"failing": [], "unknown": [], "exit": 0, "summary": "9 passed in 1.00s"}))


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.catalog = self.tmp / "tests.json"
        self.catalog.write_text(json.dumps(CATALOG))
        self.mutants = self.tmp / "mutants"
        self.rev_a = make_revision(self.tmp / "rev_a", "all")
        self.rev_b = make_revision(self.tmp / "rev_b", "marker")
        self.work = self.tmp / "work"
        self.out = self.tmp / "report.json"

    def run_main(self, *extra, a=None, b=None):
        argv = [str(a or self.rev_a), str(b or self.rev_b), "--mutants", str(self.mutants),
                "--catalog", str(self.catalog), "--python", sys.executable, "--source", str(self.tmp),
                "--map", str(self.tmp / "none.json.gz"), "--import-closure", str(self.tmp / "none.json"),
                "--work", str(self.work), "--out", str(self.out), *extra]
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
            code = ab.main(argv)
        return code, json.loads(self.out.read_text()), stdout.getvalue()


class HelperTests(unittest.TestCase):
    def test_batches(self):
        ids = [f"m{i:05d}" for i in range(65)]
        batches = ab.make_batches(reversed(ids))
        self.assertEqual([len(b) for b in batches], [30, 30, 5])
        self.assertEqual(batches[0][0], "m00000")
        self.assertEqual(batches, ab.make_batches(ids))
        shuffled = ab.make_batches(ids, seed=7)
        self.assertEqual(shuffled, ab.make_batches(list(reversed(ids)), seed=7))
        self.assertNotEqual(shuffled, batches)
        self.assertEqual(sorted(sum(shuffled, [])), ids)

    def test_oracle(self):
        duration = {0: 1000, 1: 500, 2: 3000}
        self.assertEqual(ab.oracle_milliseconds([], duration), 0)
        self.assertEqual(ab.oracle_milliseconds([0], duration), 3000)
        self.assertEqual(ab.oracle_milliseconds([0, 1], duration), 3500)
        # Floor, not a packing: three 2 s tests on k=2 -> max(2000, 3000) + 4000 (LPT would give 8000).
        three = {n: 2000 for n in range(3)}
        self.assertEqual(ab.oracle_milliseconds(list(three), three), 7000)

    def test_oracle_matches_documented_sample_floor(self):
        tests = json.loads((ROOT / "sample" / "tests.json").read_text())["tests"]
        answers = json.loads((ROOT / "sample" / "answers.json").read_text())["commits"]
        duration = {t["number"]: ab._duration_milliseconds(t["seconds"]) for t in tests}
        self.assertEqual(sum(ab.oracle_milliseconds(a["failing"], duration) for a in answers.values()), 94351)

    def test_docker_command(self):
        command = ab.docker_command("img:b", Path("/tmp/batch"), ["--no-relations"], "n1")
        self.assertEqual(command[:4], ["docker", "run", "--name", "n1"])
        joined = " ".join(command)
        for flag in ("--network none", "--cpus 2", "--memory 4096m", "--memory-swap 4096m", "--pids-limit 256",
                     "--read-only", "--cap-drop ALL", "--user 65534:65534", "--platform linux/amd64"):
            self.assertIn(flag, joined)
        self.assertIn("/tmp/batch/commits:/data/commits:ro", command)
        self.assertIn("/tmp/batch/tests.json:/data/tests.json:ro", command)
        self.assertEqual(command[-4:], ["img:b", "python", "/app/tools/timed_run.py", "--no-relations"])

    def test_side_uses_per_side_image(self):
        args = types.SimpleNamespace(a_args="--relations", b_args="", image="img:base", a_image="img:a",
                                     b_image=None)
        with mock.patch.object(ab, "image_id", side_effect=lambda image: "sha-" + image):
            a = ab.Side("a", "main", args, Path("/nonexistent"))
            b = ab.Side("b", "main", args, Path("/nonexistent"))
        self.assertEqual(a.command(Path("/d"), args, "x")[-4:], ["img:a", "python", "/app/tools/timed_run.py",
                                                                 "--relations"])
        self.assertEqual(b.command(Path("/d"), args, "x")[-3:], ["img:base", "python", "/app/tools/timed_run.py"])
        self.assertEqual(a.identity, {"image": "sha-img:a"})

    def test_git_ref_identity_is_path_independent(self):
        with tempfile.TemporaryDirectory() as tmp:
            first, label = ab.materialize("HEAD", Path(tmp) / "one")
            second, _ = ab.materialize("HEAD", Path(tmp) / "two")
            self.assertTrue((first / "selector.py").is_file())
            self.assertTrue(label.startswith("HEAD@"))
            self.assertEqual(ab.fingerprint(first), ab.fingerprint(second))
            self.assertEqual(ab.file_sha256(first / "selector.py"), ab.file_sha256(second / "selector.py"))

    def test_container_cpu(self):
        self.assertEqual(ab.container_cpu('noise\nBR_TIMING {"cpu_seconds": 1.25}\n'), 1.25)
        self.assertIsNone(ab.container_cpu("nothing"))

    def test_local_command(self):
        command = ab.local_command("py", "/rev", "/d", "/src", "/m.gz", "/c.json", ["--x"])
        self.assertEqual(command, ["py", "/rev/selector.py", "--data", "/d", "--source", "/src", "--map", "/m.gz",
                                   "--import-closure", "/c.json", "--x"])


class EndToEndTests(Fixture):
    def test_compare(self):
        make_mutants(self.mutants)
        code, report, text = self.run_main()
        self.assertEqual(code, 1)  # B misses commits A catches
        self.assertEqual([len(b) for b in report["batches"]], [30, 30, 5])
        self.assertEqual(calls(self.rev_a), 3)
        a, b = report["sides"]["a"], report["sides"]["b"]
        self.assertEqual((a["commits"], a["misses"], a["simulated_seconds"]), (65, 0, 227.5))
        missed = [f"m{i:05d}" for i in range(65) if i % 5 == 1]
        self.assertEqual(sorted(b["missed"]), missed)
        self.assertEqual(b["missed"]["m00001"], [1])
        self.assertEqual(b["simulated_seconds"], 26 * 3 + 13 * 300)
        self.assertEqual(a["oracle_seconds"], 13 * 3 + 13 * 3.5)
        self.assertEqual(a["excess_seconds"], 227.5 - 84.5)
        self.assertGreaterEqual(a["cpu_seconds"], 0)
        self.assertEqual(len(a["batches"]), 3)
        # Per family and kind.
        self.assertEqual(set(b["by_family"]), {"arith", "unknown"})
        self.assertEqual(b["by_family"]["arith"]["commits"], 33)
        self.assertEqual(b["by_family"]["arith"]["misses"], sum(1 for i in range(0, 65, 2) if i % 5 == 1))
        self.assertEqual(b["by_kind"]["fixture"]["commits"], 32)
        self.assertEqual(sum(v["misses"] for v in b["by_kind"].values()), 13)
        # Per-commit diff.
        diff = report["diff"]
        self.assertEqual(sorted(diff["new_misses_in_b"]), missed)
        self.assertEqual(diff["misses_fixed_in_b"], {})
        self.assertEqual(len(diff["b_worse"]), 13)
        self.assertEqual(len(diff["b_cheaper"]), 52)
        self.assertEqual(diff["b_cheaper"][0]["delta_seconds"], -3.5)
        self.assertIn("NEW MISS in B: m00001", text)

        # Swapped: B fixes A's misses, so no alert.
        self.work = self.tmp / "work2"
        code, report, _ = self.run_main(a=self.rev_b, b=self.rev_a)
        self.assertEqual(code, 0)
        self.assertEqual(sorted(report["diff"]["misses_fixed_in_b"]), missed)

    def test_resume(self):
        make_mutants(self.mutants)
        self.run_main()
        self.assertEqual((calls(self.rev_a), calls(self.rev_b)), (3, 3))
        self.assertEqual(len(list((self.work / "a").glob("batch-*.json"))), 3)
        _, report, _ = self.run_main()
        self.assertEqual((calls(self.rev_a), calls(self.rev_b)), (3, 3))
        self.assertEqual(report["sides"]["b"]["misses"], 13)
        # Changing B's code reruns only B.
        (self.rev_b / "mode.json").write_text(json.dumps("all"))
        code, report, _ = self.run_main()
        self.assertEqual((calls(self.rev_a), calls(self.rev_b)), (3, 6))
        self.assertEqual((code, report["sides"]["b"]["misses"]), (0, 0))
        # Different extra args are a different run.
        self.run_main("--a-args=--flag")
        self.assertEqual(calls(self.rev_a), 6)

    def test_split_repeats_and_string_ids(self):
        make_mutants(self.mutants, count=12, string_ids=True)
        code, report, _ = self.run_main("--split", "holdout", "--repeats", "3", "--batch-size", "4")
        self.assertEqual(report["sides"]["a"]["commits"], 6)
        self.assertTrue(all(int(c[1:]) % 2 for c in report["sides"]["a"]["per_commit"]))
        self.assertEqual([len(b) for b in report["batches"]], [4, 2])
        self.assertEqual(calls(self.rev_a), 6)
        self.assertEqual(len(report["sides"]["a"]["batches"][0]["cpu_seconds_all_runs"]), 3)
        self.assertEqual(report["sides"]["b"]["missed"], {"m00001": [1], "m00011": [1]})
        self.assertEqual(code, 1)

    def test_selector_failure(self):
        make_mutants(self.mutants, count=4)
        crash = make_revision(self.tmp / "rev_crash", "crash")
        code, report, text = self.run_main(b=crash)
        self.assertEqual(code, 2)
        self.assertEqual(report["sides"]["b"]["errors"][0]["error"], "selector exit 5")
        self.assertIn("ERROR batch 0", text)
        # Failed batches are retried on the next run.
        self.run_main(b=crash)
        self.assertEqual(calls(crash), 2)


if __name__ == "__main__":
    unittest.main()
