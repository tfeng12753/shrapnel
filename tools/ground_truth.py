"""Find which catalog tests really fail for one patch (evaluation only; run inside the image).

Copies the pinned checkout, applies the patch with the selector's own strict
patch reader, runs the full unchanged suite with real process pools, and writes
the failing catalog numbers. Subtest failures count against their owning test.

    docker run --rm -v $PWD:/repo:ro -v $PWD/mutants:/m shrapnel \
        python /repo/tools/ground_truth.py /m/commits/m01000.patch /m/results/m01000.json
"""
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from selector import apply_change, parse_patch  # noqa: E402

# Short-summary outcomes that mean the owning test fails. pytest 9 reports a failing
# unittest subTest as "SUBFAILED[title] tests/x.py::C::t - msg" while the parent test
# itself counts as passed, so subtest lines must be attributed to their parent.
OUTCOME = re.compile(r"^(?:FAILED|ERROR|SUBFAILED|SUBERROR)\b")
TEST_ID = re.compile(r"(?:^|\s)(tests/\S+?)(?=\s|$)")


def failing_tests(stdout, number):
    """Map pytest -rfE short-summary lines to catalog numbers; return (failing, unknown, lines)."""
    failing, unknown, lines = set(), set(), []
    for line in stdout.splitlines():
        if not OUTCOME.match(line):
            continue
        lines.append(line[:300])
        m = TEST_ID.search(line)
        if not m:
            unknown.add(line[:120])
            continue
        test_id = re.sub(r"\[.*$", "", m[1])
        if test_id in number:
            failing.add(number[test_id])
            continue
        # Collection errors name a file; every catalog test in it is affected.
        matched = [n for i, n in number.items() if i == test_id or i.startswith(test_id + "::")]
        failing.update(matched)
        if not matched:
            unknown.add(test_id)
    return failing, unknown, lines


def main(patch_path, output, source="/opt/sqlglot", catalog="/repo/sample/tests.json"):
    tests = json.loads(Path(catalog).read_text())["tests"]
    number = {t["id"]: t["number"] for t in tests}
    started = time.time()
    with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
        root = Path(tmp) / "sqlglot"
        shutil.copytree(source, root, ignore=shutil.ignore_patterns("__pycache__"))
        patch = Path(patch_path).read_text()
        if patch.strip():
            for change in parse_patch(patch):
                path = root / change["old"]
                new, _, _ = apply_change(path.read_text(), change)
                path.write_text(new)
        run = subprocess.run([sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider", "-rfE",
                              "--timeout=900"], cwd=root, capture_output=True, text=True,
                             env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp", "SKIP_INTEGRATION": "0",
                                  "PYTHONDONTWRITEBYTECODE": "1"})
    failing, unknown, lines = failing_tests(run.stdout, number)
    summary = run.stdout.strip().splitlines()[-1] if run.stdout.strip() else run.stderr[-300:]
    # Keep the raw short-summary lines so results can be re-parsed without re-running.
    Path(output).write_text(json.dumps({"failing": sorted(failing), "unknown": sorted(unknown), "exit": run.returncode,
                                        "summary": summary, "seconds": round(time.time() - started, 1),
                                        "summary_lines": lines}))


if __name__ == "__main__":
    main(*sys.argv[1:])
