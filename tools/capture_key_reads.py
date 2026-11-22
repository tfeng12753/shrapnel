"""Run the dictionary recorder without collecting unrelated comparison evidence."""
import argparse
import ast
import gzip
import json
from pathlib import Path
import sys


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("recorder", "source", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--retain-item", action="append", default=[],
                        help="Preserve an expensive item's baseline dependencies without instrumenting its call")
    args = parser.parse_args()
    sys.path.insert(0, str(args.recorder.resolve()))
    from build_map import build
    from instrument import Transformer

    Transformer.visit_Compare = ast.NodeTransformer.generic_visit
    Transformer.visit_Attribute = ast.NodeTransformer.generic_visit
    Transformer.visit_Call = ast.NodeTransformer.generic_visit
    output = args.output.resolve()
    if args.retain_item:
        import pytest

        original_main = pytest.main
        retain = set(args.retain_item)

        class RetainItems:
            def pytest_collection_modifyitems(self, items):
                for item in items:
                    if item.nodeid in retain:
                        item.add_marker(pytest.mark.skip(reason="retained in every dictionary observation"))

        def instrumented_main(argv, *, plugins):
            return original_main(argv, plugins=[*plugins, RetainItems()])

        pytest.main = instrumented_main
    build(args.source, output)
    if args.retain_item:
        with gzip.open(output, "rt") as stream:
            data = json.load(stream)
        missing = retain - set(data["tests"])
        if missing:
            raise ValueError(f"retained items absent from catalog: {sorted(missing)}")
        data["retained_items"] = sorted(retain)
        output.write_bytes(gzip.compress(json.dumps(data, separators=(",", ":")).encode(), mtime=0))
