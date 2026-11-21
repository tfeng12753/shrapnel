"""Run one selected pytest item with its real child interpreters on a patched checkout."""
import os
from pathlib import Path
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET


def cpu_seconds():
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    return time.process_time() + children.ru_utime + children.ru_stime


def passed(source, name, after, item, wall_seconds):
    deadline = time.monotonic() + wall_seconds
    trace = os.environ.get("SHRAPNEL_TRACE_PYTEST") == "1"
    def outcome(ok, reason):
        if trace:
            print(f"PYTEST_CHECK {item} {reason}", file=sys.stderr)
        return ok
    file, *parts = item.split("::")
    source = source.resolve()
    relative = Path(name)
    if (relative.is_absolute() or ".." in relative.parts or not name.startswith("sqlglot/")
            or not (source / relative).is_file() or (source / relative).resolve() != source / relative):
        return False
    if not file.endswith(".py") or not parts or not all(part.isidentifier() for part in parts):
        return False
    classname = ".".join([file[:-3].replace("/", "."), *parts[:-1]])
    try:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "source"
            shutil.copytree(source, root, ignore=shutil.ignore_patterns(".git", ".pytest_cache", "__pycache__", "*.pyc"))
            (root / name).write_text(after, encoding="utf-8")
            report = Path(folder) / "result.xml"
            env = dict(os.environ, PYTHONPATH=str(root), PYTHONDONTWRITEBYTECODE="1", SKIP_INTEGRATION="0",
                       PATH=str(Path(sys.executable).parent) + os.pathsep + os.defpath)
            if time.monotonic() >= deadline:
                return outcome(False, "timeout during staging")
            with subprocess.Popen([sys.executable, "-B", "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider",
                                   f"--junitxml={report}", item], cwd=root, env=env, stdout=sys.stderr if trace else subprocess.DEVNULL,
                                  stderr=sys.stderr if trace else subprocess.DEVNULL, start_new_session=True) as proc:
                try:
                    status = proc.wait(timeout=max(0, deadline - time.monotonic()))
                    if status:
                        return outcome(False, f"pytest exit {status}")
                finally:
                    # The original test itself launches a Python interpreter.
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            tree = ET.parse(report)
            for suite in tree.iter("testsuite"):
                if any(int(suite.get(status, "0")) for status in ("errors", "failures", "skipped")):
                    return outcome(False, "incomplete test suite")
            cases = list(tree.iter("testcase"))
            ok = (len(cases) == 1 and cases[0].get("classname") == classname and cases[0].get("name") == parts[-1]
                    and all(cases[0].find(tag) is None for tag in ("skipped", "failure", "error")))
            return outcome(ok, "passed" if ok else "unexpected test report")
    except (OSError, ValueError, ET.ParseError, subprocess.SubprocessError) as exc:
        return outcome(False, type(exc).__name__)
