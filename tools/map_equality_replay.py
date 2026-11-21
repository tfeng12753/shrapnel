"""Audit unchanged equality outcomes against an existing schedule on the pinned checkout."""
import argparse
import gzip
import hashlib
import json
import sys
from pathlib import Path

from selector import (REVISION, Selector, apply_change, baseline_tree, comparison_changes,
                      comparison_contexts, parse_patch, relation_outcome, split)


def unchanged_tests(record, before, after):
    """Decoded catalog mask with positive evidence from every observed invocation."""
    if (record["op"] != before or before not in ("Eq", "NotEq")
            or after not in ("Eq", "NotEq", "Lt", "LtE", "Gt", "GtE")):
        return 0
    unchanged = 0
    uncertain = record["unknown"]
    for state, users in record["states"].items():
        old, new = relation_outcome(state, before), relation_outcome(state, after)
        if state in ("p:1", "p:9", "p:2", "p:4") and old is not None and old == new:
            unchanged |= users
        else:
            uncertain |= users
    return unchanged & ~uncertain


def unchanged_at_patch(selector, marked, patch):
    changes = parse_patch(patch)
    if len(changes) != 1:
        return 0
    change, = changes
    name = change["old"]
    if name != change["new"] or name not in marked:
        return 0
    source = selector.read_source(name)
    after, removed, inserted = apply_change(source, change)
    if inserted or len(removed) != 1:
        return 0
    line, = removed
    if line not in marked[name]:
        return 0
    edits = comparison_changes(source, after)
    if not edits or len(edits) != 1:
        return 0
    site, old, new = edits[0]
    if site not in marked[name][line]:
        return 0
    start, end, local = comparison_contexts(baseline_tree(source))[site]
    if not local or start != line or end != line:
        return 0
    entry = selector.files[name]
    protected = selector.external | entry["readers"]
    return unchanged_tests(entry["comparisons"][site], old, new) & ~protected


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def index_identity(data, map_path):
    return ["child-work-v1", REVISION, list(sys.version_info[:2]), digest(Path(__file__).resolve().parents[1] / "selector.py"),
            digest(data / "tests.json"), digest(map_path)]


def prepare_child_work(source, data, map_path):
    catalog = json.loads((data / "tests.json").read_text())["tests"]
    selector = Selector(catalog, source, map_path, relations=True)
    if not selector.complete:
        raise ValueError("child-work preparation requires a complete dependency map")
    mask = selector.child_work_mask()
    return {"identity": index_identity(data, map_path), "mask": format(mask, "x"),
            "sources": {name: digest(source / name) for name in selector.sources}}


def checked_child_work(path, selector, data, map_path):
    if path is not None:
        try:
            index = json.loads(path.read_text())
            root = selector.source.resolve()
            if index["identity"] == index_identity(data, map_path):
                mask = int(index["mask"], 16)
                if mask == selector.all:
                    return mask
                sources = {name for name in selector.files if name.endswith(".py")}
                if 0 <= mask < selector.all and sources == index["sources"].keys() and all(
                    (root / name).resolve().is_relative_to(root) and digest(root / name) == expected
                    for name, expected in index["sources"].items()
                ):
                    return mask
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass
    return selector.child_work_mask()


def replay(source, data, map_path, schedule, child_index=None):
    catalog = json.loads((data / "tests.json").read_text())["tests"]
    selector = Selector(catalog, source, map_path, relations=True)
    marked = {}
    if selector.complete:
        opener = gzip.open if map_path.suffix == ".gz" else open
        with opener(map_path, "rt") as stream:
            mapping = json.load(stream)
        if mapping["revision"] == REVISION and mapping.get("complete") is True:
            for name, entry in mapping["files"].items():
                if not name.startswith("sqlglot/") or not name.endswith(".py"):
                    continue
                for site, record in entry.get("comparisons", {}).items():
                    if record.get("unknown_before_dispatch") is True and record.get("op") in ("Eq", "NotEq"):
                        line = int(site.split(":")[0])
                        marked.setdefault(name, {}).setdefault(line, set()).add(site)
    child_work = None
    patches = {p.stem: p for p in (data / "commits").glob("*.patch")}
    for line in schedule.read_text().splitlines():
        row = json.loads(line)
        if marked and row["workers"]:
            try:
                unchanged = unchanged_at_patch(selector, marked, patches[row["commit"]].read_text())
                selected = sum(1 << n for group in row["workers"] for n in group)
                if selected & unchanged:
                    if child_work is None:
                        child_work = checked_child_work(child_index, selector, data, map_path)
                    removed = selected & unchanged & ~child_work
                    if removed:
                        row["workers"] = split(selector.chosen(selected & ~removed))
            except (OSError, ValueError, TypeError, KeyError, SyntaxError, RecursionError):
                pass
        yield row


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("source", "data", "map"):
        parser.add_argument(f"--{option}", required=True, type=Path)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--prepare-child-work", type=Path, metavar="INDEX")
    action.add_argument("--schedule", type=Path)
    parser.add_argument("--child-work-index", type=Path)
    args = parser.parse_args()
    if args.prepare_child_work:
        index = prepare_child_work(args.source, args.data, args.map)
        args.prepare_child_work.write_text(json.dumps(index, separators=(",", ":")))
    else:
        for row in replay(args.source, args.data, args.map, args.schedule, args.child_work_index):
            print(json.dumps(row, separators=(",", ":")))
