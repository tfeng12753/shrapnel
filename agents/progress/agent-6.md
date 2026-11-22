# Inherited TypedDict experiment

Branch: `codex/agent-6-selection`. Control: `main` at `1ea65b5a2b4d7b9a787107752a75966c33fd84af`.
Starting revision: `a3ef76ca215c1a1036cf343b7935d0a09c114811`.

## Finding and implementation

The existing `typeddict_total_flip` checks the textual base scaffold for
`TypedDict`. Inherited declarations therefore return `structural` before
the existing metadata-reader and successful-import checks can narrow them.

`inherited_typeddict` is a separate default-off configuration key and CLI
toggle. It also requires the existing `typeddict_state_check` option.
Owner-specific logic lives in `typeddict_selection.py`; shared edits are
limited to the option, its call site, configuration reproduction and image
packaging. No dependency-map collection changes were made.

The prototype resolves unique imports, local aliases, indirect bases and
multiple bases in declaration order. It requires identical whole-file ASTs
after restoring boolean `total` values. Unknown bases, decorators, rebinding,
conditional declarations, changed inheritance and mixed substantive edits
fall back. It preserves source readers, external selection, metadata readers,
failed imports and independent selection reasons on mixed-file patches.

Reflection can consume metadata without spelling the existing scanner's
metadata names. The inherited path therefore also rejects unresolved
reflection, including aliases of reflection builtins. This guard is
deliberately broad. All three inherited declarations in the pinned file
pass ancestry proof but encounter this guard. There is no demonstrated safe
narrowing gain in the pinned checkout; keep the toggle disabled.

## Validation

- Linux regression suite: 260 tests passed, including the existing
  import-order regression and configuration round trips.
- Focused cases cover aliases, indirect and multiple bases, changed
  inheritance, required/optional overrides, mixed edits, source readers,
  direct and dynamic metadata consumers, and failed import checks.
- Five fresh failure-bearing metadata controls execute both declarations
  and verify changed total/required/optional values, including descendants.
  Their consumer tests remain selected. These are focused synthetic checks,
  not a replacement for fresh full-suite held-out mutants.
- Container measurement runner: `tools/measure_inherited_typeddict.py`.
  It compares a supplied control checkout with this branch's toggle off and
  on, using identical sample patches and held-out mutants, three repetitions,
  the existing worker packer and the 300-second per-missed-patch penalty.
  Results for implementation revision `bdf2927` are recorded in
  [agent-6-measurements.json](agent-6-measurements.json), including exact
  configurations and code fingerprints.

## Container smoke measurements

Each row is three repetitions of the same batch under two CPUs, 4096 MiB,
256 processes, no network and a read-only container. Correctness counts each
distinct patch once. The sample batch contains 30 patches. Only one held-out
mutant has usable ground truth; nineteen held-out results are missing.

| Batch | Configuration | Missed patches | Simulated seconds | Median CPU seconds | Median total seconds | Worst total seconds |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Sample | `main` | 0 | 116.075 | 4.042 | 120.117 | 120.437 |
| Sample | `bdf2927`, toggle off | 0 | 116.075 | 3.077 | 119.152 | 119.652 |
| Sample | `bdf2927`, toggle on | 0 | 116.075 | 2.908 | 118.983 | 119.021 |
| Held-out | `main` | 0 | 4.988 | 0.673 | 5.661 | 5.722 |
| Held-out | `bdf2927`, toggle off | 0 | 4.988 | 0.604 | 5.592 | 5.631 |
| Held-out | `bdf2927`, toggle on | 0 | 4.988 | 0.650 | 5.638 | 5.665 |

There are no missed failing test IDs; the fraction of these two batches with
a miss is zero for every configuration. The 300-second per-missed-patch
penalty is included and contributes zero. These small, sequentially timed
batches do not establish a CPU improvement. No schedule improvement was
observed, and no fresh batches of roughly 45 patches were measured.

Final selector CPU across all repetitions was 35.332 seconds. Preliminary
runs consumed 28.952 seconds and were discarded because the guard changed
during measurement. One failed input-permission attempt consumed 0.308
seconds. Total selector CPU including those attempts was 64.593 seconds.

## Resume

The next useful step is to establish which reflection consumers can reach these declarations,
with complete evidence for aliases and descendants. Do not weaken the guard
based on sample or mutant outcomes. Larger fresh batches and full-suite
failure-bearing controls remain prerequisites for promotion. Subsequent
experiments in the kickoff have not started. Integration remains separate.
