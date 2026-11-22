"""Merge maps built from the same builder under different PYTHONHASHSEED values.

    for seed in 0 1; do PYTHONHASHSEED=$seed PYTHONPATH=/opt/sqlglot \\
        python tools/build_map.py --source /opt/sqlglot --output /tmp/seed$seed.json.gz; done
    python tools/map_merge_seeds.py /tmp/seed0.json.gz /tmp/seed1.json.gz --output /tmp/map.json.gz

Every dependency mask is the union over seeds, so a test seen under any seed
is kept. In `exact_comparisons`, `complete` is the intersection, a test whose
set of states differs between seeds is removed from it, and `unknown` gains
every test that is not complete. A site missing under one seed is dropped.
"""
import argparse
import gzip
import json
from pathlib import Path


def union(a, b):
    return format(int(a, 16) | int(b, 16), "x")


def merge_masks(a, b):
    """Union two {key: hex} objects; keys present in either are kept."""
    result = dict(a)
    for key, value in b.items():
        result[key] = union(result[key], value) if key in result else value
    return result


def merge_exact(records):
    if len({record["op"] for record in records}) != 1:
        return None
    complete, seen = -1, 0
    states = {}
    for record in records:
        complete &= int(record["complete"], 16)
        seen |= int(record["complete"], 16) | int(record["unknown"], 16)
        for state, value in record["states"].items():
            states[state] = states.get(state, 0) | int(value, 16)
    # A complete test must show the same states under every seed.
    for state in states:
        present = [int(record["states"].get(state, "0"), 16) for record in records]
        everywhere, anywhere = -1, 0
        for value in present:
            everywhere &= value
            anywhere |= value
        complete &= ~(anywhere & ~everywhere)
    complete = max(complete, 0)
    return {"op": records[0]["op"], "complete": format(complete, "x"),
            "states": {state: format(value, "x") for state, value in states.items()},
            "unknown": format(seen & ~complete, "x")}


def merge_files(a, b):
    result = dict(a)
    for field in ("lines", "anchors"):
        result[field] = merge_masks(a.get(field, {}), b.get(field, {}))
    for field in ("readers", "executed"):
        result[field] = union(a.get(field, "0"), b.get(field, "0"))
    regions = {(r[0], r[1], r[2]): r[3] for r in a.get("regions", [])}
    for start, end, kind, value in b.get("regions", []):
        key = (start, end, kind)
        regions[key] = union(regions[key], value) if key in regions else value
    result["regions"] = [[start, end, kind, value] for (start, end, kind), value in regions.items()]
    result["setup_classes"] = merge_masks(a.get("setup_classes", {}), b.get("setup_classes", {}))
    if not result["setup_classes"]:
        del result["setup_classes"]
    comparisons = {}
    for site in set(a.get("comparisons", {})) | set(b.get("comparisons", {})):
        if site not in a.get("comparisons", {}) or site not in b.get("comparisons", {}):
            # Present under one seed only: the other saw it outside a test.
            continue
        x, y = a["comparisons"][site], b["comparisons"][site]
        merged = dict(x)
        merged["states"] = merge_masks(x["states"], y["states"])
        merged["unknown"] = union(x["unknown"], y["unknown"])
        comparisons[site] = merged
    if comparisons or "comparisons" in a:
        result["comparisons"] = comparisons
    exact = {}
    for site in set(a.get("exact_comparisons", {})) & set(b.get("exact_comparisons", {})):
        merged = merge_exact([a["exact_comparisons"][site], b["exact_comparisons"][site]])
        if merged is not None:
            exact[site] = merged
    if exact:
        result["exact_comparisons"] = exact
    elif "exact_comparisons" in result:
        del result["exact_comparisons"]
    dictionaries = {}
    for symbol in set(a.get("dictionary_reads", {})) | set(b.get("dictionary_reads", {})):
        x = a.get("dictionary_reads", {}).get(symbol)
        y = b.get("dictionary_reads", {}).get(symbol)
        if x is None or y is None:
            dictionaries[symbol] = x or y
            continue
        merged = dict(x)
        for field in ("keys", "presence", "lines"):
            merged[field] = merge_masks(x.get(field, {}), y.get(field, {}))
        for field in ("whole", "transferred", "unobserved"):
            merged[field] = union(x.get(field, "0"), y.get(field, "0"))
        merged["shared"] = sorted(set(x.get("shared", [])) | set(y.get("shared", [])))
        dictionaries[symbol] = merged
    if dictionaries:
        result["dictionary_reads"] = dictionaries
    return result


def merge(maps):
    result = maps[0]
    for other in maps[1:]:
        if other["tests"] != result["tests"] or other["revision"] != result["revision"]:
            raise SystemExit("maps differ in tests or revision; build both from the same checkout")
        if not other.get("complete") or not result.get("complete"):
            raise SystemExit("refusing an incomplete map")
        result["external"] = union(result["external"], other["external"])
        result["native"] = sorted(set(result["native"]) & set(other["native"]))
        files = {}
        for name in set(result["files"]) | set(other["files"]):
            x, y = result["files"].get(name), other["files"].get(name)
            files[name] = merge_files(x, y) if x is not None and y is not None else (x or y)
        result["files"] = dict(sorted(files.items()))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("maps", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    maps = [json.loads(gzip.decompress(path.read_bytes())) for path in args.maps]
    merged = merge(maps)
    args.output.write_bytes(gzip.compress(json.dumps(merged, separators=(",", ":")).encode(), mtime=0))
    print(f"merged {len(maps)} maps into {args.output}")


if __name__ == "__main__":
    main()
