"""Local container benchmark wrapper; overhead is included in its CPU estimate."""
import json
import resource
import subprocess
import sys

process = subprocess.run([sys.executable, "/app/selector.py", *sys.argv[1:]])
parent = resource.getrusage(resource.RUSAGE_SELF)
children = resource.getrusage(resource.RUSAGE_CHILDREN)
print("BR_TIMING " + json.dumps({"cpu_seconds": parent.ru_utime + parent.ru_stime + children.ru_utime + children.ru_stime}), file=sys.stderr)
raise SystemExit(process.returncode)
