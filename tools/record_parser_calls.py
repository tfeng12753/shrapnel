"""Run inside the build image to regenerate /app/parser_calls.json.gz."""

import gzip
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, "/opt/sqlglot")
from sqlglot import errors, parser
from tests.test_parser import TestParser

original = errors.highlight_sql
records = []


def capture(*args, **kwargs):
    result = original(*args, **kwargs)
    records.append([args, kwargs, list(result)])
    return result


if __name__ == "__main__":
    with (
        patch.object(errors, "highlight_sql", capture),
        patch.object(parser, "highlight_sql", capture),
    ):
        result = unittest.TestResult()
        TestParser("test_max_nodes").run(result)
    if not result.wasSuccessful() or not records:
        raise RuntimeError(f"Recording failed: {result.errors} {result.failures}")
    Path("/app/parser_calls.json.gz").write_bytes(
        gzip.compress(json.dumps(records).encode(), mtime=0)
    )
