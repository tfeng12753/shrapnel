"""Try a smaller version of the parser limit test on eligible source edits."""
import ast
from functools import lru_cache


TEST_FILE = "tests/test_parser.py"
ITEM = TEST_FILE + "::TestParser::test_max_nodes"


@lru_cache(maxsize=2)
def shortened_test(text):
    cls, = (node for node in ast.parse(text).body if isinstance(node, ast.ClassDef) and node.name == "TestParser")
    method, = (node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "test_max_nodes")
    lines = text.splitlines(keepends=True)
    start, end = method.lineno - 1, method.end_lineno
    body = "".join(lines[start:end])
    for old, new in (("width = 10000", "width = 400"), ("max_nodes=1_000_000", "max_nodes=40_000"),
                     ("max_nodes=50_000", "max_nodes=2_000"), ("(50000)", "(2000)")):
        if old not in body:
            raise ValueError("parser test no longer matches the proxy")
        body = body.replace(old, new)
    return "".join(lines[:start]) + body + "".join(lines[end:])
