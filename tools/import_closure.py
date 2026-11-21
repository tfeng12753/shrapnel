"""Record the pinned files that `import sqlglot` executes or reads (build time only).

test_lazy_load is the only external test: its child process runs exactly
`import sqlglot` and checks which dialects loaded. A change to any other file
cannot change that child's behavior, so the selector can skip the test for it.
The optional unentered_functions records are hints for prioritizing a real
patched-child check. Code objects can be inspected without executing them.
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
explicit = set()
lines = {}
compiled = {}
entered = set()
inspected = set()
concurrent_process = False
root = os.getcwd() + os.sep
def inventory(code):
    if id(code) in compiled:
        return
    compiled[id(code)] = code
    for value in code.co_consts:
        if type(value) is type(code):
            inventory(value)

def started(code, offset):
    if code.co_filename.startswith(root):
        entered.add((code.co_filename, code.co_qualname, code.co_firstlineno))
        if code.co_name == "<module>":
            inventory(code)

def trace(frame, event, arg):
    name = frame.f_code.co_filename
    if not name.startswith(root):
        return None
    if event == "line":
        lines.setdefault(name, set()).add(frame.f_lineno)
    return trace
def audit(event, args):
    global concurrent_process
    if event in ("os.fork", "os.posix_spawn", "subprocess.Popen", "_posixsubprocess.fork_exec"):
        concurrent_process = True
    if event == "object.__getattr__" and args[1] == "__code__" and type(args[0]) is type(trace):
        module, name = args[0].__module__, args[0].__qualname__
        if type(module) is str and type(name) is str:
            inspected.add((module, name))
    # Source reads only: bytecode cache writes are not inputs.
    if event != "open" or not isinstance(args[0], str):
        return
    path, mode, flags = args
    if mode is None:
        if flags & os.O_ACCMODE == os.O_WRONLY:
            return
    elif "+" not in mode and any(c in mode for c in "wax"):
        return
    opened.add(path)
    frame = sys._getframe(1)
    if not (frame.f_code.co_name == "get_data" and frame.f_back and
            frame.f_back.f_code.co_name == "get_code" and
            frame.f_globals.get("__name__") in ("_frozen_importlib_external", "importlib._bootstrap_external")):
        explicit.add(path)
sys.addaudithook(audit)
# Monitoring observes Python function entries across threads. Missing monitor
# support leaves the optional function information unavailable.
monitor = getattr(sys, "monitoring", None)
tool = None
if monitor:
    tool = next((i for i in range(6) if monitor.get_tool(i) is None), None)
    if tool is not None:
        monitor.use_tool_id(tool, "import-function-observation")
        monitor.register_callback(tool, monitor.events.PY_START, started)
        monitor.set_events(tool, monitor.events.PY_START)
sys.settrace(trace)
import sqlglot
assert len(sqlglot.Dialect._classes) == 1
sys.settrace(None)
cold = {}
if tool is not None:
    monitor.set_events(tool, 0)
    monitor.free_tool_id(tool)
    if not concurrent_process:
        for code in compiled.values():
            path, name, line = code.co_filename, code.co_qualname, code.co_firstlineno
            # Only ordinary functions with their own locals. Generator and
            # coroutine entry/resume semantics need separate observation.
            if ((path, name, line) in entered or not (code.co_flags & 2)
                    or code.co_flags & (0x20 | 0x80 | 0x100 | 0x200) or "<" in name):
                continue
            module = os.path.relpath(path, root)[:-3].replace(os.sep, ".").removesuffix(".__init__")
            if any(m == module and (name == q or name.startswith(q + ".")) for m, q in inspected):
                continue
            cold.setdefault(path, []).append([name, line])
files = {getattr(m, "__file__", None) for m in list(sys.modules.values())}
print(json.dumps([sorted(str(f) for f in files | opened if f), {p: sorted(v) for p, v in lines.items()}, sorted(explicit), cold]))
"""


def observation(root):
    root = root.resolve()
    # Same invocation as the test: `python -c ...` from the checkout root.
    output = subprocess.run([sys.executable, "-c", CHILD], cwd=root, capture_output=True, text=True, check=True).stdout
    files = set()
    names, lines, explicit, cold = json.loads(output)
    for name in names:
        path = (root / name).resolve()
        if path.is_relative_to(root) and "__pycache__" not in path.parts:
            files.add(path.relative_to(root).as_posix())
    if "sqlglot/__init__.py" not in files:
        raise RuntimeError("import closure did not include sqlglot/__init__.py")
    def relative(name):
        return (root / name).resolve().relative_to(root).as_posix()
    raw = {relative(p) for p in explicit if (root / p).resolve().is_relative_to(root)}
    executed = {relative(p): values for p, values in lines.items()
                if Path(p).resolve().is_relative_to(root) and relative(p) in files}
    return {"files": sorted(files), "lines": dict(sorted(executed.items())),
            "explicit_reads": sorted(files & raw),
            "unentered_functions": {relative(p): sorted(items) for p, items in cold.items()
                                    if Path(p).resolve().is_relative_to(root) and relative(p) in files and relative(p) not in raw}}


def closure(root):
    return observation(root)["files"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("/opt/sqlglot"))
    parser.add_argument("--output", type=Path, default=Path("/app/import-closure.json"))
    args = parser.parse_args()
    data = observation(args.source)
    args.output.write_text(json.dumps({"revision": REVISION, "tests": TESTS, **data}))
    print(f"wrote {args.output}: {len(data['files'])} files", file=sys.stderr)
