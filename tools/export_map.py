"""Copy a completed build's map out of Docker, with guaranteed container cleanup."""
import argparse
import gzip
import json
from pathlib import Path
import subprocess
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument("--image", default="blast-radius-entry")
args = parser.parse_args()
container = subprocess.check_output(["docker", "create", args.image], text=True).strip()
try:
    with tempfile.TemporaryDirectory() as temporary:
        compressed = Path(temporary) / "map.gz"
        subprocess.run(["docker", "cp", container + ":/app/dependencies.json.gz", str(compressed)], check=True)
        raw = gzip.decompress(compressed.read_bytes())
        metadata = json.loads(raw)
        if not metadata.get("complete") or metadata.get("revision") != "eb11aec4be8cb2122afe713a4816662c3f0fb2fa":
            raise SystemExit("refusing incomplete/wrong-revision map")
        if len(raw) > 18_000_000:
            raise SystemExit("map too large for repository allowance; keep build-time map generation")
        output = Path(__file__).resolve().parents[1] / "maps/dependencies.json"
        output.write_bytes(raw)
        print(f"Exported {len(raw)} bytes to {output}; review and commit it.")
finally:
    subprocess.run(["docker", "rm", container], check=True, stdout=subprocess.DEVNULL)
