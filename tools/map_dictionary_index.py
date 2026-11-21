"""Index baseline dictionary values and optional comparison sites for offline replay."""
import argparse
import ast
import gzip
import hashlib
import json
from pathlib import Path

from selector import relation_outcome
from tools.import_closure import REVISION


def scalar_sites(source_text, spans):
    """Index byte offsets of ordinary scalar values in mapped constant dictionaries."""
    sites = {}
    for node in ast.walk(ast.parse(source_text)):
        if (not isinstance(node, (ast.Assign, ast.AnnAssign)) or (node.lineno, node.end_lineno) not in spans
                or not isinstance(node.value, ast.Dict)):
            continue
        keys = node.value.keys
        if not all(isinstance(key, ast.Constant) and type(key.value) is str for key in keys):
            continue
        if len({key.value for key in keys}) != len(keys):
            continue
        for value in node.value.values:
            if (isinstance(value, ast.Constant) and value.lineno == value.end_lineno
                    and type(value.value) in (int, bool, float, str)):
                sites.setdefault(str(value.lineno), []).append(
                    [value.col_offset, value.end_col_offset, type(value.value).__name__]
                )
    return sites


def one_sided(record):
    if int(record.get("unknown", "0"), 16):
        return False
    outcomes = {relation_outcome(state, record["op"]) for state, mask in record["states"].items()
                if int(mask, 16)}
    return outcomes == {True} or outcomes == {False}


def build_index(map_path, source, include_comparisons=False):
    source = source.resolve()
    opener = gzip.open if map_path.suffix == ".gz" else open
    with opener(map_path, "rt") as stream:
        data = json.load(stream)
    if data.get("revision") != REVISION or data.get("version") != 1 or data.get("complete") is not True:
        raise ValueError("requires a complete dependency map for the pinned revision")
    files = {}
    for name, entry in data["files"].items():
        records = entry.get("dictionary_reads", {})
        comparisons = {site: record["op"] for site, record in entry.get("comparisons", {}).items()
                       if include_comparisons and one_sided(record)}
        if not (records or comparisons) or not name.startswith("sqlglot/") or not name.endswith(".py"):
            continue
        path = (source / name).resolve()
        if not path.is_relative_to(source):
            raise ValueError("dictionary path outside source tree")
        raw = path.read_bytes()
        spans = {(v["start"], v["end"]) for v in records.values()}
        sites = scalar_sites(raw.decode("utf-8"), spans) if spans else {}
        if sites or comparisons:
            files[name] = {"sha256": hashlib.sha256(raw).hexdigest(), "values": sites}
            if comparisons:
                files[name]["comparisons"] = comparisons
    return {"revision": REVISION, "format": "dictionary-values-v1", "files": files}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("source", "map", "output"):
        parser.add_argument(f"--{option}", required=True, type=Path)
    parser.add_argument("--include-comparisons", action="store_true", help="Include comparison sites with one known baseline outcome")
    args = parser.parse_args()
    args.output.write_text(json.dumps(build_index(args.map, args.source, args.include_comparisons), separators=(",", ":")))
