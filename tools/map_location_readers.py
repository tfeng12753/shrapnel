"""Index baseline test contexts that may observe Python source locations."""
import argparse
import ast
import gzip
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dict_keys import catalog_digest
from selector import REVISION


LOCATION_NAMES = frozenset("""
    __code__ __traceback__ _getframe currentframe exc_info f_code f_lineno tb_frame tb_lineno
    co_firstlineno co_lines co_lnotab co_linetable co_positions f_lasti tb_lasti
    getframeinfo getinnerframes getouterframes getsourcelines getsource findsource
    extract_stack extract_tb format_stack format_tb format_exc print_exc print_stack
    stack walk_stack walk_tb settrace setprofile monitoring warn warn_explicit
    linecache traceback inspect dis logging warnings assertLogs captureWarnings
    getlineno getfile getsourcefile getmembers getmembers_static getinstructions
""".split())


def may_observe(node):
    if isinstance(node, ast.Attribute):
        return node.attr in LOCATION_NAMES
    if isinstance(node, ast.Name):
        return node.id in LOCATION_NAMES
    if isinstance(node, ast.Constant) and type(node.value) is str:
        return node.value in LOCATION_NAMES
    if isinstance(node, ast.Call):
        func = node.func
        name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
        if name in ("eval", "exec", "compile", "attrgetter", "methodcaller", "__getattribute__"):
            return True
        if name in ("getattr", "hasattr"):
            return len(node.args) < 2 or not isinstance(node.args[1], ast.Constant)
    return False


def build(source, map_path):
    with gzip.open(map_path, "rt") as stream:
        data = json.load(stream)
    if data["complete"] is not True or data["revision"] != REVISION:
        raise ValueError("a complete map of the pinned checkout is required")
    mask = int(data["external"], 16)
    test_bits = {name: 1 << i for i, name in enumerate(data["tests"])}
    for name, info in data["files"].items():
        if not name.endswith(".py"):
            continue
        tree = ast.parse((source / name).read_bytes())
        # Follow local aliases, including an imported name with an alias.
        indirect = LOCATION_NAMES | {"getattr", "hasattr", "eval", "exec", "compile"}
        aliases = {n.asname for n in ast.walk(tree) if isinstance(n, ast.alias) and n.asname
                   and n.name in indirect}
        for n in ast.walk(tree):
            if isinstance(n, ast.Assign) and isinstance(n.value, ast.Name) and n.value.id in indirect:
                aliases.update(target.id for target in n.targets if isinstance(target, ast.Name))
        scopes = []
        if name.startswith("tests/"):
            for cls in tree.body:
                if isinstance(cls, ast.ClassDef):
                    for method in cls.body:
                        if isinstance(method, ast.FunctionDef):
                            bit = test_bits.get(f"{name}::{cls.name}::{method.name}", 0)
                            if bit:
                                scopes.append((method.lineno, method.end_lineno, bit))
        for node in ast.walk(tree):
            if may_observe(node) or (isinstance(node, ast.Name) and node.id in aliases):
                line = str(node.lineno)
                mask |= int(info["lines"].get(line, info.get("anchors", {}).get(line, "0")), 16)
                for start, end, bit in scopes:
                    if start <= node.lineno <= end:
                        mask |= bit
    return {"revision": REVISION, "catalog": catalog_digest(data["tests"]), "mask": format(mask, "x"),
            "map_sha256": hashlib.sha256(map_path.read_bytes()).hexdigest()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for opt in ("source", "map", "output"):
        parser.add_argument("--" + opt, type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(build(args.source, args.map), indent=2) + "\n")
