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
# Section header of a collection error: "____ ERROR collecting tests/x.py ____".
COLLECTING = re.compile(r"^_+ ERROR collecting (tests/\S+?) _+$")
# pytest stops before running anything when collection fails without
# --continue-on-collection-errors: "!!!! Interrupted: 2 errors during collection !!!!".
INTERRUPTED = re.compile(r"^!+ Interrupted: (\d+) errors? during collection !+$")
# Flags passed to pytest. --continue-on-collection-errors keeps a broken test module
# from aborting the session, so every other file still runs and reports its failures.
PYTEST_ARGS = ["-q", "-p", "no:cacheprovider", "-rfE", "--continue-on-collection-errors", "--timeout=900"]


def owned_tests(test_id, number):
    """Catalog numbers of every test in a file or directory (collection errors name those)."""
    path = test_id.rstrip("/")
    return [n for i, n in number.items() if i == path or i.startswith(path + "::") or i.startswith(path + "/")]


def failing_tests(stdout, number):
    """Map pytest -rfE short-summary lines to catalog numbers; return (failing, unknown, lines)."""
    failing, unknown, lines = set(), set(), []
    for line in stdout.splitlines():
        section = COLLECTING.match(line)
        if section:
            # Also covers output whose short summary is missing or cut off.
            matched = owned_tests(section[1], number)
            failing.update(matched)
            if not matched:
                unknown.add(section[1])
            continue
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
        matched = owned_tests(test_id, number)
        failing.update(matched)
        if not matched:
            unknown.add(test_id)
    return failing, unknown, lines


def interrupted(stdout):
    """Number of collection errors if pytest aborted the session before running tests, else None."""
    for line in stdout.splitlines():
        m = INTERRUPTED.match(line.strip())
        if m:
            return int(m[1])
    return None


def collection_errors(stdout):
    return sorted({m[1] for m in map(COLLECTING.match, stdout.splitlines()) if m})


def result_record(stdout, stderr, returncode, number, seconds):
    """The JSON written for one patch. An aborted session leaves tests outside the
    erroring files unrun, so it gets an `unknown` marker and is flagged as suspicious."""
    failing, unknown, lines = failing_tests(stdout, number)
    errors = interrupted(stdout)
    if errors is not None:
        unknown.add(f"session interrupted by {errors} collection errors: tests in other files did not run")
    summary = stdout.strip().splitlines()[-1] if stdout.strip() else stderr[-300:]
    # Keep the raw short-summary lines so results can be re-parsed without re-running.
    return {"failing": sorted(failing), "unknown": sorted(unknown), "exit": returncode, "summary": summary,
            "seconds": seconds, "summary_lines": lines, "collection_errors": collection_errors(stdout)}


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
        run = subprocess.run([sys.executable, "-m", "pytest", "tests", *PYTEST_ARGS], cwd=root, capture_output=True,
                             text=True, env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp",
                                             "SKIP_INTEGRATION": "0", "PYTHONDONTWRITEBYTECODE": "1"})
    record = result_record(run.stdout, run.stderr, run.returncode, number, round(time.time() - started, 1))
    Path(output).write_text(json.dumps(record))


if __name__ == "__main__":
    main(*sys.argv[1:])
