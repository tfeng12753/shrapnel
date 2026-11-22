"""Regenerate token evidence using the full original pinned parser workload."""

import argparse
import inspect
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from selector import REVISION
from token_vocabulary import FORMAT, TEST, FLAG, TOKEN, fingerprint, verify_gate


def record(root):
    if (root / ".pinned-revision").read_text().strip() != REVISION:
        raise ValueError("revision mismatch")
    before = fingerprint(root)
    gate = verify_gate(root)
    sys.path.insert(0, str(root))
    from sqlglot.parser import Parser
    from tests.test_parser import TestParser

    if not Path(inspect.getfile(Parser)).resolve().is_relative_to(root.resolve()):
        raise ValueError("parser outside pinned checkout")
    if not Path(inspect.getfile(TestParser)).resolve().is_relative_to(root.resolve()):
        raise ValueError("test outside pinned checkout")
    tokens = set()
    advances = 0
    original_advance, original_parse = Parser._advance, Parser._parse
    signature = inspect.signature(original_parse)

    def observe(token):
        name = getattr(getattr(token, "token_type", None), "name", None)
        if name is not None:
            tokens.add(name)

    def parse(self, *args, **kwargs):
        bound = signature.bind(self, *args, **kwargs)
        for token in bound.arguments["raw_tokens"]:
            observe(token)
        return original_parse(self, *args, **kwargs)

    def advance(self, *args, **kwargs):
        nonlocal advances
        original_advance(self, *args, **kwargs)
        advances += 1
        for token in (self._curr, self._next, self._prev):
            observe(token)

    try:
        Parser._advance, Parser._parse = advance, parse
        result = unittest.TestResult()
        TestParser("test_max_nodes").run(result)
    finally:
        Parser._advance, Parser._parse = original_advance, original_parse
    if (result.testsRun != 1 or not result.wasSuccessful() or result.skipped
            or result.expectedFailures or not tokens or not advances):
        raise RuntimeError(f"capture failed: {result.errors!r} {result.failures!r}")
    if fingerprint(root) != before:
        raise ValueError("inputs changed during capture")
    return {"format": FORMAT, "revision": REVISION, "fingerprint": before, "test": TEST,
            "flag": FLAG, "required_token": TOKEN, "gate": gate,
            "capture": "raw-and-current-v1", "passed": True,
            "advances": advances, "tokens": sorted(tokens)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("/opt/sqlglot"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence = record(args.source)
    args.output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, sort_keys=True))
