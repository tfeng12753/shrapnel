"""Summarize a baseline map's storage and conservative dictionary observations."""
import argparse
from collections import Counter
import gzip
import json
from pathlib import Path


def profile(path):
    encoded = path.read_bytes()
    raw = gzip.decompress(encoded) if path.suffix == ".gz" else encoded
    data = json.loads(raw)
    sizes = Counter()
    bindings = keyed = candidates = 0
    for entry in data["files"].values():
        for name, value in entry.items():
            sizes[name] += len(json.dumps(value, separators=(",", ":")).encode())
        for record in entry.get("dictionary_reads", {}).values():
            bindings += 1
            if record["keys"]:
                keyed += 1
            observed = 0
            for mask in record["keys"].values():
                observed |= int(mask, 16)
            if "unobserved" in record:
                blocked = int(record["whole"], 16) | int(record["unobserved"], 16)
                candidates += bool(observed & ~blocked)
    return {
        "tests": len(data["tests"]), "files": len(data["files"]),
        "stored_bytes": len(encoded), "uncompressed_bytes": len(raw),
        "field_value_bytes": dict(sizes.most_common()),
        "dictionary_bindings": bindings, "bindings_with_key_reads": keyed,
        "bindings_with_any_key_reader_outside_whole_and_unobserved": candidates,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("map", type=Path)
    args = parser.parse_args()
    print(json.dumps(profile(args.map), indent=2))
