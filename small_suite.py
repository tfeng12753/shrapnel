"""Confirm inexpensive selections with their original unittest methods on patched source."""
import os
from pathlib import Path
import sys
import tempfile
import unittest

from isolated_pytest import stage
from native_probes import _forked

LAZY = "tests/dialects/test_dialect.py::TestDialect::test_lazy_load"
LAZY_COMMAND = ["python", "-c", "import sqlglot; assert len(sqlglot.Dialect._classes) == 1; print('Success')"]
PROCESS_EVENTS = {"os.fork", "os.forkpty", "os.posix_spawn", "os.exec", "os.system", "subprocess.Popen"}


def verify_group(root, source, name, after, items):
    """A clean pass requires every expected method, including its class fixtures."""
    if any(key == "sqlglot" or key.startswith(("sqlglot.", "tests.")) or key == "tests" for key in sys.modules):
        return False
    stage(source, root, name, after)
    os.chdir(root)
    sys.path.insert(0, str(root))
    os.environ.update(PYTHONPATH=str(root), PYTHONDONTWRITEBYTECODE="1",
                      PATH=str(Path(sys.executable).parent) + os.pathsep + os.defpath)
    expected = {item["id"].replace("/", ".").replace(".py::", ".").replace("::", ".") for item in items}
    lazy_id = LAZY.replace("/", ".").replace(".py::", ".").replace("::", ".")
    active = None
    denied = False
    used_child = False

    def check_process(event, args):
        nonlocal denied, used_child
        if event not in PROCESS_EVENTS:
            return
        if event == "subprocess.Popen" and active == lazy_id and not used_child and args[1] == LAZY_COMMAND:
            used_child = True
            return
        denied = True
        raise RuntimeError("unexpected child process")

    sys.addaudithook(check_process)

    class Outcomes(unittest.TestResult):
        def startTest(self, test):
            nonlocal active
            active = test.id()
            super().startTest(test)

        def stopTest(self, test):
            nonlocal active
            active = None
            super().stopTest(test)

        def addSuccess(self, test):
            expected.discard(test.id())
            super().addSuccess(test)

    suite = unittest.TestSuite()
    for item in items:
        file, cls, method = item["id"].split("::")
        dotted = ".".join([file[:-3].replace("/", "."), cls, method])
        test, = unittest.defaultTestLoader.loadTestsFromName(dotted)
        if (not isinstance(test, unittest.TestCase) or test.id() != dotted or
                getattr(type(test), "pytestmark", ()) or getattr(getattr(test, method), "pytestmark", ())):
            return False
        suite.addTest(test)
    result = Outcomes()
    result.failfast = True
    suite.run(result)
    return result.wasSuccessful() and not result.skipped and not expected and not denied


def refine(selector, patch, mask):
    from selector import REGISTRY_FILES, apply_change, parse_patch, plain_text_change

    if not selector.complete or not 0 < mask.bit_count() <= 64 or not plain_text_change(patch):
        return mask
    items = selector.chosen(mask)
    if sum(item["seconds"] for item in items) > 1.25 or any(item["id"] not in selector.native for item in items):
        return mask
    changes = parse_patch(patch)
    if len(changes) != 1:
        return mask
    change, = changes
    name = change["old"]
    if name != change["new"] or not name.startswith("sqlglot/") or not name.endswith(".py") or ".." in Path(name).parts:
        return mask
    if name in REGISTRY_FILES and any(item["id"] != LAZY for item in items):
        return mask
    try:
        after, _, _ = apply_change(selector.read_source(name), change)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "source"
            passed, _ = _forked(lambda: verify_group(root, selector.source.resolve(), name, after, items),
                                False, wall_seconds=2.5, cpu_seconds=.65, strict_cpu=True)
    except (OSError, ValueError, UnicodeError, RecursionError):
        return mask
    return 0 if passed is True else mask
