# Agent 1 progress

## RESUME HERE
- Branch: `agent1/import-safety`.
- Step: Task 1. Building a downstream import check.
- Done: reproduced the base-dialect `DPIPE_IS_STRING_CONCAT` True to False flip. Full suite: 1,020 failing; current selector picks 1,283 and misses 203. 34 test files fail collection with ParseError.
- Root cause: `generators/duckdb.py` parses SQL inside the `DuckDBGenerator` class body (import time). With the patched setting, that parse raises, so importing the DuckDB dialect fails, and `tests/dialects/test_dialect.py` (imported by nearly every dialect test file for `Validator`) fails at collection.
- Plan: build-time index (`tools/import_time.py`) of library lines and uppercase settings executed or read during each module's import, plus each test file's import closure. In the selector (`import_effects.py`), when a patch touches import-time lines or settings, import the candidate test modules in a forked child with the patch overlaid, and add every test of each module that fails to import.
- Local scratch data: `~/work/agent1/dpipe` (patch, selector output, ground truth).

## Alerts

## Branch status
WIP.

## Requests

## Datasets

## Evaluations

## Notes
