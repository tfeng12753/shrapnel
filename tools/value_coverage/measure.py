import os, sys, subprocess, json
from pathlib import Path

idx = int(sys.argv[1])
catalog = json.loads(Path(sys.argv[2]).read_text())["tests"]
source = Path(sys.argv[3]).resolve()
folder = Path(sys.argv[4]).resolve()
folder.mkdir(parents=True, exist_ok=True)
plugin = Path(__file__).resolve().parent
from collections import defaultdict

groups = defaultdict(list)
for t in catalog:
    groups[t["file"]].append(t)
buckets = [[] for _ in range(8)]
loads = [0] * 8
for path, tests in sorted(
    groups.items(), key=lambda pair: -sum(t["seconds"] for t in pair[1])
):
    i = loads.index(min(loads))
    buckets[i].append(path)
    loads[i] += sum(t["seconds"] for t in tests)
log = folder / f"events-{idx}.jsonl"
log.write_text("")
env = {
    **os.environ,
    "BR_VALUE_LOG": str(log),
    "PYTHONPATH": os.pathsep.join([str(plugin), str(source)]),
    "PYTHONDONTWRITEBYTECODE": "1",
}
result = subprocess.run(
    [
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "keyplugin",
        "-p",
        "no:cacheprovider",
        "--tb=no",
        "-q",
        *buckets[idx],
    ],
    cwd=source,
    env=env,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    timeout=165,
)
print(idx, result.returncode, result.stdout.decode()[-480:])
sys.exit(result.returncode)
