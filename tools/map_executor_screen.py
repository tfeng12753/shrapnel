"""Use small baseline queries to reject changed optimizer output before plan replay."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import sys

if __package__:
    from .map_executor_plan import digest
else:
    from map_executor_plan import digest


def screen_matches(screen, module, line, optimize_query):
    indices = screen["lines"].get(module, {}).get(str(line), [])
    for index in indices:
        sql, expected = screen["queries"][index]
        if optimize_query(sql) != expected:
            return False
    return True


def main(args):
    source = args.source.resolve()
    sys.path.insert(0, str(source))
    from sqlglot import parse_one
    from sqlglot.optimizer import optimize
    from tests import helpers

    schema = getattr(helpers, args.schema)

    def optimize_query(sql):
        expr = optimize(parse_one(sql), schema, leave_tables_isolated=True)
        return digest(expr.dump())

    if args.reference:
        screen = json.loads(args.reference.read_text())
        return 0 if screen_matches(screen, args.module, args.line, optimize_query) else 1

    queries = []
    candidates = defaultdict(lambda: defaultdict(list))
    hits = set()

    def line_event(frame, event, arg):
        if event == "line":
            hits.add((frame.f_globals["__file__"], frame.f_lineno))
        return line_event

    def trace(frame, event, arg):
        if event == "call" and frame.f_globals.get("__name__", "").startswith("sqlglot.optimizer."):
            return line_event
        return None

    previous_trace = sys.gettrace()
    rows = json.loads(args.snapshot.read_text())["queries"]
    for sql in sorted({sql for _, sql, plans, _ in rows if plans}, key=lambda text: (len(text), text)):
        hits.clear()
        sys.settrace(trace)
        try:
            expected = optimize_query(sql)
        finally:
            sys.settrace(previous_trace)
        for filename, line in hits:
            name = Path(filename).relative_to(source).as_posix()
            indices = candidates[name][line]
            if len(indices) < 3:
                indices.append(len(queries))
        queries.append([sql, expected])
    args.output.write_text(json.dumps({"lines": candidates, "queries": queries}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--schema", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--snapshot", type=Path)
    mode.add_argument("--reference", type=Path)
    parser.add_argument("--module")
    parser.add_argument("--line", type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.snapshot and not args.output:
        parser.error("--snapshot requires --output")
    if args.reference and (not args.module or args.line is None or args.line < 1):
        parser.error("--reference requires --module and a positive --line")
    sys.exit(main(args))
