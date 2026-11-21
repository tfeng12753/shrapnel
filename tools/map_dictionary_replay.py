"""Verify small scheduled groups for indexed dictionary or comparison edits offline."""
import argparse
import ast
import hashlib
import json
import math
import resource
import time
from pathlib import Path
import xml.etree.ElementTree as ET

from score import WORKER_COST_MILLISECONDS
from selector import REVISION, apply_change, comparison_changes, parse_patch
from tools.import_closure import COMPARISON_OPERATORS
from tools.map_import_replay import changed_line, check_items, replaced_operator, replay as replay_import


def mapped_literal_change(before, after, sites, line):
    """Accept one scalar value replacement in one uniquely keyed dictionary."""
    if str(line) not in sites:
        return False
    old_lines = before.splitlines(keepends=True)
    new_lines = after.splitlines(keepends=True)
    if (len(old_lines) != len(new_lines) or old_lines[:line - 1] != new_lines[:line - 1]
            or old_lines[line:] != new_lines[line:]):
        return False

    old = old_lines[line - 1].encode()
    new = new_lines[line - 1].encode()
    for start, end, literal_type in sites.get(str(line), ()):
        prefix, suffix = old[:start], old[end:]
        if not new.startswith(prefix) or not new.endswith(suffix):
            continue
        token = new[len(prefix):len(new) - len(suffix)]
        try:
            replacement = ast.parse(token.decode(), mode="eval").body
        except SyntaxError:
            continue
        if (isinstance(replacement, ast.Constant) and type(replacement.value).__name__ == literal_type
                and replacement.col_offset == 0 and replacement.end_col_offset == len(token)):
            ast.parse(after)
            return True
    return False


def dictionary_update(source, patch, files, *, comparisons=False):
    changes = parse_patch(patch)
    if len(changes) != 1:
        return {}
    change, = changes
    name = change["old"]
    record = files.get(name)
    if (not record or name != change["new"] or not name.startswith("sqlglot/") or not name.endswith(".py")
            or ".." in Path(name).parts or not (source / name).resolve().is_relative_to(source)):
        return {}
    raw = (source / name).read_bytes()
    if hashlib.sha256(raw).hexdigest() != record["sha256"]:
        return {}
    before = raw.decode("utf-8")
    after, removed, inserted = apply_change(before, change)
    if len(removed) != 1 or inserted:
        return {}
    line, = removed
    if comparisons:
        operators = record.get("comparisons", {})
        if not any(site.startswith(f"{line}:") for site in operators):
            return {}
        offsets = record.get("operator_offsets", {}).get(str(line), ())
        if offsets:
            pair = changed_line(raw, after, line)
            if pair:
                old, new = pair
                for site, start, end in offsets:
                    op = getattr(ast, operators.get(site, ""), None)
                    if COMPARISON_OPERATORS.get(op) == old[start:end] and replaced_operator(old, new, start, end):
                        return {name: after}
        edits = comparison_changes(before, after)
        if not edits or len(edits) != 1 or operators.get(edits[0][0]) != edits[0][1]:
            return {}
    elif not mapped_literal_change(before, after, record["values"], line):
        return {}
    return {name: after}


def cpu_seconds():
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    return time.process_time() + children.ru_utime + children.ru_stime


def replay(args, verified_imports=()):
    cpu_deadline = cpu_seconds() + args.cpu_budget
    wall_deadline = time.monotonic() + 2 * args.cpu_budget
    source = args.source.resolve()
    data = json.loads(args.index.read_text())
    if data.get("revision") != REVISION or data.get("format") != "dictionary-values-v1":
        raise ValueError("requires the pinned dictionary index")
    catalog = {t["number"]: t for t in json.loads((args.data / "tests.json").read_text())["tests"]}
    commits = {p.stem: p for p in (args.data / "commits").glob("*.patch")}
    candidates = []
    for line in args.schedule.read_text().splitlines():
        row = json.loads(line)
        if row["commit"] in verified_imports:
            continue
        workers = [group for group in row["workers"] if group]
        tests = [catalog[n] for group in workers for n in group]
        duration = sum(round(t["seconds"] * 1000) for t in tests)
        if len(workers) != 1 or duration > WORKER_COST_MILLISECONDS:
            continue
        candidates.append((duration, row["commit"], sorted(t["id"] for t in tests)))
    attempted, verified, saved = [], [], 0
    last_cpu = last_wall = 0.0
    # Stop spending on comparison probes after one fails to verify its group.
    # Dictionary verification keeps its existing admission rules and budget.
    try_comparisons = any(f.get("comparisons") for f in data["files"].values())
    for duration, commit, items in sorted(candidates):
        if cpu_deadline - cpu_seconds() <= last_cpu or wall_deadline - time.monotonic() <= last_wall:
            break
        try:
            patch = commits[commit].read_text()
            updates = dictionary_update(source, patch, data["files"])
            comparison_probe = not updates and try_comparisons
            if comparison_probe:
                updates = dictionary_update(source, patch, data["files"], comparisons=True)
            remaining = min(2 * (cpu_deadline - cpu_seconds()), wall_deadline - time.monotonic())
            if not updates or remaining <= 0:
                continue
            attempted.append(commit)
            if comparison_probe:
                try_comparisons = False
            started_cpu, started_wall = cpu_seconds(), time.monotonic()
            if check_items(source, updates, items, wall_seconds=remaining):
                verified.append(commit)
                saved += WORKER_COST_MILLISECONDS + duration
                if comparison_probe:
                    try_comparisons = True
            last_cpu = cpu_seconds() - started_cpu
            last_wall = time.monotonic() - started_wall
        except (OSError, ValueError, KeyError, TypeError, SyntaxError, ET.ParseError, RecursionError):
            continue
    return {"attempted": attempted, "verified_passes": verified, "removable_simulated_ms": saved}


def combined_replay(args):
    if not math.isfinite(args.cpu_budget) or args.cpu_budget < 0:
        raise ValueError("CPU budget must be finite and nonnegative")
    if args.import_closure is None:
        return replay(args)
    started = cpu_seconds()
    imports = replay_import(args)
    elapsed = cpu_seconds() - started
    # A recently measured real pytest process is a local cost estimate.
    # Skip additional speculative work when similar startup exceeds our window.
    passes = len(imports["verified_passes"])
    if passes and elapsed / passes >= args.cpu_budget:
        dictionaries = {"attempted": [], "verified_passes": [], "removable_simulated_ms": 0}
    else:
        dictionaries = replay(args, imports["verified_passes"])
    return {"imports": imports, "dictionaries": dictionaries}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("source", "data", "index", "schedule"):
        parser.add_argument(f"--{option}", required=True, type=Path)
    parser.add_argument("--import-closure", type=Path,
                        help="Run the import audit first and use its measured time to screen extra checks")
    parser.add_argument("--cpu-budget", type=float, default=WORKER_COST_MILLISECONDS / 1000,
                        help="Local CPU allowance for starting checks, with twice that allowance in wall time")
    print(json.dumps(combined_replay(parser.parse_args())))
