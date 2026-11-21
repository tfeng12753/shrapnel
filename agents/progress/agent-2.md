# Agent 2 progress

## RESUME HERE
- Branch: `agent2/map-data` (from `main` 0871e19, merged with
  `overnight/map-precision` bfd8aaa). Mirrored to `claude/bold-ritchie-0vfm3b`.
- Step: Task 1, dictionary key reads at consuming sites. Builder side is done
  (`tools/instrument.py`, `tools/build_map.py`, tests in
  `tests/test_map_dictionary_aliases.py`). Next is the selector side
  (`dict_keys.py` behind `dict_key_refinement`, default off), then a map build
  and the two acceptance controls.
- Sandbox setup (fresh session): `python3 -m venv /opt/venv && /opt/venv/bin/pip
  install -r requirements.lock coverage==7.10.7`; pinned checkout by
  `git clone --filter=blob:none` of the pinned repository, `git checkout
  eb11aec4be8cb2122afe713a4816662c3f0fb2fa`, then `git archive HEAD | tar -x -C
  /opt/sqlglot` (the tarball host is blocked here; clone works).
- Map build: `MAP_TIMEOUT=600 PYTHONPATH=/opt/sqlglot /opt/venv/bin/python
  tools/build_map.py --source /opt/sqlglot --output /opt/work/<name>.json.gz >
  /opt/work/<name>.log 2>&1` (about 12 minutes on 4 cores).
- Unit tests: `/opt/venv/bin/python -m unittest discover -s tests` (only the
  stale cached-map fingerprint fails until the map is rebuilt).

## Alerts

## Branch status
WIP. Builder changes only; no selector behaviour changes yet, so the committed
map is stale (fingerprint test fails until Task 4).

## Requests

## Datasets

## Evaluations
- Baseline: `main` @ 3345ab6, project tests 127 passed.

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

### Earlier map-precision findings carried over
- The dictionary/tuple read diagnostics and import, equality and executor
  replays from the earlier branch are merged as tools; none are enabled in the
  selector.
- Known limits of read tracking: native consumers, reflection
  (`vars`/`__dict__`), child processes and values cached by one test and reused
  by others are not observed. Rules here fall back to whole reads wherever a
  use is not a recognised keyed read or transfer.
