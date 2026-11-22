"""Pinned token evidence for one boolean parser dependency."""

import ast
import hashlib
import json
from pathlib import Path

DIALECT = "sqlglot/dialects/dialect.py"
TEST = "tests/test_parser.py::TestParser::test_max_nodes"
FLAG = "DPIPE_IS_STRING_CONCAT"
TOKEN = "DPIPE"
EVIDENCE = Path(__file__).with_name("token-vocabulary.json")
FORMAT = 1


def fingerprint(root):
    """Bind every package source and test input to the evidence."""
    paths = sorted(p for folder in ("sqlglot", "tests") for p in (root / folder).rglob("*")
                   if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")
    if not paths or not (root / DIALECT).is_file() or not (root / "tests/test_parser.py").is_file():
        raise ValueError("missing pinned inputs")
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        digest.update(hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).digest())
    return digest.hexdigest()


def verify_gate(root):
    """Require the sole flag read to guard a match of the required token."""
    reads = []
    expected = ast.dump(ast.parse(f"self.dialect.{FLAG} and self._match(TokenType.{TOKEN})", mode="eval").body)
    for path in sorted((root / "sqlglot").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and FLAG in node.value:
                raise ValueError("indirect flag reference")
            if isinstance(node, ast.Name) and node.id == FLAG and isinstance(node.ctx, ast.Load):
                raise ValueError("unqualified flag read")
            if isinstance(node, ast.Attribute) and node.attr == FLAG:
                if not isinstance(node.ctx, ast.Load):
                    raise ValueError("dynamic flag write")
                parent = parents[node]
                if path != root / "sqlglot/parser.py" or ast.dump(parent) != expected:
                    raise ValueError("unsupported flag read")
                reads.append(node.lineno)
    if len(reads) != 1:
        raise ValueError("ambiguous flag dependency")
    return {"path": "sqlglot/parser.py", "line": reads[0], "expression": expected}


def flag_value(tree):
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Dialect"]
    if len(classes) != 1:
        raise ValueError("ambiguous class")
    assignments = [n for n in classes[0].body if isinstance(n, ast.Assign)
                   and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)
                   and n.targets[0].id == FLAG and isinstance(n.value, ast.Constant)
                   and type(n.value.value) is bool]
    if len(assignments) != 1:
        raise ValueError("unsupported assignment")
    return assignments[0].value


def load_evidence(root, revision, path=EVIDENCE):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if (data["format"] != FORMAT or data["revision"] != revision
                or (root / ".pinned-revision").read_text().strip() != revision
                or data["test"] != TEST or data["flag"] != FLAG or data["required_token"] != TOKEN
                or data["fingerprint"] != fingerprint(root) or data["gate"] != verify_gate(root)
                or data["capture"] != "raw-and-current-v1" or data["passed"] is not True
                or type(data["advances"]) is not int or data["advances"] <= 0
                or not isinstance(data["tokens"], list) or not data["tokens"]
                or any(not isinstance(t, str) for t in data["tokens"])):
            return None
        return data
    except (OSError, ValueError, KeyError, TypeError, SyntaxError, RecursionError):
        return None


def refine(selector, patch, mask, parse_patch, apply_change):
    target = selector.test_bits.get(TEST, 0)
    if not target & mask or not selector.complete or selector.conservative_python:
        return mask
    evidence = selector.token_evidence
    if evidence is None or TOKEN in evidence["tokens"]:
        return mask
    try:
        # Reject metadata edits and unsupported diff sections as well as mixed files.
        from selector import plain_text_change
        if (not plain_text_change(patch) or any(line.startswith(("new file mode ", "deleted file mode ",
                                                               "similarity index ", "dissimilarity index "))
                                               for line in patch.splitlines())):
            return mask
        edits = parse_patch(patch)
        if len(edits) != 1 or edits[0]["old"] != DIALECT or edits[0]["new"] != DIALECT:
            return mask
        entry = selector.files.get(DIALECT)
        if entry is None or target & (entry["readers"] | selector.external | selector.child_work):
            return mask
        old = selector.read_source(DIALECT)
        if old != (selector.source / DIALECT).read_text(encoding="utf-8"):
            return mask
        new, _, _ = apply_change(old, edits[0])
        compile(new, DIALECT, "exec")
        a, b = (ast.parse(s, type_comments=True) for s in (old, new))
        av, bv = flag_value(a), flag_value(b)
        if av.value == bv.value:
            return mask
        bv.value = av.value
        if ast.dump(a) == ast.dump(b):
            return mask & ~target
    except (OSError, ValueError, KeyError, TypeError, SyntaxError, RecursionError):
        pass
    return mask
