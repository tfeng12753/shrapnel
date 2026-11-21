"""Build optional executor reference files on the unchanged scorer checkout."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def prepare(source, output):
    scripts = Path(__file__).resolve().parent
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as directory:
        prepared = Path(directory) / "prepared"
        prepared.mkdir()
        for seed in (1, 2):
            subprocess.run([sys.executable, "-B", str(scripts / "map_executor_plan.py"), "--source", str(source),
                            "--schema", "TPCDS_SCHEMA", "--fixture", "tpcds", "--output",
                            str(prepared / f"plan-base-{seed}.json"), "--observe-downstream",
                            str(prepared / f"plan-deps-{seed}.json")], check=True,
                           env=dict(os.environ, PYTHONHASHSEED=str(seed), SKIP_INTEGRATION="0",
                                    OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1", OMP_NUM_THREADS="1"))
        subprocess.run([sys.executable, "-B", str(scripts / "map_executor_screen.py"), "--source", str(source),
                        "--schema", "TPCDS_SCHEMA", "--snapshot", str(prepared / "plan-base-1.json"),
                        "--output", str(prepared / "plan-screen.json")], check=True,
                       env=dict(os.environ, PYTHONHASHSEED="1"))
        prepared.rename(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("/opt/sqlglot"))
    parser.add_argument("--output", type=Path, default=Path("/app/executor-plans"))
    args = parser.parse_args()
    if args.output.exists() or args.output.resolve().is_relative_to(args.source.resolve()):
        parser.error("choose a new output directory outside the source checkout")
    prepare(args.source.resolve(), args.output.resolve())
