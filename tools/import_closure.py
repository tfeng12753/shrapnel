"""Record the pinned files that `import sqlglot` executes or reads (build time only).

test_lazy_load is the only external test: its child process runs exactly
`import sqlglot` and checks which dialects loaded. A change to any other file
cannot change that child's behavior, so the selector can skip the test for it.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

REVISION = "eb11aec4be8cb2122afe713a4816662c3f0fb2fa"
TESTS = ["tests/dialects/test_dialect.py::TestDialect::test_lazy_load"]
CHILD = """
import json, os, sys
opened = set()
def audit(event, args):
    # Source reads only: bytecode cache writes are not inputs.
    if event != "open" or not isinstance(args[0], str):
        return
    path, mode, flags = args
    if mode is None:
        if flags & (os.O_WRONLY | os.O_RDWR):
            return
    elif any(c in mode for c in "wax+"):
        return
    opened.add(path)
sys.addaudithook(audit)
import sqlglot
files = {getattr(m, "__file__", None) for m in list(sys.modules.values())}
print(json.dumps(sorted(str(f) for f in files | opened if f)))
"""


def closure(root):
    root = root.resolve()
    # Same invocation as the test: `python -c ...` from the checkout root.
    output = subprocess.run([sys.executable, "-c", CHILD], cwd=root, capture_output=True, text=True, check=True).stdout
    files = set()
    for name in json.loads(output):
        path = (root / name).resolve()
        if path.is_relative_to(root) and "__pycache__" not in path.parts:
            files.add(path.relative_to(root).as_posix())
    if "sqlglot/__init__.py" not in files:
        raise RuntimeError("import closure did not include sqlglot/__init__.py")
    return sorted(files)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("/opt/sqlglot"))
    parser.add_argument("--output", type=Path, default=Path("/app/import-closure.json"))
    args = parser.parse_args()
    files = closure(args.source)
    args.output.write_text(json.dumps({"revision": REVISION, "tests": TESTS, "files": files}))
    print(f"wrote {args.output}: {len(files)} files", file=sys.stderr)
