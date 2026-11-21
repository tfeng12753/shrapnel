"""Fingerprint the dependency-map builder and verify a cached map against it.

A cached map is only valid for the builder that produced it. The fingerprint
covers the files that determine map content (line endings normalized), and the
metadata file records the build environment and the map's own digest.

    python3 tools/map_fingerprint.py --check maps/dependencies.meta.json --map maps/dependencies.json.gz
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sys

REVISION = "eb11aec4be8cb2122afe713a4816662c3f0fb2fa"
BUILDER_FILES = ["tools/build_map.py", "tools/instrument.py", "tools/setup_flow.py", "tools/fetch_source.py",
                 "requirements.lock"]


def builder_digest(root):
    digest = hashlib.sha256()
    for name in BUILDER_FILES:
        digest.update(name.encode() + b"\0")
        digest.update((Path(root) / name).read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return digest.hexdigest()


def map_digest(path):
    raw = Path(path).read_bytes()
    return hashlib.sha256(gzip.decompress(raw) if str(path).endswith(".gz") else raw).hexdigest()


def check(meta_path, map_path, root):
    """Return a list of problems; empty means the cached map matches this builder."""
    try:
        meta = json.loads(Path(meta_path).read_text())
    except (OSError, ValueError) as error:
        return [f"missing or unreadable map metadata ({type(error).__name__})"]
    problems = []
    if meta.get("revision") != REVISION:
        problems.append("map metadata names a different sqlglot revision")
    if meta.get("builder_sha256") != builder_digest(root):
        problems.append("map builder or instrumentation changed since the map was built; run `make map`")
    if meta.get("map_sha256") != map_digest(map_path):
        problems.append("cached map does not match its metadata")
    return problems


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--check", type=Path, required=True, help="metadata file to verify")
    parser.add_argument("--map", type=Path, required=True)
    args = parser.parse_args()
    found = check(args.check, args.map, args.root)
    for problem in found:
        print("cached map rejected:", problem, file=sys.stderr)
    raise SystemExit(1 if found else 0)
