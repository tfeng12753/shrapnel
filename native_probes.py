"""Bounded native-unittest verification. Only confirmed passes are removed."""
from __future__ import annotations
import ast
import builtins
import importlib.machinery
import io
import json
import linecache
import math
import os
from pathlib import Path
import resource
import select
import signal
import sys
import time
import unittest


def _install_overlays(source, overlays):
    """Make imports and explicit reads in this (forked) process see patched source."""
    os.chdir(source)
    sys.path.insert(0, str(source))
    sys.dont_write_bytecode = True
    os.environ["SKIP_INTEGRATION"] = "0"
    os.environ["HOME"] = "/tmp"
    resolved = {str((source / name).resolve()): text for name, text in overlays.items()}
    original_code = importlib.machinery.SourceFileLoader.get_code
    def get_code(loader, fullname):
        filename = str(Path(loader.get_filename(fullname)).resolve())
        if filename in resolved:
            return compile(resolved[filename], filename, "exec", dont_inherit=True)
        return original_code(loader, fullname)
    importlib.machinery.SourceFileLoader.get_code = get_code
    for filename, text in resolved.items():
        linecache.cache[filename] = (len(text), None, text.splitlines(True), filename)
    # Explicit source readers must see the same patched source as imports.
    for module in (builtins, io):
        original = module.open
        def opened(file, mode="r", *args, _original=original, **kwargs):
            if not isinstance(file, int) and mode in ("r", "rt", "rb"):
                filename = str(Path(file).resolve())
                if filename in resolved:
                    stream = io.BytesIO(resolved[filename].encode("utf-8")) if "b" in mode else io.StringIO(resolved[filename])
                    stream.name = filename
                    return stream
            return _original(file, mode, *args, **kwargs)
        module.open = opened


def _import_child(source, overlays, module):
    """Import sqlglot and the patched module; any exception means failure."""
    _install_overlays(source, overlays)
    importlib.import_module("sqlglot")
    importlib.import_module(module)
    return True


def _child(source, overlays, tests):
    """The caller forks first; no source module is imported in the parent."""
    _install_overlays(source, overlays)
    expected = {}
    suite = unittest.TestSuite()
    loader = unittest.TestLoader()
    for test in sorted(tests, key=lambda t: t["id"]):
        file, *names = test["id"].split("::")
        dotted = ".".join([file.removesuffix(".py").replace("/", "."), *names])
        expected[dotted] = test["id"]
        loaded = loader.loadTestsFromName(dotted)
        # Runtime marks may differ from the baseline. Fail closed if unittest
        # cannot reproduce the pytest item's fixture/mark behavior.
        def eligible(node):
            if isinstance(node, unittest.TestSuite):
                return all(eligible(child) for child in node)
            return isinstance(node, unittest.TestCase) and not getattr(type(node), "pytestmark", ()) and not getattr(getattr(node, node._testMethodName, None), "pytestmark", ())
        if not eligible(loaded):
            return []
        suite.addTests(loaded)
    class Result(unittest.TestResult):
        def __init__(self):
            super().__init__()
            self.passed, self.failed, self.unknown = set(), set(), False
        def addSuccess(self, test):
            super().addSuccess(test)
            if test.id() in expected:
                self.passed.add(test.id())
        def addSkip(self, test, reason):
            super().addSkip(test, reason)
            if test.id() in expected:
                self.passed.add(test.id())
        def addFailure(self, test, err):
            super().addFailure(test, err)
            self.failed.add(test.id())
            self.unknown |= test.id() not in expected
        def addError(self, test, err):
            super().addError(test, err)
            self.failed.add(test.id())
            self.unknown |= test.id() not in expected
        def addSubTest(self, test, subtest, err):
            super().addSubTest(test, subtest, err)
            if err is not None:
                self.failed.add(test.id())
        def addUnexpectedSuccess(self, test):
            super().addUnexpectedSuccess(test)
            # Pytest/unittest differ on some xfail conventions; retain the test.
            self.failed.add(test.id())
    result = Result()
    suite.run(result)
    if result.unknown or loader.errors:
        return []
    return [expected[name] for name in result.passed - result.failed]


def verify(source, overlays, tests, wall_seconds=1.5, cpu_seconds=1.0):
    """Return (confirmed passing IDs, consumed child CPU); timeouts remove nothing."""
    passed, cpu = _forked(lambda: _child(source.resolve(), overlays, tests), [], wall_seconds, cpu_seconds)
    allowed = {test["id"] for test in tests}
    if not isinstance(passed, list) or any(item not in allowed for item in passed):
        return [], cpu
    return passed, cpu


def verify_import(source, overlays, module, wall_seconds=4.0, cpu_seconds=2.0):
    """Return (True if the patched module imports cleanly, consumed child CPU)."""
    ok, cpu = _forked(lambda: _import_child(source.resolve(), overlays, module), False, wall_seconds, cpu_seconds)
    return ok is True, cpu


def _forked(work, failure, wall_seconds, cpu_seconds):
    """Run work() in a limited forked child; return (its JSON result or failure, child CPU)."""
    read_fd, write_fd = os.pipe()
    sys.stdout.flush()
    pid = os.fork()
    if pid == 0:
        os.close(read_fd)
        try:
            os.setsid()
            null = os.open(os.devnull, os.O_RDWR)
            os.dup2(null, 1)
            os.dup2(null, 2)
            os.close(null)
            cap = max(1, math.ceil(cpu_seconds))
            resource.setrlimit(resource.RLIMIT_CPU, (cap, cap + 1))
            signal.signal(signal.SIGALRM, signal.SIG_DFL)
            signal.setitimer(signal.ITIMER_REAL, wall_seconds)
            try:
                result = work()
            except BaseException:
                result = failure
            payload = json.dumps(result).encode("utf-8")
            while payload:
                count = os.write(write_fd, payload)
                payload = payload[count:]
            os.close(write_fd)
        finally:
            os._exit(0)
    os.close(write_fd)
    chunks, expired = [], False
    deadline = time.monotonic() + wall_seconds + .25
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                expired = True
                break
            ready, _, _ = select.select([read_fd], [], [], min(.1, remaining))
            if ready:
                chunk = os.read(read_fd, 65536)
                if not chunk:
                    break
                chunks.append(chunk)
                if sum(map(len, chunks)) > 1_000_000:
                    expired = True
                    break
    finally:
        os.close(read_fd)
        if expired:
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        _, status, usage = os.wait4(pid, 0)
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    cpu = usage.ru_utime + usage.ru_stime
    if expired or status:
        return failure, cpu
    try:
        return json.loads(b"".join(chunks)), cpu
    except (ValueError, TypeError):
        return failure, cpu
