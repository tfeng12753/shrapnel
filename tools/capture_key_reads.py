"""Run the dictionary recorder without collecting unrelated comparison evidence."""
import argparse
import ast
from pathlib import Path
import sys


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("recorder", "source", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.recorder.resolve()))
    from build_map import build
    from instrument import Transformer

    Transformer.visit_Compare = ast.NodeTransformer.generic_visit
    build(args.source, args.output.resolve())
