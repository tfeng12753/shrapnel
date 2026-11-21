# Selector and verification track

The branch starts at `f08f491` on `main`. Submit changes only through `overnight/selector-verification` and a draft PR. Preserve the shipped configuration, leave map generation to the other track, and use even-numbered mutants only.

- The pinned sqlglot source (`eb11aec4be8cb2122afe713a4816662c3f0fb2fa`), generated import closure, and Python 3.13.5 environment with all 43 locked packages are in `/workspace/selector-bench` in this workspace.
- The [ground-truth run](https://github.com/tfeng12753/shrapnel/actions/runs/44888276378) is in progress. No artifact was available at kickoff.
- Next: implement whole-selection verification with deterministic budget admission and conservative timeout handling, inspect the map for boolean/literal evidence, harden relations, and run five public replays per configuration. Record exact configurations, map fingerprint, code revision, schedule costs, misses and CPU here as measurements finish.
