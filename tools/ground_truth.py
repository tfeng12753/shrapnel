"""Find which catalog tests really fail for one patch (evaluation only; run inside the image).

Copies the pinned checkout, applies the patch with the selector's own strict
patch reader, runs the full unchanged suite with real process pools, and writes
the failing catalog numbers. Subtest failures count against their owning test.

    docker run --rm -v $PWD:/repo:ro -v $PWD/mutants:/m blast-radius-entry \
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

ID = re.compile(r"^(?:FAILED|ERROR|SUBFAIL)\s+(\S+?)(?:\s|$)")


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
    failing, unknown = set(), set()
    for line in run.stdout.splitlines():
        m = ID.match(line)
        if not m:
            continue
        test_id = re.sub(r"\[.*$", "", m[1])
        if test_id in number:
            failing.add(number[test_id])
        else:
            # Collection errors name a file; every catalog test in it is affected.
            matched = [n for i, n in number.items() if i == test_id or i.startswith(test_id + "::")]
            failing.update(matched)
            if not matched:
                unknown.add(test_id)
    summary = run.stdout.strip().splitlines()[-1] if run.stdout.strip() else run.stderr[-300:]
    Path(output).write_text(json.dumps({"failing": sorted(failing), "unknown": sorted(unknown), "exit": run.returncode,
                                        "summary": summary, "seconds": round(time.time() - started, 1)}))


if __name__ == "__main__":
    main(*sys.argv[1:])
