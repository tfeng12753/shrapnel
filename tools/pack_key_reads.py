"""Extract per-key observations present in both complete pinned-checkout maps."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dict_keys import catalog_digest
from selector import REVISION


def read(path):
    with gzip.open(path, "rt") as stream:
        return json.load(stream)


def union(a, b):
    return format(int(a, 16) | int(b, 16), "x")


def pack(source, control, first, second):
    maps = [read(path) for path in (control, first, second)]
    names = maps[0]["tests"]
    if any(m["revision"] != REVISION or m["version"] != 1 or m["complete"] is not True
           or m["tests"] != names for m in maps):
        raise ValueError("incomplete map or mismatched checkout or test inventory")
    base, a, b = [m["files"] for m in maps]
    files = {}
    for name in sorted(base.keys() & a.keys() & b.keys()):
        left, right = (f[name].get("dictionary_reads", {}) for f in (a, b))
        records = {}
        for symbol in sorted(left.keys() & right.keys()):
            x, y = left[symbol], right[symbol]
            if (x["start"], x["end"]) != (y["start"], y["end"]):
                continue
            masks = [int(v, 16) for start, end, kind, v in base[name]["regions"]
                     if (start, end, kind) == (x["start"], x["end"], "value")]
            if len(masks) != 1:
                continue
            rec = {"start": x["start"], "end": x["end"], "shared": sorted(set(x["shared"] + y["shared"]))}
            for field in ("whole", "unobserved", "transferred"):
                rec[field] = union(x[field], y[field])
            for field in ("keys", "presence", "lines"):
                rec[field] = {k: union(x[field].get(k, "0"), y[field].get(k, "0"))
                              for k in sorted(x[field].keys() | y[field].keys())}
            # A control-map user must be explained independently under both seeds.
            for seed in (x, y):
                seen = int(seed["whole"], 16) | int(seed["transferred"], 16)
                for field in ("keys", "presence"):
                    for value in seed[field].values():
                        seen |= int(value, 16)
                rec["unobserved"] = union(rec["unobserved"], format(masks[0] & ~seen, "x"))
            records[symbol] = rec
        if records:
            files[name] = {"sha256": hashlib.sha256((source / name).read_bytes()).hexdigest(), "records": records}
    if not files:
        raise ValueError("no common dictionary observations")
    return {"version": 1, "revision": REVISION, "catalog": catalog_digest(names), "files": files}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "control", "first", "second", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    data = pack(args.source, args.control, args.first, args.second)
    args.output.write_bytes(gzip.compress(json.dumps(data, separators=(",", ":")).encode(), mtime=0))
