"""Check just the modified queries of a pinned executor fixture in a bounded child."""
from pathlib import Path
import sys
from native_probes import _forked


def passed(source, item, expected, actual):
    if len(expected) != len(actual) or not expected:
        return False
    changed = [i for i, pair in enumerate(zip(expected, actual)) if pair[0] != pair[1]]
    if not 1 <= len(changed) <= 3 or any(x[0] != y[0] for x, y in zip(expected, actual)):
        return False

    def check():
        import os
        os.chdir(source)
        sys.path.insert(0, str(source))
        from sqlglot import parse_one
        # Entries without the execute flag are parsed before the worker skips them.
        if all(not dict(actual[i][0]).get("execute") for i in changed):
            for i in changed:
                parse_one(actual[i][1])
            return True

        from tests import test_executor as suite
        if Path(suite.__file__).resolve() != source / "tests/test_executor.py":
            return False
        tpch = item.endswith("::test_execute_tpch")
        case = suite.TestExecutor(item.split("::")[-1])
        cls = type(case)
        cls.setUpClass()
        try:
            rows = cls.tpch_sqls if tpch else cls.tpcds_sqls
            if len(rows) != len(expected):
                return False
            suite.initializer(suite.TPCH_SCHEMA if tpch else suite.TPCDS_SCHEMA,
                              cls.tpch_tables if tpch else cls.tpcds_tables)
            for i in changed:
                meta, original, reference = rows[i]
                if (tuple(meta.items()), original) != expected[i]:
                    return False
                sql = actual[i][1]
                rows[i] = (meta, sql, reference)
                table = suite.mp_execute(suite.parse_one(sql), meta)
                if table is not None:
                    case.subtestHelper(i, table, tpch)
            return True
        finally:
            cls.tearDownClass()

    ok, _ = _forked(check, False, wall_seconds=12, cpu_seconds=4, strict_cpu=True)
    return ok is True
