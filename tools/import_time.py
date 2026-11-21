"""Build-time index of what importing each test module executes in the pinned library.

Some library modules run library code while they are being imported (for
example a class body that parses SQL to build a constant). If a patch changes
code or a setting used during that import, the import itself can fail, and
every test in every test file that (transitively) imports the module errors
during collection, even tests that never use the changed code. The dependency
map can't see this: collection-time execution belongs to no test.

This index records, for the pinned checkout:

* ``closures``: for every test file, the library and test modules it imports
  (each traced in a fresh interpreter);
* ``import_time``: for every library or test module whose own import runs
  library functions (the "owner"), the library source lines those functions
  executed and the uppercase names (settings, constants) referenced on them.

The selector uses it to decide when a patch could break an import and which
test files to re-import to find out.

    python3 tools/import_time.py --source /opt/<pinned> --output /app/import-time.json
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

REVISION = "eb11aec4be8cb2122afe713a4816662c3f0fb2fa"
PACKAGE = "sqlglot"
SETTING = re.compile(r"\b([A-Z][A-Z0-9_]{2,})\b")

CLOSURE_CHILD = r"""
import json, sys
sys.path.insert(0, ".")
before = set(sys.modules)
try:
    __import__(sys.argv[1])
    ok = True
except BaseException:
    ok = False
print(json.dumps({"ok": ok, "modules": sorted(m for m in sys.modules if m.split(".")[0] in (%(pkg)r, "tests"))}))
""" % {"pkg": PACKAGE}

TRACE_CHILD = r"""
import json, os, sys
sys.path.insert(0, ".")
root = os.path.realpath(%(pkg)r) + os.sep
owners = (root, os.path.realpath("tests") + os.sep)
executed = {}

def importer(frame):
    # Nearest enclosing module-level frame inside the library: the module being imported.
    f = frame.f_back
    while f is not None:
        code = f.f_code
        if code.co_name == "<module>":
            path = os.path.realpath(code.co_filename)
            if path.startswith(owners):
                return path
        f = f.f_back
    return None

def tracer(frame, event, arg):
    code = frame.f_code
    # Only real functions (not module or class bodies): class-body lines run at
    # every import, and their values reach import-time code through names.
    if event != "call" or not code.co_flags & 0x02:  # CO_NEWLOCALS
        return None
    path = os.path.realpath(code.co_filename)
    if not path.startswith(root):
        return None
    owner = importer(frame)
    if owner is None:
        return None
    lines = executed.setdefault(owner, {}).setdefault(path, set())
    def local(frame, event, arg):
        if event == "line":
            lines.add(frame.f_lineno)
        return local
    lines.add(frame.f_lineno)
    return local

sys.settrace(tracer)
failed = []
for name in json.loads(sys.argv[1]):
    try:
        __import__(name)
    except BaseException:
        failed.append(name)
sys.settrace(None)
print(json.dumps({"failed": failed, "executed": {m: {p: sorted(l) for p, l in files.items()} for m, files in executed.items()}}))
""" % {"pkg": PACKAGE}


def test_modules(root):
    names = []
    for path in sorted((root / "tests").rglob("test_*.py")):
        rel = path.relative_to(root).with_suffix("")
        if all(part.isidentifier() for part in rel.parts):
            names.append(".".join(rel.parts))
    return names


def module_file(root, module):
    base = root / Path(*module.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate.relative_to(root).as_posix()
    return None


def run(root, code, *args):
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "SKIP_INTEGRATION": "0"}
    out = subprocess.run([sys.executable, "-c", code, *args], cwd=root, capture_output=True, text=True, env=env, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


def build(root):
    root = root.resolve()
    modules = test_modules(root)
    with ThreadPoolExecutor(max_workers=max(1, min(4, os.cpu_count() or 1))) as pool:
        results = list(pool.map(lambda m: run(root, CLOSURE_CHILD, m), modules))
    closures = {}
    for module, result in zip(modules, results):
        test_file = module_file(root, module)
        if test_file is None:
            continue
        if not result["ok"]:
            raise RuntimeError(f"baseline import of {module} failed")
        closures[test_file] = sorted(f for f in (module_file(root, m) for m in result["modules"]) if f)
    traced = run(root, TRACE_CHILD, json.dumps(modules))
    if traced["failed"]:
        raise RuntimeError(f"baseline import failed while tracing: {traced['failed'][:3]}")
    import_time = {}
    for owner, files in traced["executed"].items():
        owner_rel = Path(owner).relative_to(root).as_posix()
        lines, attrs = {}, set()
        for path, numbers in files.items():
            rel = Path(path).relative_to(root).as_posix()
            lines[rel] = numbers
            source = Path(path).read_text(encoding="utf-8").splitlines()
            for n in numbers:
                if 0 < n <= len(source):
                    attrs.update(SETTING.findall(source[n - 1]))
        import_time[owner_rel] = {"lines": lines, "attrs": sorted(attrs)}
    return {"revision": REVISION, "closures": closures, "import_time": import_time}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    index = build(args.source)
    args.output.write_text(json.dumps(index, separators=(",", ":")))
    print(f"wrote {args.output}: {len(index['closures'])} test files, {len(index['import_time'])} modules with import-time execution",
          file=sys.stderr)
