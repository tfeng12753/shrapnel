"""Export a map built outside Docker (same pinned Python and coverage) to maps/.

    PYTHONPATH=/opt/sqlglot python tools/build_map.py --source /opt/sqlglot --output /tmp/map.json.gz
    python tools/map_export_local.py /tmp/map.json.gz

The builder files fingerprinted are this checkout's, so build from the same
commit you export from. The environment record must match the image's
(Python and coverage versions, machine); the script refuses otherwise.
"""
import argparse
import gzip
import json
from pathlib import Path
import platform
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from map_fingerprint import REVISION, builder_digest, map_digest  # noqa: E402

IMAGE_PYTHON = "3.13.18"
IMAGE_COVERAGE = "7.10.7"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("map", type=Path)
    args = parser.parse_args()
    import coverage
    environment = {"python": sys.version.split()[0], "coverage": coverage.__version__, "platform": platform.machine()}
    if (environment["python"], environment["coverage"], environment["platform"]) != (IMAGE_PYTHON, IMAGE_COVERAGE, "x86_64"):
        raise SystemExit(f"environment {environment} differs from the selector image; build in Docker instead")
    raw = gzip.decompress(args.map.read_bytes())
    metadata = json.loads(raw)
    if not metadata.get("complete") or metadata.get("revision") != REVISION:
        raise SystemExit("refusing incomplete/wrong-revision map")
    packed = gzip.compress(raw, compresslevel=9, mtime=0)
    if len(packed) > 18_000_000:
        raise SystemExit("map too large for repository allowance")
    root = Path(__file__).resolve().parents[1]
    output = root / "maps" / "dependencies.json.gz"
    output.write_bytes(packed)
    (root / "maps" / "dependencies.json").unlink(missing_ok=True)
    meta = {"revision": REVISION, "builder_sha256": builder_digest(root), "map_sha256": map_digest(output),
            "tests": len(metadata["tests"]), "environment": environment}
    (root / "maps" / "dependencies.meta.json").write_text(json.dumps(meta, indent=1) + "\n")
    print(f"Exported {len(packed)} bytes ({len(raw)} uncompressed) to {output}")


if __name__ == "__main__":
    main()
