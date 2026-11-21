"""Find patches that can break test collection by changing what an import runs.

Some modules run library code while they are being imported (for example a
class body that parses SQL to build a constant). A patch that changes code or a
setting used during such an import can make the import fail, and then every test
in every test file that imports the module errors during collection, including
tests that never use the changed code. The dependency map can't see this
because collection-time execution belongs to no test.

``tools/import_time.py`` (run at image build) records which library lines and
uppercase names each module's import uses, and each test file's import
closure. ``ImportEffects`` turns a patch into the set of affected import
"owners"; the selector then imports those owners in a forked child with the
patch applied and adds every test of every test file whose closure includes an
owner that fails to import.
"""
import ast
from functools import lru_cache
import re

NAME = re.compile(r"\b([A-Z][A-Z0-9_]{2,})\b")


@lru_cache(maxsize=32)
def _tree(source):
    return ast.parse(source)


def assigned_names(source, lines):
    """Uppercase names assigned by statements that contain any of the given lines."""
    names = set()
    try:
        tree = _tree(source)
    except SyntaxError:
        return names
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            continue
        if not any(node.lineno <= line <= node.end_lineno for line in lines):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            for sub in ast.walk(target):
                if isinstance(sub, ast.Name) and NAME.fullmatch(sub.id):
                    names.add(sub.id)
                elif isinstance(sub, ast.Attribute) and NAME.fullmatch(sub.attr):
                    names.add(sub.attr)
    return names


def module_name(path):
    """Dotted module for a repository-relative .py path."""
    if not path.endswith(".py"):
        return None
    parts = path[:-3].split("/")
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) if parts and all(p.isidentifier() for p in parts) else None


class ImportEffects:
    def __init__(self, index, tests):
        self.import_time = {owner: ({name: set(lines) for name, lines in info["lines"].items()}, set(info["attrs"]))
                            for owner, info in index["import_time"].items()}
        self.closures = {test_file: set(modules) for test_file, modules in index["closures"].items()}
        self.file_tests = {}
        for test in tests:
            name = test.get("file") or test["id"].split("::", 1)[0]
            self.file_tests[name] = self.file_tests.get(name, 0) | (1 << test["number"])

    def owners(self, edits):
        """Owners whose import uses a changed line or a changed uppercase name.

        ``edits`` is a list of (path, old_source, changed_old_lines).
        """
        changed = {}
        names = set()
        for path, source, lines in edits:
            changed.setdefault(path, set()).update(lines)
            names |= assigned_names(source, frozenset(lines))
        found = []
        for owner, (owner_lines, owner_names) in self.import_time.items():
            if names & owner_names or any(changed[p] & owner_lines.get(p, ()) for p in changed):
                found.append(owner)
        # A changed module that is itself imported by tests is an owner of its own import.
        return sorted(set(found))

    def dependents(self, owners):
        """Test files whose import closure includes any owner (or that are an owner)."""
        owners = set(owners)
        return sorted(f for f, closure in self.closures.items() if f in owners or closure & owners)

    def tests_of(self, files):
        mask = 0
        for name in files:
            mask |= self.file_tests.get(name, 0)
        return mask
