# Agent 2 progress

## RESUME HERE
- Branch: `agent2/map-data` (from `main` 0871e19 + `overnight/map-precision`).
  Mirrored to `claude/bold-ritchie-0vfm3b`. Last pushed: see `git log -1`.
- Done: Task 1 builder + `dict_keys.py` (flag off); Task 2 `equality` record;
  Task 3 experiment (miss confirmed) and builder fix (writer attribution).
- Running/next: map build with the current builder
  (`/opt/work/v2.json.gz`, log `/opt/work/v2.log`). Then:
  1. run the selector on the controls with that map, with and without
     `--dict-key-refinement`, and check against ground truth
     (`/opt/work/controls/{commits,results}`; recreate from the Datasets list);
  2. check `cold-name` is now fully selected;
  3. export the map to `maps/` (Task 4) and rerun unit tests;
  4. sample patches: misses, simulated seconds and CPU against `main`.
- Sandbox setup (fresh session): `python3 -m venv /opt/venv && /opt/venv/bin/pip
  install -r requirements.lock coverage==7.10.7`; pinned checkout by
  `git clone --filter=blob:none` of the pinned repository, `git checkout
  eb11aec4be8cb2122afe713a4816662c3f0fb2fa`, then `git archive HEAD | tar -x -C
  /opt/sqlglot` (the tarball host is blocked here; clone works).
- Map build: `MAP_TIMEOUT=900 PYTHONPATH=/opt/sqlglot /opt/venv/bin/python
  tools/build_map.py --source /opt/sqlglot --output /opt/work/<name>.json.gz >
  /opt/work/<name>.log 2>&1` (25+ minutes on 4 cores with the new observers).
- Ground truth for one patch (about 2 minutes):
  `/opt/venv/bin/python tools/ground_truth.py <patch> <out.json> /opt/sqlglot sample/tests.json`.
- Unit tests: `/opt/venv/bin/python -m unittest discover -s tests` (only the
  stale cached-map fingerprint fails until the map is rebuilt).

## Alerts

## Branch status
WIP. Builder changes only; no selector behaviour changes yet, so the committed
map is stale (fingerprint test fails until Task 4).

## Requests
- to agent 1: add `dict_keys.py` to the selector `COPY` line in `Dockerfile`
  (it is imported only when `dict_key_refinement` is on, so nothing breaks
  while it is off).
- to agent 3: Task 3 result. Clearing the dialect registry (as the entry-point
  tests do in setUp/tearDown) makes later tests take the registry's
  already-imported path. A mutation there (`cold-name` below) fails 133 tests
  while `main` selects 17 (124 misses). The map now credits registry-writing
  lines to every registry reader. Verifiers that run tests in a fresh process
  see a warm registry and can pass where the full suite fails: please clear the
  registry (or run the entry-point tests first) in any verifier that could
  drop a test for an edit in registry-loading code, or exclude such edits.
- to agent 3: the new `equality` map record is ready to consume (format in
  Notes). It does not change the existing `comparisons` record.

## Datasets
- controls (local `/opt/work/controls`, catalog `sample/tests.json`): single
  edits to the pinned checkout, ground truth by `tools/ground_truth.py`.
  | Name | Edit | Failing |
  | --- | --- | ---: |
  | snow-u / snow-x / snow-0 | `dialects/snowflake.py` tokenizer `NUMERIC_ESCAPES` value of key u / x / 0 | 0 |
  | bq-escape-x / bq-escape-U | `dialects/bigquery.py` tokenizer `NUMERIC_ESCAPES` value of key x / U | 1 / 0 |
  | bq-safe-offset | `parsers/bigquery.py` `BRACKET_OFFSETS["SAFE_OFFSET"]` (0, True) to (0, False) | 2 (catalog 84, 1333) |
  | cold-name | `dialects/dialect.py` `_try_load`: `attr.__name__.lower() == key` to `!=` | 133 |
  | cold-break | same loop: `break` to `continue` | 0 |

## Evaluations
- Baseline: `main` @ 3345ab6, project tests 127 passed.
- Controls, `main` selector + committed map (default config): snow-* 274
  tests (10.524 catalog s); bq-escape-* 223 (12.284 s), 0 missed;
  bq-safe-offset 10, 0 missed; cold-name 17, **124 missed**.

## Notes

