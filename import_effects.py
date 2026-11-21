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


def assigned_names(source, lines, tree=None):
    """Names whose value a change to the given lines can alter.

    Uppercase names (settings, constants) assigned anywhere, and any name
    assigned directly in a class body (class attributes such as ``arg_types``),
    for statements containing any of the given lines.
    """
    names = set()
    try:
        tree = tree if tree is not None else _tree(source)
    except SyntaxError:
        return names
    class_level = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            class_level.update(id(stmt) for stmt in node.body)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            continue
        if not any(node.lineno <= line <= node.end_lineno for line in lines):
            continue
        in_class = id(node) in class_level
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            for sub in ast.walk(target):
                name = sub.id if isinstance(sub, ast.Name) else sub.attr if isinstance(sub, ast.Attribute) else None
                if name and (NAME.fullmatch(name) or (in_class and isinstance(sub, ast.Name))):
                    names.add(name)
    return names


def enclosing_classes(source, lines, tree=None):
    """Qualified names of the classes whose bodies contain any of the given lines."""
    found = set()
    try:
        tree = tree if tree is not None else _tree(source)
    except SyntaxError:
        return found

    def visit(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                name = prefix + child.name
                if any(child.lineno <= line <= child.end_lineno for line in lines):
                    found.add(name)
                visit(child, name + ".")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, prefix + child.name + ".<locals>.")
            else:
                visit(child, prefix)
    visit(tree, "")
    return found


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
        def table(entries):
            return {owner: ({name: set(lines) for name, lines in info["lines"].items()}, set(info["attrs"]),
                            set(info.get("classes", ())))
                    for owner, info in entries.items()}
        self.import_time = table(index["import_time"])
        self.class_setup = table(index.get("class_setup", {}))
        self.closures = {test_file: set(modules) for test_file, modules in index["closures"].items()}
        self.file_tests, self.class_masks = {}, {}
        for test in tests:
            name = test.get("file") or test["id"].split("::", 1)[0]
            self.file_tests[name] = self.file_tests.get(name, 0) | (1 << test["number"])
            class_id = test["id"].rsplit("::", 1)[0]
            self.class_masks[class_id] = self.class_masks.get(class_id, 0) | (1 << test["number"])

    def owners(self, edits):
        """Modules whose import uses a changed line or a changed name."""
        return self._match(self.import_time, edits)

    def setup_owners(self, edits):
        """Test classes whose setUpClass uses a changed line or a changed name."""
        return self._match(self.class_setup, edits)

    def class_tests(self, class_ids):
        mask = 0
        for class_id in class_ids:
            mask |= self.class_masks.get(class_id, 0)
        return mask

    def _match(self, table, edits):
        """Entries of a trace table that ran a changed line or read a changed name.

        ``edits`` is a list of (path, old_source, changed_old_lines) or
        (path, old_source, changed_old_lines, parsed_old_tree or a function returning it).
        """
        changed, sources = {}, {}
        for edit in edits:
            path, source, lines = edit[:3]
            changed.setdefault(path, set()).update(lines)
            sources[path] = (source, edit[3] if len(edit) > 3 else None)
        parsed = {}

        def analysis(path):
            # (changed names, enclosing classes); parsed only when needed.
            if path not in parsed:
                source, tree = sources[path]
                tree = tree() if callable(tree) else tree
                lines = frozenset(changed[path])
                parsed[path] = (assigned_names(source, lines, tree),
                                {f"{path}::{q}" for q in enclosing_classes(source, lines, tree)})
            return parsed[path]

        found = set()
        for owner, (owner_lines, owner_names, owner_classes) in table.items():
            for path in changed:
                ran = owner_lines.get(path, set())
                if changed[path] & ran:
                    found.add(owner)  # A changed line ran during the import.
                    continue
                if not ran and not any(c.startswith(path + "::") for c in owner_classes):
                    continue  # The import neither runs this file's code nor uses its classes.
                names, classes = analysis(path)
                # A changed name is referenced by code the import ran, and that code
                # can reach it: the file's code ran, or the edited class was used.
                if names & owner_names and (ran or classes & owner_classes):
                    found.add(owner)
        return sorted(found)

    def dependents(self, owners):
        """Test files whose import closure includes any owner (or that are an owner)."""
        owners = set(owners)
        return sorted(f for f, closure in self.closures.items() if f in owners or closure & owners)

    def tests_of(self, files):
        mask = 0
        for name in files:
            mask |= self.file_tests.get(name, 0)
        return mask
