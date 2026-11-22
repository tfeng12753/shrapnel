"""Convert baseline read observations from each shard into a test-ID index."""

import argparse
import json
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP))
from key_evidence import table_id
from selector import Selector


def pack(catalog, source, dependencies, logs):
    ids = {t["id"] for t in catalog}
    passed, uncertain, facts = set(), set(), {}
    for path in logs:
        for row in path.read_text().splitlines():
            item, event, payload = json.loads(row)
            if event == "status":
                name, status = payload
                (passed if status == "passed" else uncertain).add(name)
            elif event == "uncertain":
                uncertain.add(payload)
            elif event == "read":
                signature, key = payload
                facts.setdefault(table_id(signature), {}).setdefault(key, set()).add(
                    item
                )
    if not ids <= passed | uncertain or not facts:
        raise ValueError("incomplete baseline observation")
    selector = Selector(catalog, source, dependencies)
    if not selector.complete:
        raise ValueError("incomplete dependency map")
    children = [t["id"] for t in selector.chosen(selector.child_work_mask())]
    return {
        "complete": True,
        "catalog": sorted(ids),
        "unknown": sorted((uncertain & ids) | (ids - passed)),
        "children": children,
        "reads": {
            k: {j: sorted(v) for j, v in row.items()} for k, row in facts.items()
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for arg in ["catalog", "source", "map", "output"]:
        parser.add_argument(arg, type=Path)
    parser.add_argument("logs", nargs="+", type=Path)
    args = parser.parse_args()
    data = pack(
        json.loads(args.catalog.read_text())["tests"], args.source, args.map, args.logs
    )
    args.output.write_text(json.dumps(data, separators=(",", ":")) + "\n")