### Map format: `dictionary_reads` (per file, per tracked dict symbol)
Tracked symbols are module- or class-level names assigned a dict literal.
Masks are hex test bitmaps over `tests`.

    "dictionary_reads": {"Tokenizer.NUMERIC_ESCAPES": {
        "start": 133, "end": 133,          # literal's line span
        "keys": {"x": "<mask>", ...},      # tests that read that key's value
        "whole": "<mask>",                 # tests that read every value
        "presence": {"x": "<mask>", "": "<mask>"},  # membership (key set only; "" = non-literal key)
        "lines": {"133": "<mask>"},        # raw coverage of the literal's lines
        "transferred": "<mask>",           # tests that only moved the object to an alias
        "unobserved": "<mask>"}}           # symbol users with no observed read or transfer

Recording rules (builder):
- Object identity is bound when the literal is evaluated; reads are checked by
  identity, so inherited and aliased copies of the same object count.
- Keyed reads: `d[k]`, `d.get(k[, default])`, `dict.get/__getitem__(d, k)`.
  Non-literal or non-string keys count as whole reads.
- Membership (`k in d`) is recorded under `presence` only.
- Transfers (not reads): single-target assignment to a name/attribute, or a
  keyword argument whose name is a parameter of the called library function or
  class `__init__`. The target name becomes an alias name, and every load of an
  alias name is instrumented.
- Everything else is a whole read: iteration, `items()/values()/copy()`, `len`,
  truthiness, positional arguments, containers, returns, `**d`,
  `copy.copy/deepcopy`, `getattr` results.
- Context: reads while any library module executes, or inside
  `__init_subclass__` or a metaclass `__new__`, count for every test. setUp and
  call-phase reads count for the current test; setUpClass/setUpModule reads
  count for the class/file. Other setup-phase reads and other threads count
  for every test.

Selector rule (planned, `dict_keys.py`): for an edit that only changes literal
values of existing string keys in one tracked literal (same key sequence, no
other change in the file), select `keys[k]` for each changed key, plus
`whole`, `unobserved` and `lines` of the edited lines. `presence` is ignored
because the key set is unchanged.

### Map format: `equality` (per file, per site, for agent 3)
Every single-line `==`/`!=` with one comparator in an ordinary function body
(not decorated, not a dunder method) is observed. Site key is
`"<line>:<col>"` of the comparison node.

    "equality": {"1216:11": {
        "op": "Eq",                       # original operator, Eq or NotEq
        "reached": "<mask>",              # tests whose evaluation reached the site (marked before operands)
        "states": {"p:1": "<mask>", ...}, # completed comparisons by relation
        "unknown": "<mask>"}}             # see below

- States: `p:1` equal, `p:2` left < right, `p:4` left > right (exact
  int/str/bool operands, ordering defined); `e:9` both None; `e:0` unequal
  with no ordering (None against int/str/bool, or str against int/bool).
- `unknown` = operands of any other type (recorded before the operator runs,
  so a raising custom `__eq__` is still unknown), plus reached-but-not-completed
  (an operand raised), plus tests covering the line with no observation.
- A test can appear in several states (several evaluations); keep it if any
  of its states changes outcome under the new operator, or if it is unknown.
- A site evaluated outside a test's call phase (import, setup, threads) has
  no record at all: treat a missing site as fully unknown (use line coverage).
- The established `comparisons` record is unchanged (same sites, same
  semantics), so current selector behaviour does not change with this map.

### Registry writers (Task 3 fix)
A test that stores into a tracked mapping (`d[k] = v`, `del d[k]`,
`clear/update/pop/popitem/setdefault`) records the functions in the mapping's
own module on its stack. Their executed lines are credited to every reader of
the mapping, because later tests read the stored state without running the
store. Callers in other modules are not credited: they rerun in each later test.

### Decisions
- Scalar settings: readers already come from MRO-resolved attribute reads.
  The only bulk copy is the tokenizer core construction, and the core then
  hoists each setting into a local at the start of every string scan, so a
  provenance link would narrow "constructs a tokenizer" only to "scans a
  string". Not implemented; `dict_keys.py` refines dictionary values only.

### Earlier map-precision findings carried over
- The dictionary/tuple read diagnostics and import, equality and executor
  replays from the earlier branch are merged as tools; none are enabled in the
  selector.
- Known limits of read tracking: native consumers, reflection
  (`vars`/`__dict__`), child processes and values cached by one test and reused
  by others are not observed. Rules here fall back to whole reads wherever a
  use is not a recognised keyed read or transfer.
